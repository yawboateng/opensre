"""Rootly REST API client for incident context and timeline write-back.

Rootly speaks **JSON:API**: every request and response carries
``application/vnd.api+json``, and a resource arrives as
``{"data": {"id", "type", "attributes"}}`` rather than a flat object. A plain
``application/json`` POST is rejected outright, which is the single easiest
thing to get wrong here — the content type lives on the config's ``headers``
so no call site can forget it.

Responses are flattened to plain dicts before they leave this module: the
envelope is transport detail, and an LLM reading ``data.attributes.title``
through three layers of nesting wastes context for no gain.
"""

from __future__ import annotations

import logging
import re
import threading
from http import HTTPStatus
from typing import Any

import httpx

from integrations.config_models import RootlyIntegrationConfig
from integrations.probes import ProbeResult
from platform.observability.errors.service import capture_service_error

logger = logging.getLogger(__name__)

RootlyConfig = RootlyIntegrationConfig

# Rootly allows 3000 reads/min per key, so the caps here are about prompt size,
# not rate limits: every incident returned is tokens the model has to read.
_MAX_PAGE_SIZE = 50
_DEFAULT_PAGE_SIZE = 20
_MAX_EVENT_PAGE_SIZE = 100
_DEFAULT_EVENT_PAGE_SIZE = 50
_MAX_SUMMARY_CHARS = 2000
_ERROR_DETAIL_CHARS = 300

_INTERNAL_VISIBILITY = "internal"
_EXTERNAL_VISIBILITY = "external"
_VISIBILITIES = (_INTERNAL_VISIBILITY, _EXTERNAL_VISIBILITY)

_SECRET_RE = re.compile(
    r"(?i)(bearer\s+[A-Za-z0-9._~+/=-]{6,}"
    r"|authorization\s*[:=]\s*\S+"
    r"|rootly[_-]?(api[_-]?)?(token|key)\s*[:=]\s*\S+)"
)


def _clamp(value: int | None, default: int, maximum: int) -> int:
    if value is None:
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(parsed, maximum))


def _attributes(record: Any) -> dict[str, Any]:
    """Pull ``attributes`` out of one JSON:API resource object."""
    if not isinstance(record, dict):
        return {}
    attributes = record.get("attributes")
    return attributes if isinstance(attributes, dict) else {}


