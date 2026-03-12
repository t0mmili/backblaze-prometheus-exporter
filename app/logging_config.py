import logging
import os
import sys
import json
from datetime import datetime

class JsonFormatter(logging.Formatter):
    # Standard LogRecord attributes we want to ignore
    RESERVED_ATTRS = {
        'args', 'asctime', 'created', 'exc_info', 'exc_text', 'filename',
        'funcName', 'levelname', 'levelno', 'lineno', 'module', 'msecs',
        'message', 'msg', 'name', 'pathname', 'process', 'processName',
        'relativeCreated', 'stack_info', 'taskName', 'thread', 'threadName'
    }

    def format(self, record):
        log = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
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

        return json.dumps(log)

def setup_logging():
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
