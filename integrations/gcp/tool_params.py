"""Shared ``extract_params`` payload and config rehydration for GCP tools.

Every GCP tool needs the same two things: the set of projects the call may
target, and the credential that reaches each of them. Both travel as
unprotected passthroughs — see :mod:`integrations.gcp.projects` for why
``project`` must not be an injected (protected) parameter.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from integrations.config_models import GCPIntegrationConfig
from integrations.selectors import get_instances

#: Only real model fields survive rehydration. ``availability_view`` stamps
#: ``connection_verified`` onto every source dict and the synthetic harnesses
#: inject ``_backend``; ``GCPIntegrationConfig`` forbids extra fields, so
#: passing the raw source dict through would raise on a key the tool never
#: asked for.
_MODEL_FIELDS = frozenset(GCPIntegrationConfig.model_fields)

_DEFAULT_LIMIT = 100


def sanitize_config(raw: object) -> dict[str, Any]:
    """Drop every key ``GCPIntegrationConfig`` would reject.

    Accepts a model as well as a dict: ``_all_gcp_instances`` carries the
    classified ``GCPIntegrationConfig`` objects, while the flat source dict and
    the tool-call passthrough carry plain dicts.
    """
    if isinstance(raw, BaseModel):
        raw = raw.model_dump()
    if not isinstance(raw, dict):
        return {}
    return {key: value for key, value in raw.items() if key in _MODEL_FIELDS}


def _projects_of(config: dict[str, Any]) -> list[str]:
    extra = config.get("additional_projects") or []
    items = extra.split(",") if isinstance(extra, str) else extra
    if not isinstance(items, (list, tuple)):
        items = []
    return [str(config.get("project_id", "") or ""), *(str(item).strip() for item in items)]


def gcp_tool_params(sources: dict[str, dict]) -> dict[str, Any]:
    """Return the standard passthrough payload for a GCP tool's ``extract_params``.

    Walks every registered GCP instance so a deployment with one credential per
    estate presents a single flat project namespace to the model — it picks a
    project, not a credential.
    """
    project_configs: dict[str, dict[str, Any]] = {}
    default_project = ""
    limit = _DEFAULT_LIMIT

    for instance in get_instances(sources, "gcp"):
        config = sanitize_config(instance.get("config"))
        projects = [project for project in _projects_of(config) if project]
        if not projects:
            continue
        if not default_project:
            default_project = projects[0]
            limit = int(config.get("max_results") or _DEFAULT_LIMIT)
        for project in projects:
            # First instance wins: two credentials naming the same project is a
            # configuration accident, not a request to query it twice.
            project_configs.setdefault(project, config)

    return {
        "default_project": default_project,
        "available_projects": list(project_configs),
        "project_configs": project_configs,
        "limit": limit,
    }


def config_from(raw: dict[str, Any] | None, *, fallback_project: str = "") -> GCPIntegrationConfig:
    """Rebuild a config from the passthrough dict.

    ``fallback_project`` keeps the tool callable in unit tests and synthetic
    harnesses that stub the client and never populate ``project_configs``, and
    covers the case where a config was registered without the project the caller
    asked for.
    """
    payload = sanitize_config(raw)
    if not payload.get("project_id"):
        payload["project_id"] = fallback_project
    return GCPIntegrationConfig.model_validate(payload)
