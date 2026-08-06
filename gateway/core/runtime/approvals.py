"""Transport-neutral approval gate for write tools in gateway turns.

Tools registered with ``requires_approval=True`` declare a human-in-the-loop
contract that the CLI honours with a ``Proceed? [Y/n]`` prompt. Chat gateways
honour it by posting Approve / Deny buttons and blocking the tool call until an
authorized member clicks or the request expires.

This module holds the parts that do not depend on a chat transport: the broker
that connects a click to the waiting tool call, the button identifiers, and the
harness hooks. Each transport supplies its own :class:`ApprovalPrompter` — Block
Kit in ``gateway.transports.slack.approvals``, message components in
``gateway.transports.discord.approvals``.
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from core.execution import BeforeToolCallResult, ToolExecutionHooks, ToolExecutionRequest
from gateway.core.runtime.security_audit import audit_security_action

logger = logging.getLogger("gateway")

APPROVE_ACTION_ID = "opensre_approval_approve"
DENY_ACTION_ID = "opensre_approval_deny"

# Never let one approval outlive the turn timeout (240s default): a prompt
# nobody answers should resolve to deny while the turn can still say so.
MAX_APPROVAL_WAIT_SECONDS = 180.0

# Longest argument preview rendered into an approval prompt. Slack allows 3000
# characters per section and Discord 2000 per message, both counting the prompt
# text and the code fence around this, so Discord sets the ceiling.
ARGS_PREVIEW_LIMIT = 1400

# Argument-rendering limits. A value longer than this is reported by length
# instead of pasted; a list longer than this shows a count for the remainder.
_VALUE_LIMIT = 160
_INLINE_STRING_LIMIT = 40
_LIST_ITEM_LIMIT = 8

# Keys tried in order to label one entry of a list of objects.
_ITEM_LABEL_KEYS = ("path", "name", "id", "key", "title")

# Values under keys containing any of these are replaced before display.
_SECRET_KEY_PARTS = (
    "token",
    "secret",
    "password",
    "passwd",
    "credential",
    "api_key",
    "apikey",
    "auth",
)
_REDACTED = "••••"


@dataclass
class _PendingApproval:
    event: threading.Event = field(default_factory=threading.Event)
    approved: bool = False
    decided_by: str = ""
    platform: str = ""
    chat_id: str = ""


class ApprovalBroker:
    """Thread-safe registry connecting button clicks to waiting tool calls."""

    def __init__(self) -> None:
        self._pending: dict[str, _PendingApproval] = {}
        self._lock = threading.Lock()

    def create(
        self,
        *,
        platform: str | None = None,
        chat_id: str | None = None,
    ) -> str:
        approval_id = uuid.uuid4().hex
        with self._lock:
            self._pending[approval_id] = _PendingApproval(
                platform=platform or "",
                chat_id=chat_id or "",
            )
        audit_security_action(
            action="approval.create",
            platform=platform,
            chat_id=chat_id,
            resource_type="approval",
            resource_id=approval_id,
            outcome="pending",
        )
        return approval_id

    def resolve(self, approval_id: str, *, approved: bool, decided_by: str) -> bool:
        """Deliver a click decision; False when unknown/expired/already decided."""
        with self._lock:
            pending = self._pending.get(approval_id)
            if pending is None or pending.event.is_set():
                return False
            pending.approved = approved
            pending.decided_by = decided_by
            pending.event.set()
            platform = pending.platform
            chat_id = pending.chat_id
        audit_security_action(
            action="approval.resolve",
            platform=platform or None,
            chat_id=chat_id or None,
            actor_id=decided_by,
            resource_type="approval",
            resource_id=approval_id,
            outcome="approved" if approved else "denied",
        )
        return True

    def wait(self, approval_id: str, *, timeout: float) -> tuple[bool, str]:
        """Block for a decision; expiry counts as deny. Returns (approved, decided_by)."""
        with self._lock:
            pending = self._pending.get(approval_id)
        if pending is None:
            return (False, "")
        decided = pending.event.wait(timeout)
        with self._lock:
            self._pending.pop(approval_id, None)
        if not decided:
            audit_security_action(
                action="approval.expire",
                platform=pending.platform or None,
                chat_id=pending.chat_id or None,
                resource_type="approval",
                resource_id=approval_id,
                outcome="denied",
            )
            return (False, "")
        return (pending.approved, pending.decided_by)


class ApprovalPrompter(Protocol):
    """Asks one conversation for approval and reports the decision."""

    def request(
        self,
        *,
        call_id: str,
        headline: str,
        reason: str,
        details: str,
        expiry_seconds: float,
    ) -> tuple[bool, str]:
        """Return (approved, id of the member who decided; empty when expired)."""


def tool_headline(tool: Any, tool_name: str, arguments: Mapping[str, Any]) -> str:
    """The action named in the reviewer's terms, or the bare tool name."""
    display = getattr(tool, "approval_display", None)
    if display is None:
        return tool_name
    try:
        return _one_line(display.headline(arguments)) or tool_name
    except Exception:  # pragma: no cover - defensive: never block a write on rendering
        logger.warning("approval headline rendering failed tool=%s", tool_name, exc_info=True)
        return tool_name


