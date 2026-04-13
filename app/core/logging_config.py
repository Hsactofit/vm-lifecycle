"""Structured logging setup.

Outputs
────────
  stdout   → always (12-factor app standard; container log drivers pick this up)
  log file → when LOG_FILE is set (rotating, configurable size & backups)

Format
───────
  json → one JSON object per line (production default)
         Fields: timestamp, level, logger, message + any extra fields
         (vm_id, request_id, …) promoted to top-level for log aggregators.
  text → human-readable (local development)

File rotation
──────────────
  LOG_FILE        → path to the log file (e.g. ./logs/app.log)
  LOG_MAX_BYTES   → max file size before rotation (default 10 MB)
  LOG_BACKUP_COUNT → number of rotated files to keep (default 5)

  With defaults, you get up to 50 MB of logs on disk before the oldest
  is deleted. Adjust to match your retention/alerting requirements.
"""
import json
import logging
import logging.handlers
import sys
from datetime import datetime, timezone
from pathlib import Path

# Standard LogRecord attributes that should NOT be promoted to JSON top-level.
_STANDARD_LOG_ATTRS = frozenset(
    {
        "args", "asctime", "created", "exc_info", "exc_text",
        "filename", "funcName", "levelname", "levelno", "lineno",
        "message", "module", "msecs", "msg", "name", "pathname",
        "process", "processName", "relativeCreated", "stack_info",
        "taskName", "thread", "threadName",
    }
)


class JSONFormatter(logging.Formatter):
    """Emit one JSON object per log record.

    Any key passed via the `extra={}` argument to logger.info/warning/etc.
    (e.g. vm_id, request_id, duration_ms) is promoted to a top-level field
    so log aggregators can index and query it without regex parsing.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        for key, value in record.__dict__.items():
            if key not in _STANDARD_LOG_ATTRS and not key.startswith("_"):
                payload[key] = value

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str)


def _build_formatter(log_format: str) -> logging.Formatter:
    if log_format == "json":
        return JSONFormatter()
    return logging.Formatter("%(asctime)s  %(levelname)-8s  %(name)s  %(message)s")


def setup_logging() -> None:
    """Configure root logger. Called once inside FastAPI's lifespan hook."""
    from app.core.config import get_settings

    settings = get_settings()
    level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)
    formatter = _build_formatter(settings.LOG_FORMAT)

    handlers: list[logging.Handler] = []

    # ── stdout handler (always present) ──────────────────────────────────────
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(formatter)
    handlers.append(stdout_handler)

    # ── rotating file handler (optional) ─────────────────────────────────────
    if settings.LOG_FILE:
        log_path = Path(settings.LOG_FILE)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        file_handler = logging.handlers.RotatingFileHandler(
            filename=log_path,
            maxBytes=settings.LOG_MAX_BYTES,
            backupCount=settings.LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        handlers.append(file_handler)

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    for h in handlers:
        root.addHandler(h)

    # Prevent uvicorn's per-request access log from doubling our request logs.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

    if settings.LOG_FILE:
        logging.getLogger(__name__).info(
            "logging_configured",
            extra={
                "level": settings.LOG_LEVEL,
                "format": settings.LOG_FORMAT,
                "log_file": settings.LOG_FILE,
                "max_bytes": settings.LOG_MAX_BYTES,
                "backup_count": settings.LOG_BACKUP_COUNT,
            },
        )
