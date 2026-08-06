"""Project discovery tool — how the agent learns valid ``project`` values.

Reports the configured scope, and additionally attempts a live Resource Manager
listing so folder- or organization-level access surfaces projects that were
never named in configuration. The live call is best-effort: ``resourcemanager.
projects.list`` is a permission many service accounts legitimately lack, and a
missing grant must not make the tool useless when the configured scope alone
already answers the question.
"""

from __future__ import annotations

from typing import Any

from core.tool_framework.telemetry import report_run_error
from core.tool_framework.tool_decorator import tool
from core.tool_framework.utils.tool_availability import tool_unavailable
from integrations.gcp.availability import gcp_available
from integrations.gcp.project_discovery import DiscoveryResult, discover
from integrations.gcp.projects import group_projects
from integrations.gcp.tool_params import config_from, gcp_tool_params

_COMPONENT = "integrations.gcp.tools.gcp_list_projects_tool"

#: What to tell the agent when a visible project is not on the allow-list. The
#: fix is configuration, so it names both ways of applying it — one project at a
#: time, or every project this credential can see.
_UNUSABLE_NOTE = (
    "Projects outside configured_projects are visible but not yet queryable. "
    "Add them to GCP_ADDITIONAL_PROJECTS by id, or set "
    "GCP_ADDITIONAL_PROJECTS=discover to allow everything listed here."
)


def _discover(config_payload: dict[str, Any], fallback_project: str) -> DiscoveryResult:
    """List what one credential can see.

    Shares :func:`~integrations.gcp.project_discovery.discover` with the
    allow-list expansion rather than listing separately. Two listings would be
    two answers, and this tool exists to tell the operator what the deployment
    can reach — the one thing it must never be wrong about is the allow-list it
    is reporting on.

    Uses the *cached* entry point deliberately: when
    ``GCP_ADDITIONAL_PROJECTS=discover`` is on, this returns the same listing the
    allow-list was built from, with no second round trip.
    """
    return discover(config_from(config_payload, fallback_project=fallback_project))


def _report_discovery_failure(result: DiscoveryResult) -> None:
    """Send the failed listing to Sentry under this tool's name, at ``warning``.

    Warning, not error: a missing ``resourcemanager.projects.list`` grant is a
    configuration choice, not a defect, and the tool still answers from the
    configured scope. Reported here rather than inside ``project_discovery``
    because that module also serves allow-list expansion, which is not a tool
    call and would have to borrow a tool's name to report at all.
    """
    if result.exception is None:
        # A GCPClientError — a credential that never built. That is already
        # surfaced in ``discovery_error`` and is not a runtime fault to page on.
        return
    report_run_error(
        result.exception,
        tool_name="gcp_list_projects",
        source="gcp",
        component=_COMPONENT,
        method="cloudresourcemanager.projects.list",
        severity="warning",
    )


@tool(
    name="gcp_list_projects",
    display_name="GCP projects",
    source="gcp",
    description=(
        "List the Google Cloud projects this deployment can query. Call before "
        "passing a non-default project to another GCP tool."
    ),
    use_cases=[
        "Discovering valid project values for gcp_logging_query or gcp_monitoring_query",
        "Checking whether a service's project is in scope before investigating it",
    ],
    anti_examples=[
        "Do not call repeatedly — the project list does not change during an investigation.",
    ],
    requires=[],
    input_schema={"type": "object", "properties": {}, "required": []},
    is_available=gcp_available,
    extract_params=gcp_tool_params,
)
def gcp_list_projects(
    default_project: str = "",
    available_projects: list[str] | None = None,
    project_configs: dict[str, Any] | None = None,
    # ``gcp_tool_params`` is shared by all three GCP tools, so it also injects
    # ``limit`` — irrelevant here, but it has to be accepted.
    **_injected: Any,
) -> dict[str, Any]:
    """Return configured and (where permitted) discoverable GCP projects."""
    configured = list(available_projects or [])
    if default_project and default_project not in configured:
        configured.insert(0, default_project)
    if not configured:
        return tool_unavailable(
            "gcp", "no GCP project is configured; set GCP_PROJECT_ID", projects=[]
        )

    result: dict[str, Any] = {
        "default_project": default_project,
        "configured_projects": configured,
        "projects": configured,
    }

    # One listing per registered credential: each can see a different slice of
    # the resource hierarchy, so querying only the default would under-report.
    discovered: list[str] = []
    seen_discovered: set[str] = set()
    # Only the first failure is kept. One event per tool call, not one per
    # credential: a deployment whose credentials all lack the grant would
    # otherwise send an identical event per instance on every call.
    failure: DiscoveryResult | None = None
    for config_payload, group in group_projects(configured, project_configs):
        listing = _discover(config_payload, group[0])
        if listing.error and failure is None:
            failure = listing
        for project in listing.projects:
            if project not in seen_discovered:
                seen_discovered.add(project)
                discovered.append(project)

    if failure is not None:
        _report_discovery_failure(failure)
        result["discovery_error"] = failure.error
        if not discovered:
            # No credential answered, so there is nothing to merge and no
            # `discovered_projects` key to promise. The configured scope is
            # still a correct answer, so this is not an unavailable envelope.
            return result

    merged = list(configured)
    seen = set(merged)
    for project in discovered:
        if project not in seen:
            seen.add(project)
            merged.append(project)

    result["discovered_projects"] = discovered
    result["projects"] = merged
    # Only projects in `configured` are accepted by the other GCP tools today;
    # say so rather than letting the agent infer that everything listed is usable.
    unusable = [p for p in discovered if p not in configured]
    if unusable:
        result["note"] = _UNUSABLE_NOTE
    return result