def tool_details(tool: Any, tool_name: str, arguments: Mapping[str, Any]) -> str:
    """The tool's own description of the change, or the generic argument summary."""
    display = getattr(tool, "approval_display", None)
    if display is None:
        return arguments_preview(arguments)
    try:
        return _clamp(display.details(arguments))
    except Exception:  # pragma: no cover - defensive: never block a write on rendering
        logger.warning("approval preview rendering failed tool=%s", tool_name, exc_info=True)
        return arguments_preview(arguments)


def approval_tool_hooks(prompter: ApprovalPrompter) -> ToolExecutionHooks:
    """Tool hooks enforcing ``requires_approval`` through chat buttons."""

    def before_tool_call(request: ToolExecutionRequest) -> BeforeToolCallResult | None:
        tool = request.tool
        if not bool(getattr(tool, "requires_approval", False)):
            return None
        tool_name = request.tool_call.name
        approved, decided_by = prompter.request(
            call_id=request.tool_call.id,
            headline=tool_headline(tool, tool_name, request.arguments),
            reason=str(getattr(tool, "approval_reason", "") or ""),
            details=tool_details(tool, tool_name, request.arguments),
            expiry_seconds=float(getattr(tool, "approval_expiry_seconds", 300)),
        )
        if approved:
            return BeforeToolCallResult(approved=True)
        who = f"<@{decided_by}>" if decided_by else "nobody (request expired)"
        return BeforeToolCallResult(
            blocked=True,
            reason=(
                f"The user denied approval for {request.tool_call.name} "
                f"(decision by {who}). Do not retry; tell the user what you "
                "wanted to do and why."
            ),
        )

    return ToolExecutionHooks(before_tool_call=before_tool_call)


def _is_secret_key(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in _SECRET_KEY_PARTS)


def _one_line(value: Any) -> str:
    return " ".join(str(value).split())


def _scalar(value: Any) -> str:
    """One argument value, summarised by length rather than cut mid-word."""
    text = _one_line(value)
    if len(text) <= _VALUE_LIMIT:
        return text
    return f"{text[:_VALUE_LIMIT]}… ({len(text)} chars)"


def _item_detail(item: Mapping[str, Any], *, label_key: str) -> str:
    """The non-label fields of a list entry, as ``[delete=True]`` style notes.

    Long strings are reported by length. A file's whole new contents are the
    reason this exists: the reviewer needs to know a path is being rewritten
    and roughly how much, not to read four kilobytes in a chat message.
    """
    notes: list[str] = []
    for key, value in item.items():
        if key == label_key:
            continue
        if _is_secret_key(str(key)):
            notes.append(f"{key}={_REDACTED}")
        elif isinstance(value, str) and len(value) > _INLINE_STRING_LIMIT:
            notes.append(f"{key} {len(value)} chars")
        else:
            notes.append(f"{key}={_one_line(value)}")
    return f"  [{', '.join(notes)}]" if notes else ""


