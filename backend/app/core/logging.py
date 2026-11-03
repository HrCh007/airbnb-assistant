"""Logging configuration with redaction of secrets and sensitive values."""

from __future__ import annotations

import logging
import re

_REDACT_PATTERNS = [
    re.compile(r"(sk-ant-[A-Za-z0-9_\-]{6})[A-Za-z0-9_\-]+"),
    re.compile(r"(sk-[A-Za-z0-9]{4})[A-Za-z0-9_\-]{10,}"),
    re.compile(r"(AIza[0-9A-Za-z_\-]{4})[0-9A-Za-z_\-]{20,}"),
    re.compile(r"(?i)(password[\"']?\s*[:=]\s*[\"']?)[^\s\"',}]+"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9\-_\.]+"),
]


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        try:
            msg = record.getMessage()
        except Exception:  # pragma: no cover - defensive
            return True
        redacted = msg
        for pat in _REDACT_PATTERNS:
            redacted = pat.sub(r"\1***", redacted)
        if redacted != msg:
            record.msg = redacted
            record.args = ()
        return True


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
    )
    handler.addFilter(RedactingFilter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    # Keep noisy libraries quiet; never log request bodies.
    for noisy in ("httpx", "httpx2", "httpcore", "anthropic", "openai", "sqlalchemy.engine"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
