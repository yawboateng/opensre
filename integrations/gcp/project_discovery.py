"""Expand ``GCP_ADDITIONAL_PROJECTS=discover`` into a concrete allow-list.

GCP inherits IAM down the resource hierarchy, so one service account granted a
viewer role at the folder or organization level already reads every project
beneath it. Naming each of those projects in configuration is pure transcription
— and transcription that goes stale the moment someone creates a project. This
module asks Cloud Resource Manager instead.

**Why a token and not a wildcard.** ``*`` is already taken: a *caller* passes it
to :func:`~integrations.gcp.projects.resolve_projects` to mean "every configured
project". If it also meant "discover" on the config side, the same character
would denote both a set and the act of computing that set, and neither reader
could tell which was intended. Worse, the two are silently compatible — an
operator who writes ``GCP_ADDITIONAL_PROJECTS="*"`` expecting discovery gets a
project whose id is literally ``*``, which passes every local check and then
fails inside Google. ``discover`` cannot be mistaken for a project id.

**Cached for the life of the process, failures included.** The list feeds an
allow-list consulted on every GCP tool call, so re-listing per call would put a
network round trip in front of each one. Caching only successes would be worse
than useless for the failure that actually happens: a service account without
``resourcemanager.projects.list`` would retry, and pay, forever. One attempt per
credential per process; a restart re-attempts, which is the right cadence for a
list that changes when someone creates a project.

**Never widens on failure.** A failed discovery yields the configured projects
alone. The alternative — falling back to something broader — would mean a
transient Google error quietly changed what the agent is allowed to read.
"""

from __future__ import annotations

import hashlib
import logging
import threading
from dataclasses import dataclass, field

from config.constants.gcp import GCP_DISCOVER_PROJECTS_TOKEN
from integrations.config_models import GCPIntegrationConfig
from integrations.gcp.client import (
    RESOURCE_MANAGER_API,
    GCPClientError,
    build_service,
    describe_api_error,
)

logger = logging.getLogger(__name__)

#: One page of Resource Manager results. An estate larger than this is real but
#: rare, and every project here is carried in tool params on each GCP call, so
#: an unbounded list would spend context on projects nobody asked about. When
#: the cap bites it is reported rather than silently applied — see
#: :attr:`DiscoveryResult.truncated`.
MAX_DISCOVERED = 200

#: Only ``ACTIVE`` projects are usable; ``DELETE_REQUESTED`` ones still list.
_ACTIVE = "ACTIVE"


@dataclass(frozen=True)
class DiscoveryResult:
    """Outcome of one Resource Manager listing.

    ``error`` and ``projects`` are not mutually exclusive: a partially
    successful multi-credential expansion reports both.

    ``exception`` is carried rather than reported here on purpose. This module
    is called both from a tool (``gcp_list_projects``, which owes Sentry an
    event tagged with its own name) and from allow-list expansion, which is not
    a tool call and has no name to report under. Reporting centrally would have
    to invent one, and every discovery failure in the process would then be
    attributed to whichever caller the constant named.
    """

    projects: tuple[str, ...] = ()
    error: str = ""
    truncated: bool = False
    exception: Exception | None = None

    @property
    def attempted(self) -> bool:
        """Whether a listing ran at all, as opposed to discovery being off."""
        return bool(self.projects or self.error)


@dataclass
class _Cache:
    """Process-wide memo of discovery per credential.

    The lock guards the dict, deliberately *not* the network call: holding it
    across ``projects.list`` would serialize every GCP tool call in the process
    behind one request, over an httplib2 transport with no per-request timeout.
    A concurrent first call therefore duplicates the listing once and then
    converges — cheap, versus a hung call blocking the whole process.
    """

    entries: dict[str, DiscoveryResult] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def get(self, key: str) -> DiscoveryResult | None:
        with self.lock:
            return self.entries.get(key)

    def put(self, key: str, result: DiscoveryResult) -> None:
        with self.lock:
            self.entries[key] = result

    def clear(self) -> None:
        with self.lock:
            self.entries.clear()


