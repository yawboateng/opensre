"""Boot-time GKE auto-registration: opt-in, bounded, and never destructive."""

from __future__ import annotations

import logging
from typing import Any

import pytest

from config.constants.gcp import GCP_AUTO_REGISTER_GKE_ENV
from config.constants.kubernetes import KUBERNETES_INSTANCES_ENV
from integrations.gcp.gke import autoregister
from integrations.gcp.gke.registration import (
    ClusterRegistration,
    Outcome,
    RegistrationReport,
)


class _Recorder:
    """Stand in for ``register_gke_clusters`` and remember how it was called."""

    def __init__(self, report: RegistrationReport | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._report = report or RegistrationReport()

    def __call__(self, **kwargs: Any) -> RegistrationReport:
        self.calls.append(kwargs)
        return self._report


def _resolved() -> dict[str, Any]:
    return {}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(GCP_AUTO_REGISTER_GKE_ENV, raising=False)
    monkeypatch.delenv(KUBERNETES_INSTANCES_ENV, raising=False)
    # The once-per-process guard is module state, so without this the first test
    # to start a thread would silently suppress every later one.
    monkeypatch.setattr(autoregister, "_started_thread", None)


@pytest.fixture
def _ready(monkeypatch: pytest.MonkeyPatch) -> _Recorder:
    """Everything auto-registration depends on, stubbed to succeed."""
    recorder = _Recorder()
    monkeypatch.setattr(autoregister, "plugin_installed", lambda: True)
    monkeypatch.setattr(autoregister, "resolve_local_classified_integrations", _resolved)
    monkeypatch.setattr(autoregister, "register_gke_clusters", recorder)
    return recorder


# --- opt-in --------------------------------------------------------------------


def test_unset_means_off() -> None:
    """The default must be off: registering widens what the agent can reach."""
    assert autoregister.requested_projects() is None


@pytest.mark.parametrize("value", ["false", "FALSE", "0", "no", "off", "", "   "])
def test_falsey_values_are_off(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv(GCP_AUTO_REGISTER_GKE_ENV, value)

    assert autoregister.requested_projects() is None


@pytest.mark.parametrize("value", ["true", "TRUE", "1", "yes", "on"])
def test_truthy_values_mean_every_configured_project(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """``*`` is the shared grammar's wildcard, and it spans *configured* projects
    only — ``resolve_projects`` rejects anything else, so opting in cannot reach
    an estate nobody configured."""
    monkeypatch.setenv(GCP_AUTO_REGISTER_GKE_ENV, value)

    assert autoregister.requested_projects() == "*"


def test_a_project_list_is_passed_through_verbatim(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(GCP_AUTO_REGISTER_GKE_ENV, "proj-a,proj-b")

    assert autoregister.requested_projects() == "proj-a,proj-b"


def test_nothing_starts_when_opted_out(monkeypatch: pytest.MonkeyPatch) -> None:
    def _explode() -> bool:
        raise AssertionError("must not touch the plugin when opted out")

    monkeypatch.setattr(autoregister, "plugin_installed", _explode)

    assert autoregister.start_gke_autoregistration(logging.getLogger(__name__)) is None


# --- the precedence guard ------------------------------------------------------


def test_kubernetes_instances_env_stops_registration_entirely(
    monkeypatch: pytest.MonkeyPatch, _ready: _Recorder, caplog: pytest.LogCaptureFixture
) -> None:
    """Explicit operator config must survive.

    ``merge_local_integrations`` lets the store override the environment for a
    *whole service*, not per instance. Registering even one discovered cluster
    would therefore replace the entire ``KUBERNETES_INSTANCES`` record and
    silently drop every cluster the operator declared. Standing down is the only
    non-destructive answer.
    """
    monkeypatch.setenv(KUBERNETES_INSTANCES_ENV, '[{"name":"eks-prod","kubeconfig":"..."}]')

    with caplog.at_level(logging.INFO):
        autoregister.register_now(logging.getLogger(__name__), "*")

    assert _ready.calls == []
    assert KUBERNETES_INSTANCES_ENV in caplog.text


def test_an_empty_kubernetes_instances_env_does_not_block(
    monkeypatch: pytest.MonkeyPatch, _ready: _Recorder
) -> None:
    """Whitespace is not a declared cluster set."""
    monkeypatch.setenv(KUBERNETES_INSTANCES_ENV, "   ")

    autoregister.register_now(logging.getLogger(__name__), "*")

    assert len(_ready.calls) == 1


# --- refusing to register instances that cannot work ---------------------------


def test_a_missing_auth_plugin_stops_registration(
    monkeypatch: pytest.MonkeyPatch, _ready: _Recorder, caplog: pytest.LogCaptureFixture
) -> None:
    """A GKE kubeconfig holds no credential — it execs the plugin to mint a token.

    Without the plugin every instance written would be permanently unable to
    connect, and the failure would read as a cluster problem rather than a
    missing binary.
    """
    monkeypatch.setattr(autoregister, "plugin_installed", lambda: False)

    with caplog.at_level(logging.WARNING):
        autoregister.register_now(logging.getLogger(__name__), "*")

    assert _ready.calls == []
    assert "gke-gcloud-auth-plugin" in caplog.text


# --- what gets handed to the registrar -----------------------------------------


def test_the_selector_and_the_auto_tag_reach_the_registrar(_ready: _Recorder) -> None:
    autoregister.register_now(logging.getLogger(__name__), "proj-a")

    call = _ready.calls[0]
    assert call["project"] == "proj-a"
    assert call["tags"] == {"auto_registered": "true"}


def test_existing_clusters_are_never_overwritten(_ready: _Recorder) -> None:
    """``overwrite`` stays at its default. Repointing an instance an operator
    already registered would change where every later ``kubernetes_*`` call
    lands, which a boot-time convenience has no business doing."""
    autoregister.register_now(logging.getLogger(__name__), "*")

    assert _ready.calls[0].get("overwrite") in (None, False)


# --- failure containment -------------------------------------------------------


def test_the_thread_body_swallows_exceptions(monkeypatch: pytest.MonkeyPatch) -> None:
    """A convenience that runs at boot must not be able to kill the process."""

    def _boom(_logger: logging.Logger, _selector: str) -> None:
        raise RuntimeError("control plane unreachable")

    monkeypatch.setattr(autoregister, "register_now", _boom)

    autoregister._run(logging.getLogger(__name__), "*")


def test_discovery_errors_are_logged_not_raised(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    report = RegistrationReport(errors=["project 'proj-b' could not be listed"])
    monkeypatch.setattr(autoregister, "plugin_installed", lambda: True)
    monkeypatch.setattr(autoregister, "resolve_local_classified_integrations", _resolved)
    monkeypatch.setattr(autoregister, "register_gke_clusters", _Recorder(report))

    with caplog.at_level(logging.WARNING):
        autoregister.register_now(logging.getLogger(__name__), "*")

    assert "proj-b" in caplog.text


def test_a_failed_cluster_is_reported_with_its_detail(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    report = RegistrationReport(
        results=[
            ClusterRegistration("c1", "proj-a", "c1", Outcome.FAILED, "private endpoint only"),
            ClusterRegistration("c2", "proj-a", "c2", Outcome.REGISTERED, "registered"),
        ]
    )
    monkeypatch.setattr(autoregister, "plugin_installed", lambda: True)
    monkeypatch.setattr(autoregister, "resolve_local_classified_integrations", _resolved)
    monkeypatch.setattr(autoregister, "register_gke_clusters", _Recorder(report))

    with caplog.at_level(logging.INFO):
        autoregister.register_now(logging.getLogger(__name__), "*")

    assert "private endpoint only" in caplog.text
    assert "registered 1, skipped 0, failed 1" in caplog.text


# --- the background thread -----------------------------------------------------


def test_opting_in_starts_a_daemon_thread(
    monkeypatch: pytest.MonkeyPatch, _ready: _Recorder
) -> None:
    """Daemon, so a hung control-plane call cannot delay process exit; threaded,
    so an unbounded remote call never sits in front of the readiness probe."""
    monkeypatch.setenv(GCP_AUTO_REGISTER_GKE_ENV, "true")

    thread = autoregister.start_gke_autoregistration(logging.getLogger(__name__))

    assert thread is not None
    assert thread.daemon
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert _ready.calls[0]["project"] == "*"


def test_a_second_call_reuses_the_first_thread_instead_of_rediscovering(
    monkeypatch: pytest.MonkeyPatch, _ready: _Recorder
) -> None:
    """Two entry points call in, and the gateway process reaches both.

    ``gateway.runtime.startup`` calls it directly, and importing
    ``gateway.http.webapp`` calls it at module scope — which the gateway also
    does, via ``serve_webapp_in_thread``. Registration is idempotent, so the
    duplicate was harmless in outcome, but it was a second full round of cluster
    discovery for an answer already known, and it logged a bogus second summary.
    """
    monkeypatch.setenv(GCP_AUTO_REGISTER_GKE_ENV, "true")
    logger = logging.getLogger(__name__)

    first = autoregister.start_gke_autoregistration(logger)
    assert first is not None
    first.join(timeout=5)

    second = autoregister.start_gke_autoregistration(logger)

    assert second is first
    assert len(_ready.calls) == 1, _ready.calls


def test_opting_out_is_decided_before_the_once_guard(
    monkeypatch: pytest.MonkeyPatch, _ready: _Recorder
) -> None:
    """Turning it off must not be sticky in the other direction either.

    The guard remembers a thread, not a decision, so a call made while opted out
    records nothing and a later opt-in still runs.
    """
    assert autoregister.start_gke_autoregistration(logging.getLogger(__name__)) is None

    monkeypatch.setenv(GCP_AUTO_REGISTER_GKE_ENV, "true")
    thread = autoregister.start_gke_autoregistration(logging.getLogger(__name__))

    assert thread is not None
    thread.join(timeout=5)
    assert len(_ready.calls) == 1, _ready.calls
