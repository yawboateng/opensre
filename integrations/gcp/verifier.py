"""GCP integration verifier — live Resource Manager probe.

Auto-discovered by ``integrations/_verifiers_loader.py``; no central import
list to update.
"""

from __future__ import annotations

from typing import Any

from integrations.config_models import GCPIntegrationConfig
from integrations.gcp.client import (
    RESOURCE_MANAGER_API,
    GCPClientError,
    build_service,
    describe_api_error,
)
from integrations.verification import register_verifier, result


@register_verifier("gcp")
def verify_gcp(source: str, config: dict[str, Any]) -> dict[str, str]:
    """Confirm credentials resolve and the primary project is readable.

    A live call rather than a config-presence check, matching the AWS STS
    verifier. It is worth the round-trip here: the failure this catches —
    credentials that resolve to the wrong principal — is otherwise invisible
    until the first investigation, and surfaces as a bare 403.
    """
    try:
        cfg = GCPIntegrationConfig.model_validate(config)
    except Exception as exc:
        return result("gcp", source, "missing", str(exc))

    if not cfg.project_id:
        return result("gcp", source, "missing", "Missing project_id.")

    try:
        service = build_service(cfg, RESOURCE_MANAGER_API)
        project = service.projects().get(projectId=cfg.project_id).execute()
    except GCPClientError as exc:
        return result("gcp", source, "failed", str(exc))
    except Exception as exc:
        return result(
            "gcp",
            source,
            "failed",
            f"Could not read project {cfg.project_id}: {describe_api_error(exc)}",
        )

    name = str(project.get("name", "")).strip() or cfg.project_id
    scope = len(cfg.all_projects)
    reach = "1 project" if scope == 1 else f"{scope} projects"
    mode = (
        "service-account key"
        if cfg.service_account_key
        else ("impersonation" if cfg.impersonate_service_account else "ADC")
    )
    return result(
        "gcp",
        source,
        "passed",
        f"Connected to GCP project {name} ({cfg.project_id}) via {mode}; scope covers {reach}.",
    )
