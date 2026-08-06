"""``opensre integrations add-gke-clusters`` surface behavior.

The registration logic itself is covered in
``tests/integrations/test_gcp_gke_registration.py``; this pins the parts that
only exist at the CLI boundary — the up-front auth-plugin gate, tag parsing,
and the exit code an operator's script will branch on.
"""

from __future__ import annotations

from typing import Any

import pytest
from click.testing import CliRunner

from integrations.gcp import gke as gke_package
from integrations.gcp.gke.registration import ClusterRegistration, Outcome, RegistrationReport
from platform.common.exit_codes import ERROR, SUCCESS
from surfaces.cli.commands.integrations import add_gke_clusters_command


def _report(*results: ClusterRegistration, errors: list[str] | None = None) -> RegistrationReport:
    return RegistrationReport(results=list(results), errors=list(errors or []))


def _registered(name: str = "prod") -> ClusterRegistration:
    return ClusterRegistration(name, "acme", name, Outcome.REGISTERED, f"Cluster '{name}' saved.")


class _Recorder:
    """Captures the keyword arguments the command forwards to registration."""

    def __init__(self, report: RegistrationReport) -> None:
        self.report = report
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> RegistrationReport:
        self.calls.append(kwargs)
        return self.report


@pytest.fixture
def _no_store(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the command away from the real integration store."""

    def _resolved() -> dict[str, Any]:
        return {"gcp": {"project_id": "acme"}}

    monkeypatch.setattr("integrations.catalog.resolve_effective_integrations", _resolved)


def _set_plugin(monkeypatch: pytest.MonkeyPatch, installed: bool) -> None:
    def _installed() -> bool:
        return installed

    monkeypatch.setattr(gke_package, "plugin_installed", _installed)


def _set_register(monkeypatch: pytest.MonkeyPatch, recorder: _Recorder) -> None:
    monkeypatch.setattr(gke_package, "register_gke_clusters", recorder)


@pytest.mark.usefixtures("_no_store")
def test_a_missing_auth_plugin_stops_the_run_before_anything_is_written(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _Recorder(_report())
    _set_plugin(monkeypatch, installed=False)
    _set_register(monkeypatch, recorder)

    result = CliRunner().invoke(add_gke_clusters_command, [])

    assert result.exit_code == ERROR
    assert "gke-gcloud-auth-plugin" in result.output
    assert recorder.calls == []


@pytest.mark.usefixtures("_no_store")
def test_a_dry_run_proceeds_without_the_plugin_but_warns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _Recorder(_report(_registered()))
    _set_plugin(monkeypatch, installed=False)
    _set_register(monkeypatch, recorder)

    result = CliRunner().invoke(add_gke_clusters_command, ["--dry-run"])

    assert result.exit_code == SUCCESS
    assert "Warning" in result.output
    assert recorder.calls[0]["dry_run"] is True


@pytest.mark.usefixtures("_no_store")
def test_the_run_reports_each_cluster_and_a_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder(
        _report(
            _registered("prod"),
            ClusterRegistration(
                "staging", "acme", "staging", Outcome.SKIPPED, "already registered"
            ),
        )
    )
    _set_plugin(monkeypatch, installed=True)
    _set_register(monkeypatch, recorder)

    result = CliRunner().invoke(add_gke_clusters_command, [])

    assert result.exit_code == SUCCESS
    assert "+ prod (acme) -> prod" in result.output
    assert "= staging (acme) -> staging" in result.output
    assert "registered 1, skipped 1, failed 0." in result.output


@pytest.mark.usefixtures("_no_store")
def test_a_failed_cluster_produces_a_nonzero_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder(
        _report(ClusterRegistration("prod", "acme", "prod", Outcome.FAILED, "connection refused"))
    )
    _set_plugin(monkeypatch, installed=True)
    _set_register(monkeypatch, recorder)

    result = CliRunner().invoke(add_gke_clusters_command, [])

    assert result.exit_code == ERROR
    assert "! prod (acme) -> prod: connection refused" in result.output


@pytest.mark.usefixtures("_no_store")
def test_a_discovery_error_produces_a_nonzero_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder(_report(errors=["acme: HTTP 403: denied"]))
    _set_plugin(monkeypatch, installed=True)
    _set_register(monkeypatch, recorder)

    result = CliRunner().invoke(add_gke_clusters_command, [])

    assert result.exit_code == ERROR
    assert "acme: HTTP 403: denied" in result.output


@pytest.mark.usefixtures("_no_store")
def test_an_empty_estate_says_so_rather_than_printing_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _Recorder(_report())
    _set_plugin(monkeypatch, installed=True)
    _set_register(monkeypatch, recorder)

    result = CliRunner().invoke(add_gke_clusters_command, [])

    assert result.exit_code == SUCCESS
    assert "No GKE clusters found" in result.output


@pytest.mark.usefixtures("_no_store")
def test_flags_reach_the_registration_call(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder(_report(_registered()))
    _set_plugin(monkeypatch, installed=True)
    _set_register(monkeypatch, recorder)

    result = CliRunner().invoke(
        add_gke_clusters_command,
        ["--project", "acme,research", "--tag", "env=prod", "--overwrite", "--no-verify"],
    )

    assert result.exit_code == SUCCESS
    call = recorder.calls[0]
    assert call["project"] == "acme,research"
    assert call["tags"] == {"env": "prod"}
    assert call["overwrite"] is True
    assert call["verify"] is False


@pytest.mark.usefixtures("_no_store")
def test_a_malformed_tag_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder(_report())
    _set_plugin(monkeypatch, installed=True)
    _set_register(monkeypatch, recorder)

    result = CliRunner().invoke(add_gke_clusters_command, ["--tag", "oops"])

    assert result.exit_code == ERROR
    assert "expected KEY=VALUE" in result.output
    assert recorder.calls == []