def _item_line(item: Any) -> str:
    if not isinstance(item, Mapping):
        return _scalar(item)
    for key in _ITEM_LABEL_KEYS:
        label = item.get(key)
        if label is not None and str(label).strip():
            return f"{_one_line(label)}{_item_detail(item, label_key=key)}"
    return _one_line(", ".join(f"{k}={v}" for k, v in item.items()))[:_VALUE_LIMIT]


def _sequence_lines(key: str, value: Sequence[Any]) -> list[str]:
    count = len(value)
    if not count:
        return [f"{key}: (none)"]
    lines = [f"{key}: {count} {'item' if count == 1 else 'items'}"]
    lines.extend(f"  · {_item_line(item)}" for item in value[:_LIST_ITEM_LIMIT])
    if count > _LIST_ITEM_LIMIT:
        lines.append(f"  · … and {count - _LIST_ITEM_LIMIT} more")
    return lines


def _argument_lines(key: str, value: Any) -> list[str]:
    if _is_secret_key(key):
        return [f"{key}: {_REDACTED}"]
    if isinstance(value, (list, tuple)):
        return _sequence_lines(key, value)
    if isinstance(value, Mapping):
        if not value:
            return [f"{key}: (empty)"]
        return [f"{key}: {len(value)} fields ({', '.join(_one_line(k) for k in value)})"]
    return [f"{key}: {_scalar(value)}"]


def arguments_preview(arguments: Mapping[str, Any]) -> str:
    """Render tool arguments as a readable summary of what the call will do.

    One field per line, values summarised rather than dumped: a long body is
    reported by length, and a list gets a bullet per entry keyed on whichever
    of ``path``/``name``/``id`` it carries. JSON was the previous format and it
    failed the one job this has — the interesting field is usually last, so a
    length cap ate exactly the part the reviewer needed to see.

    Arguments here are model-supplied (credentials are injected downstream, in
    ``core.execution``), but a model may pass an explicit token override, so
    secret-looking keys are still redacted before this reaches a channel.
    """
    if not arguments:
        return ""
    try:
        lines = [
            line for key, value in arguments.items() for line in _argument_lines(str(key), value)
        ]
    except Exception:  # pragma: no cover - defensive: never block a write on rendering
        logger.warning("approval preview rendering failed", exc_info=True)
        return "(arguments could not be rendered; see server logs)"

    kept: list[str] = []
    used = 0
    for index, line in enumerate(lines):
        if used + len(line) + 1 > ARGS_PREVIEW_LIMIT:
            kept.append(f"… {len(lines) - index} more line(s) not shown")
            break
        kept.append(line)
        used += len(line) + 1
    return "\n".join(kept)


def _clamp(text: str) -> str:
    """Hold any preview inside the transport budget, on a line boundary.

    A tool renders its own prompt, so the length guarantee has to be enforced
    here — a verbose renderer must not be able to overflow a Slack section or
    have Discord silently drop the buttons off the end of the message.
    """
    if len(text) <= ARGS_PREVIEW_LIMIT:
        return text
    kept = text[:ARGS_PREVIEW_LIMIT].rsplit("\n", 1)[0]
    return f"{kept}\n… {len(text) - len(kept)} more character(s) not shown"


__all__ = [
    "APPROVE_ACTION_ID",
    "ARGS_PREVIEW_LIMIT",
    "DENY_ACTION_ID",
    "MAX_APPROVAL_WAIT_SECONDS",
    "ApprovalBroker",
    "ApprovalPrompter",
    "approval_tool_hooks",
    "arguments_preview",
    "tool_details",
    "tool_headline",
]
