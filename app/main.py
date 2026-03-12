#!/usr/bin/env python3

import os
import time
import threading
import json_repair
import jsonschema
import prometheus_client as prom
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
from flask import Flask, Response, render_template
from b2sdk.v3 import B2Api, InMemoryAccountInfo
from b2sdk.v3.exception import (
    B2Error, BucketIdNotFound, InvalidAuthToken, Unauthorized,
    B2ConnectionError
)
from app.app_config import (
    APP_NAME, APP_VERSION, B2_SDK_VERSION, B2_BUCKETS_SCHEMA_FILE,
    FLASK_TEMPLATE_FOLDER
)
from app.logging_config import setup_logging

# --- LOGGING SETUP ---

logger = setup_logging()

# --- METRICS SETUP ---

prom.REGISTRY.unregister(prom.PROCESS_COLLECTOR)
prom.REGISTRY.unregister(prom.PLATFORM_COLLECTOR)
prom.REGISTRY.unregister(prom.GC_COLLECTOR)

# Metrics definition
path_count = prom.Gauge(
    "backblaze_b2_path_files_count",
    "Total number of files in the path",
    ['bucket', 'path']
)
path_latest_only = prom.Gauge(
    "backblaze_b2_path_files_latest_version_only",
    "Whether only the latest version (1) or all versions (0) of files are "
    "processed",
    ['bucket']
)
path_last_upload = prom.Gauge(
    "backblaze_b2_path_last_upload_seconds",
    "Unix timestamp of the most recent upload in the path",
    ['bucket', 'path']
)
path_size = prom.Gauge(
    "backblaze_b2_path_size_bytes",
    "Total size of files in the path",
    ['bucket', 'path']
)
scrape_duration = prom.Gauge(
    "backblaze_b2_scrape_duration_seconds",
    "Time taken to fetch stats",
    ['bucket']
)
scrape_last_time = prom.Gauge(
    "backblaze_b2_scrape_last_time_seconds",
    "Unix timestamp of the most recent successful scrape",
    ['bucket']
)
scrape_success = prom.Gauge(
    "backblaze_b2_scrape_success",
    "Whether the last scrape was successful (1) or failed (0)",
    ['bucket']
)

# --- B2 LOGIC ---

def init_b2(key_id: str, key: str) -> B2Api:
    info = InMemoryAccountInfo()
    b2_api = B2Api(info)

    try:
        b2_api.authorize_account(key_id, key, "production")
        logger.info(
            "B2 authorization successful",
            extra={"event": "b2_auth_success"}
        )
        return b2_api

    except (InvalidAuthToken, Unauthorized) as e:
        logger.error(
            "B2 authentication failed",
            extra={"event": "b2_auth_failed", "error": str(e)}
        )
        raise

    except B2ConnectionError as e:
        logger.error(
            "Connection error during B2 authorization",
            extra={"event": "b2_connection_error", "error": str(e)}
        )
        raise

    except B2Error as e:
        logger.error(
            "Unexpected B2 error during authorization",
            extra={"event": "b2_unexpected_error", "error": str(e)}
        )
        raise

