"""Logging configuration for the Telegram gateway process."""

from __future__ import annotations

import logging

from config.constants.logging import resolve_log_level

# Third-party libraries that become very noisy at INFO during normal gateway
# operation (long-poll getUpdates, Telegram send/edit, OpenAI completions).
_QUIET_LOGGER_NAMES = (
    "httpx",
    "httpcore",
    "openai",
)

# Routine authorized inbound audits are still emitted at INFO for other surfaces
# (Hermes, ops tooling) but are hidden in the dedicated gateway process.
_ROUTINE_AUDIT_MARKER = "authorized=True"


class _GatewayLogFormatter(logging.Formatter):
    """Present gateway package logs under a single short logger name."""

    def format(self, record: logging.LogRecord) -> str:
        if record.name == "gateway" or record.name.startswith("gateway."):
            record.name = "gateway"
        return super().format(record)


class _GatewayProcessLogFilter(logging.Filter):
    """Drop high-volume success-path noise from the gateway terminal."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.name == "integrations.messaging_security" and record.levelno <= logging.INFO:
            message = record.getMessage()
            if _ROUTINE_AUDIT_MARKER in message:
                return False
        return True


def _quiet_noisy_loggers() -> None:
    for name in _QUIET_LOGGER_NAMES:
        logging.getLogger(name).setLevel(logging.WARNING)


def configure_logging() -> logging.Logger:
    """Configure root logging for the dedicated Telegram gateway process."""
    gateway_logger = logging.getLogger("gateway")
    configured_level = resolve_log_level()

    if not logging.getLogger().handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            _GatewayLogFormatter(
                fmt="%(asctime)s %(levelname)-5s %(name)s | %(message)s",
                datefmt="%H:%M:%S",
            )
        )
        handler.addFilter(_GatewayProcessLogFilter())
        logging.basicConfig(
            level=logging.INFO if configured_level is None else configured_level,
            handlers=[handler],
        )
    elif configured_level is not None:
        # Another host already owns the handlers, so ``basicConfig`` is a no-op
        # and the knob would silently do nothing. Only touch the level when the
        # operator actually asked for one — otherwise leave that host's setup be.
        logging.getLogger().setLevel(configured_level)

    _quiet_noisy_loggers()
    return gateway_logger
