"""Google Cloud credential resolution and API client construction.

Built on the discovery-based ``google-api-python-client`` rather than the
per-service ``google-cloud-*`` libraries. ``logging/v2``, ``monitoring/v3`` and
``cloudresourcemanager/v1`` are all reachable through discovery, so this
integration adds no new dependency to the project — which matters here: the
``google-cloud-aiplatform`` import chain has already caused one production
outage when a partner-model route hard-imported a package that was not
installed.

Imports go through :func:`importlib.import_module` behind a ``cast(Any, ...)``,
matching ``integrations/google_docs/client.py``. The Google libraries ship no
type stubs and ``mypy.ini`` sets ``ignore_missing_imports = False`` globally, so
a plain ``import googleapiclient.discovery`` would fail ``make typecheck``.
"""

from __future__ import annotations

import importlib
import json
import logging
from pathlib import Path
from typing import Any, cast

from integrations.config_models import GCPIntegrationConfig

logger = logging.getLogger(__name__)

#: Single scope covering read access to every API this integration touches.
#: Individual permissions are still enforced by IAM roles on the principal.
CLOUD_PLATFORM_SCOPE = "https://www.googleapis.com/auth/cloud-platform"

#: Discovery API names/versions used by the GCP tools.
LOGGING_API = ("logging", "v2")
MONITORING_API = ("monitoring", "v3")
RESOURCE_MANAGER_API = ("cloudresourcemanager", "v1")
CONTAINER_API = ("container", "v1")
COMPUTE_API = ("compute", "v1")


class GCPClientError(RuntimeError):
    """Raised when credentials or an API client cannot be constructed."""


def _load_service_account_info(raw: str) -> dict[str, Any]:
    """Parse a service-account key given as either literal JSON or a file path.

    Operators supply this both ways in practice — a mounted key file path in
    Kubernetes, an inline JSON blob in a secret manager — and guessing wrong
    produces a confusing parse error, so both are accepted explicitly.
    """
    text = raw.strip()
    if not text.startswith("{"):
        try:
            text = Path(text).read_text(encoding="utf-8")
        except OSError as exc:
            raise GCPClientError(
                f"service_account_key points at a file that could not be read: {exc.strerror}"
            ) from exc
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        # Deliberately no exc.msg and no slice of the input: a malformed value
        # here is very often a real credential pasted into the wrong variable,
        # and JSONDecodeError embeds the offending text.
        raise GCPClientError(
            f"service_account_key is not valid JSON (line {exc.lineno}, column {exc.colno})"
        ) from exc
    if not isinstance(parsed, dict):
        raise GCPClientError("service_account_key JSON must be an object")
    return parsed


def resolve_credentials(config: GCPIntegrationConfig) -> Any:
    """Return Google credentials for ``config``.

    Precedence: explicit service-account key, then impersonation layered over
    the ambient credential, then plain Application Default Credentials. On GKE
    with Workload Identity the ADC path needs no configuration at all.
    """
    if config.service_account_key:
        service_account = cast(Any, importlib.import_module("google.oauth2.service_account"))
        base = service_account.Credentials.from_service_account_info(
            _load_service_account_info(config.service_account_key),
            scopes=[CLOUD_PLATFORM_SCOPE],
        )
    else:
        google_auth = cast(Any, importlib.import_module("google.auth"))
        try:
            base, _detected_project = google_auth.default(scopes=[CLOUD_PLATFORM_SCOPE])
        except Exception as exc:
            raise GCPClientError(
                "no Google credentials available: set GCP_SERVICE_ACCOUNT_KEY, or run with "
                f"Workload Identity / Application Default Credentials ({type(exc).__name__})"
            ) from exc

    if config.impersonate_service_account:
        impersonated = cast(Any, importlib.import_module("google.auth.impersonated_credentials"))
        return impersonated.Credentials(
            source_credentials=base,
            target_principal=config.impersonate_service_account,
            target_scopes=[CLOUD_PLATFORM_SCOPE],
        )
    return base


def build_service(config: GCPIntegrationConfig, api: tuple[str, str]) -> Any:
    """Build a discovery client for ``api`` (an ``(name, version)`` pair).

    ``cache_discovery=False`` suppresses the oauth2client file-cache warning;
    the discovery document itself is bundled with the library, so this does not
    add a network round-trip.
    """
    name, version = api
    discovery = cast(Any, importlib.import_module("googleapiclient.discovery"))
    try:
        return discovery.build(
            name,
            version,
            credentials=resolve_credentials(config),
            cache_discovery=False,
        )
    except GCPClientError:
        raise
    except Exception as exc:
        raise GCPClientError(
            f"could not build the {name}/{version} client ({type(exc).__name__})"
        ) from exc


def describe_api_error(exc: Exception) -> str:
    """Render a Google API exception as one actionable line.

    Google's own 403 text is kept because it names the exact missing
    permission, which is the single most useful thing an operator can be told.
    Stack traces and payload echoes are dropped. Note that a 403 here does not
    imply the resource exists — Vertex and several other APIs check IAM before
    existence, so "or it may not exist" in Google's message is not a hint.
    """
    status = getattr(exc, "status_code", None)
    if status is None:
        response = getattr(exc, "resp", None)
        status = getattr(response, "status", None)

    detail = ""
    content = getattr(exc, "content", None)
    if isinstance(content, (bytes, bytearray)):
        try:
            payload = json.loads(content.decode("utf-8", "replace"))
            detail = str(payload.get("error", {}).get("message", "")).strip()
        except (ValueError, AttributeError):
            detail = ""

    if status and detail:
        return f"HTTP {status}: {detail}"
    if status:
        return f"HTTP {status} from the Google API"
    return f"{type(exc).__name__} calling the Google API"