def _data_list(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def _meta_total(payload: Any) -> int | None:
    if not isinstance(payload, dict):
        return None
    meta = payload.get("meta")
    if not isinstance(meta, dict):
        return None
    total = meta.get("total_count")
    return total if isinstance(total, int) else None


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else f"{text[:limit]}…"


def _named(value: Any) -> str:
    """Rootly nests severity/status names; accept either a string or an object."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("name", "slug", "title"):
            found = value.get(key)
            if isinstance(found, str) and found:
                return found
    return ""


def _shape_incident(record: Any, *, full: bool = False) -> dict[str, Any]:
    """Flatten one incident resource into the fields an RCA actually reads."""
    attributes = _attributes(record)
    shaped: dict[str, Any] = {
        "id": str(record.get("id", "")) if isinstance(record, dict) else "",
        "sequential_id": attributes.get("sequential_id"),
        "slug": attributes.get("slug", ""),
        "title": attributes.get("title", ""),
        "status": attributes.get("status", ""),
        "severity": _named(attributes.get("severity")),
        "url": attributes.get("url", ""),
        "started_at": attributes.get("started_at", ""),
        "created_at": attributes.get("created_at", ""),
        "resolved_at": attributes.get("resolved_at", ""),
    }
    if full:
        shaped.update(
            {
                "summary": _truncate(str(attributes.get("summary") or ""), _MAX_SUMMARY_CHARS),
                "kind": attributes.get("kind", ""),
                "labels": attributes.get("labels", {}),
                "mitigated_at": attributes.get("mitigated_at", ""),
                "acknowledged_at": attributes.get("acknowledged_at", ""),
                "slack_channel_url": attributes.get("slack_channel_url", ""),
                "short_url": attributes.get("short_url", ""),
            }
        )
    return shaped


def _shape_event(record: Any) -> dict[str, Any]:
    attributes = _attributes(record)
    return {
        "id": str(record.get("id", "")) if isinstance(record, dict) else "",
        "event": attributes.get("event", ""),
        "visibility": attributes.get("visibility", ""),
        "kind": attributes.get("kind", ""),
        "occurred_at": attributes.get("occurred_at", ""),
        "created_at": attributes.get("created_at", ""),
    }


def normalize_visibility(value: str | None) -> str:
    """Anything that is not an explicit ``external`` stays internal.

    Defaulting the other way would publish an agent-written note to a customer
    facing timeline on a typo.
    """
    candidate = (value or "").strip().lower()
    return candidate if candidate in _VISIBILITIES else _INTERNAL_VISIBILITY


class RootlyClient:
    """Synchronous client for the Rootly v1 API."""

    def __init__(self, config: RootlyConfig) -> None:
        self.config = config
        self._client: httpx.Client | None = None
        self._client_lock = threading.RLock()

    @property
    def is_configured(self) -> bool:
        return self.config.is_configured

    def _get_client(self) -> httpx.Client:
        with self._client_lock:
            if self._client is None:
                self._client = httpx.Client(
                    base_url=self.config.base_url,
                    headers=self.config.headers,
                    timeout=self.config.timeout_seconds,
                )
            return self._client

    def close(self) -> None:
        with self._client_lock:
            if self._client is not None:
                self._client.close()
                self._client = None

    def __enter__(self) -> RootlyClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _redact(self, value: object) -> str:
        text = str(value)
        if self.config.api_token:
            text = text.replace(self.config.api_token, "[REDACTED]")
        return _SECRET_RE.sub("[REDACTED]", text)

    def _error(self, method: str, exc: Exception) -> dict[str, Any]:
        """Structured failure with the token scrubbed out of every path."""
        capture_service_error(exc, logger=logger, integration="rootly", method=method)
        if isinstance(exc, httpx.HTTPStatusError):
            status = exc.response.status_code
            if status == HTTPStatus.TOO_MANY_REQUESTS:
                return {
                    "success": False,
                    "error": "Rootly rate limit exceeded (HTTP 429); retry shortly.",
                }
            detail = self._redact(exc.response.text[:_ERROR_DETAIL_CHARS])
            return {"success": False, "error": f"HTTP {status}: {detail}"}
        return {"success": False, "error": self._redact(exc)}

    def _get(self, path: str, params: dict[str, Any]) -> Any:
        response = self._get_client().get(path, params=params)
        response.raise_for_status()
        return response.json()

    def probe_access(self) -> ProbeResult:
        """Validate the token with the smallest possible incident listing."""
        if not self.is_configured:
            return ProbeResult.missing("Missing API token.")
        try:
            self._get("/v1/incidents", {"page[size]": 1})
        except Exception as exc:
            return ProbeResult.failed(
                f"Connection failed: {self._redact(exc)}",
                base_url=self.config.base_url,
            )
        return ProbeResult.passed(
            "Connected to Rootly; API token accepted.",
            base_url=self.config.base_url,
        )

    def list_incidents(
        self,
        *,
        status: str = "",
        severity: str = "",
        created_after: str = "",
        search: str = "",
        page_size: int | None = _DEFAULT_PAGE_SIZE,
    ) -> dict[str, Any]:
        """List incidents newest-first, optionally filtered."""
        size = _clamp(page_size, _DEFAULT_PAGE_SIZE, _MAX_PAGE_SIZE)
        params: dict[str, Any] = {"page[size]": size, "sort": "-created_at"}
        if status:
            params["filter[status]"] = status
        if severity:
            params["filter[severity]"] = severity
        if created_after:
            params["filter[created_at][gte]"] = created_after
        if search:
            params["filter[search]"] = search

        try:
            payload = self._get("/v1/incidents", params)
        except Exception as exc:
            return self._error("list_incidents", exc)

        incidents = [_shape_incident(item) for item in _data_list(payload)]
        total = _meta_total(payload)
        return {
            "success": True,
            "incidents": incidents,
            "returned": len(incidents),
            "total": total if total is not None else len(incidents),
            "truncated": bool(total is not None and total > len(incidents)),
        }

    def get_incident(self, incident_id: str) -> dict[str, Any]:
        """Fetch one incident with its related services and environments."""
        try:
            payload = self._get(
                f"/v1/incidents/{incident_id}",
                {"include": "services,environments"},
            )
        except Exception as exc:
            return self._error("get_incident", exc)

        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            return {"success": False, "error": f"Rootly returned no incident for id {incident_id}."}
        return {"success": True, "incident": _shape_incident(data, full=True)}

    def list_incident_events(
        self,
        incident_id: str,
        *,
        page_size: int | None = _DEFAULT_EVENT_PAGE_SIZE,
    ) -> dict[str, Any]:
        """Return the incident timeline oldest-first.

        Causal order is the point of a timeline: reversing it makes the model
        read the resolution before the trigger.
        """
        size = _clamp(page_size, _DEFAULT_EVENT_PAGE_SIZE, _MAX_EVENT_PAGE_SIZE)
        try:
            payload = self._get(
                f"/v1/incidents/{incident_id}/events",
                {"page[size]": size, "sort": "occurred_at"},
            )
        except Exception as exc:
            return self._error("list_incident_events", exc)

        events = [_shape_event(item) for item in _data_list(payload)]
        total = _meta_total(payload)
        return {
            "success": True,
            "incident_id": incident_id,
            "events": events,
            "returned": len(events),
            "total": total if total is not None else len(events),
            "truncated": bool(total is not None and total > len(events)),
        }

    def post_timeline_event(
        self,
        incident_id: str,
        *,
        event: str,
        visibility: str = _INTERNAL_VISIBILITY,
    ) -> dict[str, Any]:
        """Append one event to an incident timeline.

        The occurred-at time is deliberately omitted so Rootly stamps it at
        creation — a findings note belongs at the moment it was written, not at
        a time the model guessed.
        """
        text = (event or "").strip()
        if not text:
            return {"success": False, "error": "event text is required."}
        resolved_visibility = normalize_visibility(visibility)
        body = {
            "data": {
                "type": "incident_events",
                "attributes": {"event": text, "visibility": resolved_visibility},
            }
        }
        try:
            response = self._get_client().post(f"/v1/incidents/{incident_id}/events", json=body)
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            return self._error("post_timeline_event", exc)

        data = payload.get("data") if isinstance(payload, dict) else None
        created = _shape_event(data) if isinstance(data, dict) else {}
        return {
            "success": True,
            "incident_id": incident_id,
            "event_id": created.get("id", ""),
            "event": created.get("event", text),
            "visibility": created.get("visibility", resolved_visibility),
            "occurred_at": created.get("occurred_at", ""),
        }


def make_rootly_client(
    api_token: str | None,
    *,
    base_url: str = "",
    timeout_seconds: float | str | None = None,
) -> RootlyClient | None:
    """Create a Rootly client, or ``None`` when no usable token is supplied."""
    token = (api_token or "").strip()
    if not token:
        return None
    try:
        config = RootlyConfig.model_validate(
            {
                "api_token": token,
                "base_url": base_url or "",
                "timeout_seconds": timeout_seconds,
            }
        )
    except Exception as exc:
        logger.warning("[rootly] Failed to build client config: %s", exc)
        return None
    return RootlyClient(config)


__all__ = [
    "RootlyClient",
    "RootlyConfig",
    "make_rootly_client",
    "normalize_visibility",
]
