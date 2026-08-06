"""GCP tools: filters, payload normalization, aligner choice, and fan-out."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest

from integrations.gcp.tools.gcp_list_projects_tool import gcp_list_projects
from integrations.gcp.tools.gcp_logging_query_tool import gcp_logging_query
from integrations.gcp.tools.gcp_logging_query_tool.entries import extract_message, normalize_entry
from integrations.gcp.tools.gcp_logging_query_tool.filters import build_filter, normalize_severity
from integrations.gcp.tools.gcp_monitoring_query_tool import gcp_monitoring_query
from integrations.gcp.tools.gcp_monitoring_query_tool.aligners import (
    aligner_for_kind,
    extract_metric_type,
)
from integrations.gcp.tools.gcp_monitoring_query_tool.series import normalize_series, point_value
from tools.registry import get_registered_tool

_NOW = datetime(2026, 8, 5, 12, 0, 0, tzinfo=UTC)

# --- registry ----------------------------------------------------------------


@pytest.mark.parametrize("name", ["gcp_logging_query", "gcp_monitoring_query", "gcp_list_projects"])
def test_tool_is_registered(name: str) -> None:
    registered = get_registered_tool(name)

    assert registered is not None
    assert registered.source == "gcp"


def test_project_is_not_an_injected_param() -> None:
    registered = get_registered_tool("gcp_logging_query")

    assert registered is not None
    # Injected params override model input, which would make `project` inert.
    assert "project" not in (registered.injected_params or ())


# --- log filters -------------------------------------------------------------


def test_build_filter_always_bounds_the_window() -> None:
    built = build_filter(user_filter="", severity="", hours=2, now=_NOW)

    assert built == 'timestamp >= "2026-08-05T10:00:00Z"'


def test_build_filter_parenthesises_the_caller_filter() -> None:
    built = build_filter(user_filter='a="1" OR b="2"', severity="", hours=1, now=_NOW)

    # Without the parentheses the OR would escape the timestamp bound and the
    # query would silently read the whole retention window.
    assert built == 'timestamp >= "2026-08-05T11:00:00Z" AND (a="1" OR b="2")'


def test_build_filter_adds_a_severity_floor() -> None:
    built = build_filter(user_filter="", severity="error", hours=1, now=_NOW)

    assert "severity >= ERROR" in built


def test_build_filter_ignores_an_unknown_severity() -> None:
    built = build_filter(user_filter="", severity="loud", hours=1, now=_NOW)

    assert "severity" not in built


def test_normalize_severity_widens_rather_than_failing() -> None:
    assert normalize_severity("warning") == "WARNING"
    assert normalize_severity("nonsense") is None


def test_build_filter_caps_the_lookback() -> None:
    far = build_filter(user_filter="", severity="", hours=24 * 365, now=_NOW)

    # 30 days back from _NOW, not a year.
    assert '"2026-07-06T12:00:00Z"' in far


# --- log entries -------------------------------------------------------------


def test_extract_message_reads_text_payload() -> None:
    assert extract_message({"textPayload": " boom "}) == "boom"


def test_extract_message_reads_a_structured_payload() -> None:
    assert extract_message({"jsonPayload": {"message": "structured boom"}}) == "structured boom"


def test_extract_message_serializes_an_audit_payload() -> None:
    # Audit entries have no conventional message key; returning "" would hide
    # exactly the entries an investigation most needs.
    message = extract_message({"protoPayload": {"methodName": "SetIamPolicy"}})

    assert json.loads(message) == {"methodName": "SetIamPolicy"}


def test_normalize_entry_flattens_gke_labels() -> None:
    normalized = normalize_entry(
        {
            "timestamp": "2026-08-05T12:00:00Z",
            "severity": "ERROR",
            "textPayload": "crash",
            "logName": "projects/acme/logs/stdout",
            "trace": "projects/acme/traces/abc123",
            "resource": {
                "type": "k8s_container",
                "labels": {
                    "project_id": "acme",
                    "cluster_name": "prod",
                    "namespace_name": "payments",
                    "pod_name": "api-1",
                    "container_name": "api",
                },
            },
        }
    )

    assert normalized["log_name"] == "stdout"
    assert normalized["trace"] == "abc123"
    assert normalized["pod_name"] == "api-1"
    assert normalized["namespace_name"] == "payments"


# --- time series -------------------------------------------------------------


def test_point_value_decodes_int64_from_a_json_string() -> None:
    assert point_value({"int64Value": "42"}) == 42


def test_point_value_summarises_a_distribution() -> None:
    assert point_value({"distributionValue": {"count": "3", "mean": 1.5}}) == {
        "count": "3",
        "mean": 1.5,
    }


def test_normalize_series_returns_points_in_causal_order() -> None:
    normalized = normalize_series(
        {
            "metric": {"type": "kubernetes.io/container/memory/used_bytes"},
            "resource": {"type": "k8s_container"},
            "points": [
                {"interval": {"endTime": "t2"}, "value": {"doubleValue": 2.0}},
                {"interval": {"endTime": "t1"}, "value": {"doubleValue": 1.0}},
            ],
        }
    )

    assert [point["time"] for point in normalized["points"]] == ["t1", "t2"]


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("GAUGE", "ALIGN_MEAN"),
        ("CUMULATIVE", "ALIGN_RATE"),
        ("DELTA", "ALIGN_RATE"),
        ("", "ALIGN_MEAN"),
    ],
)
def test_aligner_matches_the_metric_kind(kind: str, expected: str) -> None:
    assert aligner_for_kind(kind) == expected


def test_extract_metric_type_reads_the_filter() -> None:
    assert extract_metric_type('resource.type="k8s_container" AND metric.type="a/b/c"') == "a/b/c"


# --- fakes -------------------------------------------------------------------


class _FakeEntries:
    def __init__(self, calls: list[dict[str, Any]], pages: list[dict[str, Any]]) -> None:
        self._calls = calls
        self._pages = pages

    def list(self, body: dict[str, Any]) -> _FakeEntries:
        self._calls.append(body)
        return self

    def execute(self) -> dict[str, Any]:
        return self._pages.pop(0)


class _FakeLoggingService:
    def __init__(self, calls: list[dict[str, Any]], pages: list[dict[str, Any]]) -> None:
        self._entries = _FakeEntries(calls, pages)

    def entries(self) -> _FakeEntries:
        return self._entries


def _entry(timestamp: str, message: str) -> dict[str, Any]:
    return {"timestamp": timestamp, "severity": "ERROR", "textPayload": message}


# --- logging tool ------------------------------------------------------------


def test_logging_query_rejects_an_unknown_project() -> None:
    result = gcp_logging_query(project="nope", default_project="acme", available_projects=["acme"])

    assert result["available"] is False
    assert "nope" in result["error"]


def test_logging_query_batches_one_credential_into_one_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import integrations.gcp.tools.gcp_logging_query_tool as module

    calls: list[dict[str, Any]] = []
    pages = [{"entries": [_entry("2026-08-05T12:00:00Z", "boom")]}]
    monkeypatch.setattr(
        module, "build_service", lambda _config, _api: _FakeLoggingService(calls, pages)
    )

    config = {"project_id": "acme", "additional_projects": ["acme-staging"]}
    result = gcp_logging_query(
        project="*",
        default_project="acme",
        available_projects=["acme", "acme-staging"],
        project_configs={"acme": config, "acme-staging": config},
    )

    assert len(calls) == 1
    assert calls[0]["resourceNames"] == ["projects/acme", "projects/acme-staging"]
    assert result["found"] is True
    assert result["projects"] == ["acme", "acme-staging"]


def test_logging_query_fans_out_across_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    import integrations.gcp.tools.gcp_logging_query_tool as module

    calls: list[dict[str, Any]] = []
    pages = [
        {"entries": [_entry("2026-08-05T11:00:00Z", "older")]},
        {"entries": [_entry("2026-08-05T12:00:00Z", "newer")]},
    ]
    monkeypatch.setattr(
        module, "build_service", lambda _config, _api: _FakeLoggingService(calls, pages)
    )

    result = gcp_logging_query(
        project="*",
        default_project="acme",
        available_projects=["acme", "research"],
        project_configs={
            "acme": {"project_id": "acme"},
            "research": {"project_id": "research", "impersonate_service_account": "ro@x"},
        },
    )

    assert [call["resourceNames"] for call in calls] == [
        ["projects/acme"],
        ["projects/research"],
    ]
    # Two newest-first pages merged back into one newest-first list.
    assert [entry["message"] for entry in result["entries"]] == ["newer", "older"]


def test_logging_query_marks_a_merged_result_truncated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import integrations.gcp.tools.gcp_logging_query_tool as module

    calls: list[dict[str, Any]] = []
    pages = [
        {"entries": [_entry("2026-08-05T11:00:00Z", "a")]},
        {"entries": [_entry("2026-08-05T12:00:00Z", "b")]},
    ]
    monkeypatch.setattr(
        module, "build_service", lambda _config, _api: _FakeLoggingService(calls, pages)
    )

    result = gcp_logging_query(
        project="*",
        limit=1,
        default_project="acme",
        available_projects=["acme", "research"],
        project_configs={
            "acme": {"project_id": "acme"},
            "research": {"project_id": "research", "impersonate_service_account": "ro@x"},
        },
    )

    assert result["entry_count"] == 1
    assert result["truncated"] is True


# --- monitoring tool ---------------------------------------------------------


class _FakeTimeSeries:
    def __init__(self, aligners: list[str], responses: list[Any]) -> None:
        self._aligners = aligners
        self._responses = responses

    def list(self, **kwargs: Any) -> _FakeTimeSeries:
        self._aligners.append(str(kwargs["aggregation_perSeriesAligner"]))
        return self

    def execute(self) -> dict[str, Any]:
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        assert isinstance(response, dict)
        return response


class _FakeMetricDescriptors:
    def __init__(self, kind: str) -> None:
        self._kind = kind

    def get(self, name: str) -> _FakeMetricDescriptors:
        self._name = name
        return self

    def execute(self) -> dict[str, str]:
        if not self._kind:
            raise RuntimeError("descriptor unavailable")
        return {"metricKind": self._kind}


class _FakeMonitoringProjects:
    def __init__(self, aligners: list[str], responses: list[Any], kind: str) -> None:
        self._series = _FakeTimeSeries(aligners, responses)
        self._descriptors = _FakeMetricDescriptors(kind)

    def timeSeries(self) -> _FakeTimeSeries:  # noqa: N802 — Google API method name
        return self._series

    def metricDescriptors(self) -> _FakeMetricDescriptors:  # noqa: N802 — Google API method
        return self._descriptors


class _FakeMonitoringService:
    def __init__(self, aligners: list[str], responses: list[Any], kind: str) -> None:
        self._projects = _FakeMonitoringProjects(aligners, responses, kind)

    def projects(self) -> _FakeMonitoringProjects:
        return self._projects


class _AlignerRejected(Exception):
    status_code = 400
    content = json.dumps(
        {"error": {"message": "Field aggregation.perSeriesAligner had an invalid value"}}
    ).encode()


def _install_monitoring(
    monkeypatch: pytest.MonkeyPatch, responses: list[Any], kind: str
) -> list[str]:
    import integrations.gcp.tools.gcp_monitoring_query_tool as module

    aligners: list[str] = []
    monkeypatch.setattr(
        module,
        "build_service",
        lambda _config, _api: _FakeMonitoringService(aligners, responses, kind),
    )
    return aligners


def test_monitoring_query_requires_a_filter() -> None:
    result = gcp_monitoring_query(default_project="acme", available_projects=["acme"])

    assert result["available"] is False
    assert "filter is required" in result["error"]


def test_monitoring_query_rejects_multiple_projects() -> None:
    result = gcp_monitoring_query(
        filter='metric.type="a/b"',
        project="*",
        default_project="acme",
        available_projects=["acme", "other"],
    )

    assert result["available"] is False
    assert "one project per call" in result["error"]


def test_monitoring_query_rates_a_cumulative_metric(monkeypatch: pytest.MonkeyPatch) -> None:
    aligners = _install_monitoring(monkeypatch, [{"timeSeries": []}], "CUMULATIVE")

    result = gcp_monitoring_query(
        filter='metric.type="a/b/c"', default_project="acme", available_projects=["acme"]
    )

    assert aligners == ["ALIGN_RATE"]
    assert result["aligner"] == "ALIGN_RATE"


def test_monitoring_query_honours_an_explicit_aligner(monkeypatch: pytest.MonkeyPatch) -> None:
    aligners = _install_monitoring(monkeypatch, [{"timeSeries": []}], "CUMULATIVE")

    gcp_monitoring_query(
        filter='metric.type="a/b/c"',
        aligner="align_max",
        default_project="acme",
        available_projects=["acme"],
    )

    assert aligners == ["ALIGN_MAX"]


def test_monitoring_query_retries_a_rejected_aligner(monkeypatch: pytest.MonkeyPatch) -> None:
    # No descriptor available, so the first guess is the gauge default and the
    # API rejects it — the retry is what keeps counters usable.
    aligners = _install_monitoring(monkeypatch, [_AlignerRejected(), {"timeSeries": []}], kind="")

    result = gcp_monitoring_query(
        filter='metric.type="a/b/c"', default_project="acme", available_projects=["acme"]
    )

    assert aligners == ["ALIGN_MEAN", "ALIGN_RATE"]
    assert result["aligner"] == "ALIGN_RATE"


def test_monitoring_query_does_not_retry_an_explicit_aligner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    aligners = _install_monitoring(monkeypatch, [_AlignerRejected()], kind="")

    result = gcp_monitoring_query(
        filter='metric.type="a/b/c"',
        aligner="ALIGN_MEAN",
        default_project="acme",
        available_projects=["acme"],
    )

    assert aligners == ["ALIGN_MEAN"]
    assert result["found"] is False


# --- list projects tool ------------------------------------------------------


class _FakeRMProjects:
    def __init__(self, response: Any) -> None:
        self._response = response

    def list(self, pageSize: int) -> _FakeRMProjects:  # noqa: N803 — Google API kwarg
        self._page_size = pageSize
        return self

    def execute(self) -> dict[str, Any]:
        if isinstance(self._response, Exception):
            raise self._response
        assert isinstance(self._response, dict)
        return self._response


class _FakeRMService:
    def __init__(self, response: Any) -> None:
        self._projects = _FakeRMProjects(response)

    def projects(self) -> _FakeRMProjects:
        return self._projects


def test_list_projects_is_unavailable_without_configuration() -> None:
    result = gcp_list_projects()

    assert result["available"] is False


def test_list_projects_merges_discovery_and_skips_deleted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import integrations.gcp.tools.gcp_list_projects_tool as module

    response = {
        "projects": [
            {"projectId": "acme", "lifecycleState": "ACTIVE"},
            {"projectId": "discovered", "lifecycleState": "ACTIVE"},
            {"projectId": "gone", "lifecycleState": "DELETE_REQUESTED"},
        ]
    }
    monkeypatch.setattr(module, "build_service", lambda _config, _api: _FakeRMService(response))

    result = gcp_list_projects(
        default_project="acme",
        available_projects=["acme"],
        project_configs={"acme": {"project_id": "acme"}},
    )

    assert result["projects"] == ["acme", "discovered"]
    assert "gone" not in result["discovered_projects"]
    # Discovered-but-unconfigured projects are visible, not queryable — say so.
    assert "note" in result


def test_list_projects_survives_a_missing_list_permission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import integrations.gcp.tools.gcp_list_projects_tool as module

    monkeypatch.setattr(
        module,
        "build_service",
        lambda _config, _api: _FakeRMService(RuntimeError("permission denied")),
    )

    result = gcp_list_projects(
        default_project="acme",
        available_projects=["acme", "acme-staging"],
        project_configs={"acme": {"project_id": "acme"}},
    )

    # Losing the optional expansion must not lose the configured answer.
    assert result["projects"] == ["acme", "acme-staging"]
    assert "discovery_error" in result
