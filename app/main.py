"""Exporter entrypoint: loads configuration, starts the collector, serves HTTP.

The /metrics endpoint only reads values cached by the background collector, so
Prometheus scrapes never call the B2 API.
"""

#!/usr/bin/env python3

import os
import sys
import time
import json
import json_repair
import jsonschema

from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
from b2sdk.version import VERSION as B2_SDK_VERSION
from flask import Flask, Response, jsonify, render_template

from app.app_config import (
    APP_NAME, APP_VERSION, B2_BUCKETS_SCHEMA_FILE, FLASK_TEMPLATE_FOLDER
)
from app.logging_config import setup_logging
from app.collector import collector_state, freshness_deadline, start_collector

# --- LOGGING SETUP ---

logger = setup_logging()

# --- WEB SERVER ---

def create_app(config: dict) -> Flask:
    """Build the Flask app with the landing page, metrics and health routes."""
    app = Flask(__name__, template_folder=str(FLASK_TEMPLATE_FOLDER))
    deadline = freshness_deadline(config['update_interval'])

    @app.route('/')
    def welcome():
        """Landing page with version, configuration and endpoint links."""
        return render_template(
            'index.html', app_name=APP_NAME, app_version=APP_VERSION,
            b2_sdk_version=B2_SDK_VERSION,
            buckets_count=len(config['b2_buckets']),
            latest_only=config['b2_latest_only'],
            interval=config['update_interval'],
            deadline=deadline
        )

    @app.route('/metrics')
    def metrics():
        """Metrics: expose Prometheus metrics."""
        logger.debug(
            "Prometheus scrape request received",
            extra={"event": "metrics_request_received"}
        )

        return Response(generate_latest(), mimetype=CONTENT_TYPE_LATEST)

    @app.route('/healthz')
    def healthz():
        """Liveness: is the collector thread alive and iterating."""
        snap = collector_state.snapshot()
        stalled_for = time.time() - snap['last_iteration_ts']
        healthy = snap['thread_alive'] and stalled_for <= deadline

        return jsonify({
            "status": "ok" if healthy else "unhealthy",
            "collector_thread_alive": snap['thread_alive'],
            "seconds_since_last_iteration": round(stalled_for, 1),
            "deadline_seconds": deadline, "last_error": snap['last_error'],
        }), (200 if healthy else 503)

    @app.route('/readyz')
    def readyz():
        """Readiness: is there fresh B2 data behind this process."""
        snap = collector_state.snapshot()
        last = snap['last_success_ts']
        age = None if last is None else time.time() - last
        ready = snap['authorized'] and age is not None and age <= deadline

        return jsonify({
            "status": "ready" if ready else "not_ready",
            "authorized": snap['authorized'],
            "credentials_rejected": snap['fatal'],
            "seconds_since_last_success": (
                None if age is None else round(age, 1)
            ),
            "deadline_seconds": deadline,
            "last_error": snap['last_error'],
        }), (200 if ready else 503)

    return app

# --- CONFIG & MAIN ---

def get_env_required(name: str) -> str | None:
    """Read a mandatory environment variable, logging and returning None if
    unset."""
    value = os.environ.get(name)
    if not value:
        logger.error(
            "Environment variable is not set or empty",
            extra={"event": "config_missing_env", "env_var": name}
        )
        return None
    return value

def get_env_int(name: str, default: int, minimum: int = 0) -> int:
    """Read an integer environment variable, falling back to default and
    clamping."""
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning(
            "Environment variable is not a valid integer, using default",
            extra={
                "event": "config_invalid_int", "env_var": name, "value": raw,
                "default": default
            }
        )
        return default
    if value < minimum:
        logger.warning(
            "Environment variable below minimum, clamping",
            extra={
                "event": "config_value_clamped", "env_var": name,
                "value": value, "minimum": minimum
            }
        )
        return minimum
    return value

def verify_and_load_config() -> dict | None:
    """Load and validate configuration from the environment.

    Returns the config dict, or None if anything is missing or invalid; the
    caller is expected to exit non-zero in that case.
    """
    key_id = get_env_required("B2_APPLICATION_KEY_ID")
    buckets_raw = get_env_required("B2_BUCKETS_CONFIG")

    if not key_id or not buckets_raw:
        return None

    key = os.environ.get("B2_APPLICATION_KEY")

    if not key:
        key_file = get_env_required("B2_APPLICATION_KEY_FILE")

        if key_file:
            try:
                with open(key_file) as fp:
                    key = fp.readline().strip()
            except OSError as e:
                logger.error(
                    "Failed to read B2 application key file",
                    extra={
                        "event": "config_app_key_file_error", "error": str(e)
                    }
                )
                return None
        else:
            return None

        if not key:
            logger.error(
                "B2 application key file is empty",
                extra={"event": "config_app_key_file_empty"}
            )
            return None

    latest_only = os.environ.get("B2_FILES_LATEST_ONLY", "false").lower() in (
        "true", "1", "yes"
    )
    port = get_env_int("FLASK_PORT", 52000, minimum=1)
    interval = get_env_int("METRICS_UPDATE_INTERVAL", 3600, minimum=1)

    buckets_json = json_repair.loads(buckets_raw)

    if not isinstance(buckets_json, dict):
        logger.error(
            "Buckets config is not a valid JSON object",
            extra={
                "event": "config_invalid_buckets_json",
                "env_var": "B2_BUCKETS_CONFIG"
            }
        )
        return None

    try:
        with open(B2_BUCKETS_SCHEMA_FILE) as fp:
            schema_json = json.load(fp)
    except (OSError, json.JSONDecodeError) as e:
        logger.error(
            "Failed to read buckets config schema",
            extra={"event": "config_schema_read_error", "error": str(e)}
        )
        return None

    if not isinstance(schema_json, dict):
        logger.error(
            "Buckets config schema is not a valid JSON object",
            extra={"event": "config_invalid_schema_json"}
        )
        return None

    try:
        jsonschema.validate(buckets_json, schema_json)
    except jsonschema.ValidationError as e:
        logger.error(
            "Buckets config validation failed",
            extra={
                "event": "config_validation_failed", "error": e.message,
                "path": list(e.path)
            }
        )
        return None

    config = {
        "b2_app_key_id": key_id,
        "b2_app_key": key,
        "b2_buckets": buckets_json,
        "b2_latest_only": latest_only,
        "port": port,
        "update_interval": interval,
    }

    logger.debug(
        "Exporter configuration loaded",
        extra={
            "event": "config_loaded",
            "buckets": list(buckets_json.keys()),
            "paths_total": sum(len(p) for p in buckets_json.values()),
            "latest_only": latest_only,
            "update_interval": interval,
            "port": port,
        }
    )

    return config

config = verify_and_load_config()

if not config:
    logger.error(
        "Exporter failed to start due to configuration errors",
        extra={"event": "exporter_startup_failed"}
    )
    sys.exit(1)

start_collector(config)
app = create_app(config)

logger.info(
    "Exporter started",
    extra={
        "event": "exporter_startup_successful",
        "exporter_version": APP_VERSION,
        "b2_sdk_version": B2_SDK_VERSION
    }
)

if __name__ == "__main__":
    if config:
        app.run(host='0.0.0.0', port=config['port'], debug=False)
    else:
        logger.error(
            "Exporter failed to start due to configuration errors",
            extra={"event": "exporter_startup_failed"}
        )
        sys.exit(1)
