"""Logging helpers for the paperbase MCP bridge."""

from __future__ import annotations

import json
import logging
import logging.config
from datetime import datetime, timezone
from typing import Any


_RESERVED_LOG_RECORD_FIELDS = {
    "args",
    "asctime",
    "created",
    "exc_info",
    "exc_text",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "module",
    "msecs",
    "message",
    "msg",
    "name",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "thread",
    "threadName",
    "taskName",
}


def _coerce_level(level: str) -> str:
    normalized = (level or "INFO").upper()
    return normalized if normalized in {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"} else "INFO"


class StructuredFormatter(logging.Formatter):
    def __init__(self, *, component: str, use_json: bool) -> None:
        super().__init__()
        self._component = component
        self._use_json = use_json

    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "component": getattr(record, "component", self._component),
            "message": message,
            "pid": record.process,
        }
        extras = {
            key: value
            for key, value in record.__dict__.items()
            if key not in _RESERVED_LOG_RECORD_FIELDS and not key.startswith("_")
        }
        if extras:
            payload.update(extras)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        if self._use_json:
            return json.dumps(payload, default=str, sort_keys=True)

        extras_text = " ".join(f"{key}={json.dumps(value, default=str)}" for key, value in extras.items())
        exception_text = f" exception={json.dumps(payload['exception'])}" if "exception" in payload else ""
        suffix = f" {extras_text}" if extras_text else ""
        return (
            f"{payload['timestamp']} {payload['level']} [{payload['component']}] "
            f"{payload['logger']}: {message}{suffix}{exception_text}"
        )


def configure_logging(*, component: str, level: str, log_format: str) -> None:
    normalized_level = _coerce_level(level)
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "paperbase": {
                    "()": "paperbase_plugin.logging_utils.StructuredFormatter",
                    "component": component,
                    "use_json": log_format.lower() == "json",
                }
            },
            "handlers": {
                "default": {
                    "class": "logging.StreamHandler",
                    "formatter": "paperbase",
                    "stream": "ext://sys.stdout",
                }
            },
            "root": {
                "handlers": ["default"],
                "level": normalized_level,
            },
            "loggers": {
                "uvicorn": {"handlers": ["default"], "level": normalized_level, "propagate": False},
                "uvicorn.error": {"handlers": ["default"], "level": normalized_level, "propagate": False},
                "uvicorn.access": {"handlers": ["default"], "level": normalized_level, "propagate": False},
            },
        }
    )