def update_b2_metrics(b2_api: B2Api, config: dict) -> None:
    logger.debug(
        "Starting buckets metrics update",
        extra={"event": "metrics_update_started"}
    )

    for bucket_name, paths in config['b2_buckets'].items():
        try:
            start_time = time.time()
            bucket = b2_api.get_bucket_by_name(bucket_name)

            path_latest_only.labels(bucket=bucket_name).set(
                1 if config['b2_latest_only'] else 0
            )

            for path in paths:
                count, size, last_ts = 0, 0, 0
                for file_version, _ in bucket.ls(
                    path=path, latest_only=config['b2_latest_only'],
                    recursive=True
                ):
                    count += 1
                    size += file_version.size
                    last_ts = max(last_ts, file_version.upload_timestamp)

                path_count.labels(bucket=bucket_name, path=path).set(count)
                path_size.labels(bucket=bucket_name, path=path).set(size)
                path_last_upload.labels(bucket=bucket_name, path=path).set(
                    last_ts
                )

                logger.debug(
                    "Path statistics",
                    extra={"event": "path_stats", "bucket": bucket_name,
                        "path": path, "files": count, "size_bytes": size,
                        "last_upload_ts": last_ts
                    }
                )

            duration = time.time() - start_time

            scrape_success.labels(bucket=bucket_name).set(1)
            scrape_duration.labels(bucket=bucket_name).set(duration)
            scrape_last_time.labels(bucket=bucket_name).set(time.time() * 1000)

            if duration > 30:
                logger.warning(
                    "Bucket metrics update is slow",
                    extra={
                        "event": "metrics_update_slow",
                        "bucket": bucket_name,
                        "duration": duration
                    }
                )

            logger.info(
                "Bucket metrics updated",
                extra={
                    "event": "metrics_update_successful", "bucket": bucket_name
                }
            )

        except BucketIdNotFound:
            logger.error(
                "Bucket not found",
                extra={"event": "bucket_not_found", "bucket": bucket_name}
            )
            scrape_success.labels(bucket=bucket_name).set(0)
            raise

        except InvalidAuthToken:
            logger.error(
                "B2 authentication token expired or invalid",
                extra={"event": "b2_token_invalid"}
            )
            scrape_success.labels(bucket=bucket_name).set(0)
            raise

        except B2ConnectionError as e:
            logger.warning(
                "Network error while accessing bucket",
                extra={
                    "event": "b2_network_error", "error": str(e),
                    "bucket": bucket_name
                }
            )
            scrape_success.labels(bucket=bucket_name).set(0)

        except B2Error as e:
            logger.error(
                "B2 API error while processing bucket",
                extra={
                    "event": "b2_api_error", "error": str(e),
                    "bucket": bucket_name
                }
            )
            scrape_success.labels(bucket=bucket_name).set(0)
            raise

        except Exception as e:
            logger.error(
                "Unexpected error during metrics update",
                extra={
                    "event": "metrics_update_unexpected_error",
                    "error": str(e), "bucket": bucket_name
                }
            )
            scrape_success.labels(bucket=bucket_name).set(0)
            raise

    logger.debug(
        "Completed buckets metrics update",
        extra={"event": "metrics_update_completed"}
    )

def collection_loop(config: dict) -> None:
    b2_api = init_b2(config['b2_app_key_id'], config['b2_app_key'])
    while True:
        update_b2_metrics(b2_api, config)
        time.sleep(config['update_interval'])

# --- WEB SERVER ---

def create_app(config: dict) -> Flask:
    app = Flask(__name__, template_folder=str(FLASK_TEMPLATE_FOLDER))

    @app.route('/')
    def welcome():
        return render_template(
            'index.html', app_name=APP_NAME, app_version=APP_VERSION,
            b2_sdk_version=B2_SDK_VERSION,
            paths_count=len(config['b2_buckets']),
            latest_only=config['b2_latest_only'],
            interval=config['update_interval']
        )

    @app.route('/metrics')
    def metrics():
        logger.debug(
            "Prometheus scrape request received",
            extra={"event": "metrics_request_received"}
        )
        return Response(generate_latest(), mimetype=CONTENT_TYPE_LATEST)

    return app

# --- CONFIG & MAIN ---

def get_env_required(name: str) -> str | None:
    value = os.environ.get(name)
    if not value:
        logger.error(
            "Environment variable is not set or empty",
            extra={"event": "config_missing_env", "env_var": name}
        )
        return None
    return value

def verify_and_load_config() -> dict | None:
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

    latest_only = os.environ.get("B2_FILES_LATEST_ONLY", "false").lower() in (
        "true", "1", "yes"
    )
    port = int(os.environ.get("FLASK_PORT", "52000"))
    interval = int(os.environ.get("METRICS_UPDATE_INTERVAL", "3600"))

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

    with open(B2_BUCKETS_SCHEMA_FILE) as fp:
        schema_json = json_repair.loads(fp.read())

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
            "port": port
        }
    )

    return config

config = verify_and_load_config()

if config:
    updater_thread = threading.Thread(
        target=collection_loop, 
        args=(config,), 
        daemon=True
    )
    updater_thread.start()

    logger.info(
        "Metrics update thread started",
        extra={"event": "metrics_update_thread_started"}
    )

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
        exit(1)
