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
from integrations.gcp.client import (
    RESOURCE_MANAGER_API,
    GCPClientError,
    build_service,
    describe_api_error,
)
from integrations.gcp.projects import group_projects
from integrations.gcp.tool_params import config_from, gcp_tool_params

_COMPONENT = "integrations.gcp.tools.gcp_list_projects_tool"

#: Live discovery can return a very large estate; cap what reaches the context.
_MAX_DISCOVERED = 200


def _discover(config_payload: dict[str, Any], fallback_project: str) -> tuple[list[str], str]:
    """Return ``(active project ids, error)`` for one credential."""
    try:
        config = config_from(config_payload, fallback_project=fallback_project)
        service = build_service(config, RESOURCE_MANAGER_API)
        response = service.projects().list(pageSize=_MAX_DISCOVERED).execute()
    except GCPClientError as exc:
        return [], str(exc)
    except Exception as exc:
        report_run_error(
            exc,
            tool_name="gcp_list_projects",
            source="gcp",
            component=_COMPONENT,
            method="cloudresourcemanager.projects.list",
            severity="warning",
            extras={"fallback_project": fallback_project},
        )
        # Not an error result: the configured list is still a valid, useful
        # answer. Only the optional live expansion failed.
        return [], describe_api_error(exc)

    return [
        str(item.get("projectId", ""))
        for item in (response.get("projects") or [])
        if isinstance(item, dict)
        and item.get("projectId")
        and item.get("lifecycleState", "ACTIVE") == "ACTIVE"
    ], ""


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
    errors: list[str] = []
    for config_payload, group in group_projects(configured, project_configs):
        found, error = _discover(config_payload, group[0])
        if error:
            errors.append(error)
        for project in found:
            if project not in seen_discovered:
                seen_discovered.add(project)
                discovered.append(project)

    if errors and not discovered:
        result["discovery_error"] = errors[0]
        return result

    merged = list(configured)
    seen = set(merged)
    for project in discovered:
        if project not in seen:
            seen.add(project)
            merged.append(project)

    if errors:
        result["discovery_error"] = errors[0]
    result["discovered_projects"] = discovered
    result["projects"] = merged
    # Only projects in `configured` are accepted by the other GCP tools today;
    # say so rather than letting the agent infer that everything listed is usable.
    unusable = [p for p in discovered if p not in configured]
    if unusable:
        result["note"] = (
            "Projects outside configured_projects are visible but not yet queryable. "
            "Add them to GCP_ADDITIONAL_PROJECTS to use them."
        )
    return result
