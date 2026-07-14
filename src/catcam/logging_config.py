"""Logging setup: JSON-lines formatting, size-based rotation, and a
defense-in-depth token-redaction filter applied to every record before it
reaches a handler.

All CatCam modules log through a child of the `"catcam"` logger (e.g.
`"catcam.motion"`, `"catcam.recorder"`), so configuring handlers/filters once
on `"catcam"` here covers every module via normal logger propagation.
"""

import json
import logging
import re
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .config import LoggingConfig

# Telegram bot tokens look like "<numeric-id>:<35-ish char secret>", e.g.
# "123456789:AAFabcDEF-01234567890abcdefghijklmno". Matches the token whether
# bare or embedded in a bot API URL (".../bot<token>/sendMessage").
_TOKEN_PATTERN = re.compile(r"\d{6,}:[A-Za-z0-9_-]{30,}")

REDACTED = "<redacted-token>"


def redact(text: str) -> str:
    """Return `text` with any Telegram-bot-token-shaped substring replaced."""
    return _TOKEN_PATTERN.sub(REDACTED, text)


class _JsonFormatter(logging.Formatter):
    """Renders each `LogRecord` as a single JSON-lines object."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload)


class _RedactionFilter(logging.Filter):
    """Flattens and redacts a record's message before any handler sees it.

    Defense in depth alongside "never log the token directly": if some
    exception message or third-party string happens to contain a
    token-shaped substring, it's scrubbed here regardless of the call site.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = ()
        return True


def setup_logging(config: LoggingConfig) -> None:
    """Configure the shared `"catcam"` logger: JSON formatting, a rotating
    file handler (`config.max_bytes` x `config.backup_count`), a console
    handler, and the redaction filter - idempotent, safe to call once at
    startup.
    """
    root = logging.getLogger("catcam")
    root.setLevel(config.level)
    root.handlers.clear()
    root.propagate = False

    formatter = _JsonFormatter()
    # Attached per-handler, not per-logger: a Logger's own `filters` only run
    # for records originating on that exact Logger object, not on the
    # descendant loggers (`catcam.motion`, `catcam.recorder`, ...) that
    # propagate records up to these shared handlers - so the filter has to
    # live on the handlers themselves to see every record.
    redaction_filter = _RedactionFilter()

    log_path = Path(config.file)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        log_path, maxBytes=config.max_bytes, backupCount=config.backup_count
    )
    file_handler.setFormatter(formatter)
    file_handler.addFilter(redaction_filter)
    root.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.addFilter(redaction_filter)
    root.addHandler(console_handler)
