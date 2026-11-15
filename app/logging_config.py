"""Structured JSON logging to stdout, configured once at startup."""

import logging
import os
import sys
import json
from datetime import datetime, timezone

class JsonFormatter(logging.Formatter):
    """Renders records as single-line JSON, promoting `extra` keys to
    fields."""
    # Standard LogRecord attributes we want to ignore
    RESERVED_ATTRS = {
        'args', 'asctime', 'created', 'exc_info', 'exc_text', 'filename',
        'funcName', 'levelname', 'levelno', 'lineno', 'module', 'msecs',
        'message', 'msg', 'name', 'pathname', 'process', 'processName',
        'relativeCreated', 'stack_info', 'taskName', 'thread', 'threadName'
    }

    def format(self, record):
        """Serialize a record, including any non-standard attributes."""
        log = {
            "timestamp": datetime.now(timezone.utc).isoformat().replace(
                "+00:00", "Z"
            ),
            "level": record.levelname,
            "message": record.getMessage(),
            "logger": record.name
        }

        # Find any attributes not in the reserved list
        extra_fields = {
            k: v for k, v in record.__dict__.items()
            if k not in self.RESERVED_ATTRS
        }
        log.update(extra_fields)

        # Reserved attributes are skipped above, so exceptions must be
        # rendered explicitly
        if record.exc_info:
            log["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            log["stack"] = self.formatStack(record.stack_info)

        return json.dumps(log, default=str)

def setup_logging() -> logging.Logger:
    """Install the JSON handler on the root logger and quiet noisy libraries.

    Must run before the first log record is emitted. Returns the application
    logger; components use logging.getLogger("backblaze_exporter.<component>").
    """
    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    b2sdk_level = os.environ.get("B2SDK_LOG_LEVEL", "WARNING").upper()
    urllib3_level = os.environ.get("URLLIB3_LOG_LEVEL", "WARNING").upper()
    werkzeug_level = os.environ.get("WERKZEUG_LOG_LEVEL", "WARNING").upper()

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        handlers=[handler],
        force=True
    )

    logging.getLogger("b2sdk").setLevel(
        getattr(logging, b2sdk_level, logging.WARNING)
    )
    logging.getLogger("urllib3.connectionpool").setLevel(
        getattr(logging, urllib3_level, logging.WARNING)
    )
    logging.getLogger("werkzeug").setLevel(
        getattr(logging, werkzeug_level, logging.WARNING)
    )

    return logging.getLogger("backblaze_exporter")