_cache = _Cache()


def wants_discovery(values: object) -> bool:
    """Whether ``values`` asks for live discovery.

    Accepts the shapes ``additional_projects`` travels in: the raw
    comma-separated env string, the validated list, or neither.
    """
    return any(item == GCP_DISCOVER_PROJECTS_TOKEN for item in _as_items(values))


def literal_projects(values: object) -> list[str]:
    """Return ``values`` without the discovery token.

    The token is a directive, not a project id. Letting it reach a project list
    is the exact failure ``"*"`` produces — a name that passes every local check
    and is rejected by Google, in a request that may carry real projects down
    with it.
    """
    return [item for item in _as_items(values) if item != GCP_DISCOVER_PROJECTS_TOKEN]


def _as_items(values: object) -> list[str]:
    """Normalize an env string / list / anything else to stripped entries."""
    if isinstance(values, str):
        values = values.split(",")
    if not isinstance(values, (list, tuple)):
        return []
    return [text for text in (str(item).strip() for item in values) if text]


def _cache_key(config: GCPIntegrationConfig) -> str:
    """Key on the credential alone — that is what decides the visible estate.

    Neither the project id nor the configured extras belong here: two instances
    sharing one service account see the same hierarchy regardless of which slice
    of it each was told about, and keying on them would re-list per instance for
    an identical answer.

    Hashed so a service-account key is not held a second time as a dict key,
    where any incidental dump of the cache would print it.
    """
    material = f"{config.service_account_key}\x00{config.impersonate_service_account}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def list_visible_projects(config: GCPIntegrationConfig) -> DiscoveryResult:
    """Ask Cloud Resource Manager what ``config``'s credential can see. Uncached."""
    try:
        service = build_service(config, RESOURCE_MANAGER_API)
        response = service.projects().list(pageSize=MAX_DISCOVERED).execute()
    except GCPClientError as exc:
        return DiscoveryResult(error=str(exc))
    except Exception as exc:  # noqa: BLE001 — every failure degrades to configured-only
        return DiscoveryResult(error=describe_api_error(exc), exception=exc)

    found = tuple(
        str(item.get("projectId", ""))
        for item in (response.get("projects") or [])
        if isinstance(item, dict)
        and item.get("projectId")
        and item.get("lifecycleState", _ACTIVE) == _ACTIVE
    )
    return DiscoveryResult(projects=found, truncated=bool(response.get("nextPageToken")))


def discover(config: GCPIntegrationConfig) -> DiscoveryResult:
    """Cached :func:`list_visible_projects`. One attempt per credential per process."""
    key = _cache_key(config)
    cached = _cache.get(key)
    if cached is not None:
        return cached

    result = list_visible_projects(config)
    fallback_project = config.project_id
    if result.error:
        # Warning, not error: the deployment still works on its configured
        # projects. Logged once because the result — failure included — is
        # cached, so this does not repeat per tool call.
        logger.warning(
            "GCP project discovery failed for the credential covering %s; "
            "continuing with configured projects only (%s)",
            fallback_project or "the default project",
            result.error,
        )
    elif result.truncated:
        logger.warning(
            "GCP project discovery returned more than %d projects; the allow-list is "
            "capped there. Name the projects you need in %s instead.",
            MAX_DISCOVERED,
            "GCP_ADDITIONAL_PROJECTS",
        )
    else:
        logger.info(
            "GCP project discovery: %d project(s) visible to the credential covering %s.",
            len(result.projects),
            fallback_project or "the default project",
        )
    _cache.put(key, result)
    return result


def reset_cache() -> None:
    """Forget every cached listing. For tests, and for a re-read after a regrant."""
    _cache.clear()


__all__ = [
    "MAX_DISCOVERED",
    "DiscoveryResult",
    "discover",
    "list_visible_projects",
    "literal_projects",
    "reset_cache",
    "wants_discovery",
]
