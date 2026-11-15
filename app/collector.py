"""Backblaze B2 metrics collector.

Owns the Prometheus metrics, the B2 session and the background collection loop.
The loop is designed never to terminate: transient failures (DNS, network,
expired token, B2 5xx) are retried with backoff and per-bucket failures are
isolated so one bad bucket cannot stop the others.
"""

import logging
import math
import random
import threading
import time

import prometheus_client as prom
from b2sdk.v3 import B2Api, InMemoryAccountInfo
from b2sdk.v3.exception import (
    B2Error, BucketIdNotFound, InvalidAuthToken, Unauthorized,
    B2ConnectionError
)

from app.app_config import (
    RETRY_BACKOFF_INITIAL_SECONDS, RETRY_BACKOFF_MAX_SECONDS,
    CYCLE_JITTER_FRACTION, CYCLE_MIN_SLEEP_SECONDS,
    SLOW_SCRAPE_THRESHOLD_SECONDS
)

# Child of the logger configured in main; inherits handlers and level.
logger = logging.getLogger("backblaze_exporter.collector")

# --- METRICS SETUP ---

prom.REGISTRY.unregister(prom.PROCESS_COLLECTOR)
prom.REGISTRY.unregister(prom.PLATFORM_COLLECTOR)
prom.REGISTRY.unregister(prom.GC_COLLECTOR)

# B2 Bucket / path metrics
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
    "backblaze_b2_path_last_upload_timestamp_seconds",
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
scrape_last_success = prom.Gauge(
    "backblaze_b2_scrape_last_success_timestamp_seconds",
    "Unix timestamp of the most recent successful scrape of the bucket",
    ['bucket']
)
scrape_success = prom.Gauge(
    "backblaze_b2_scrape_success",
    "Whether the last scrape was successful (1) or failed (0)",
    ['bucket']
)

# Collector health metrics
collector_up = prom.Gauge(
    "backblaze_b2_collector_up",
    "Whether the collection loop currently holds a valid B2 authorization (1) "
    "or not (0)"
)
collector_last_success = prom.Gauge(
    "backblaze_b2_collector_last_success_timestamp_seconds",
    "Unix timestamp of the most recent cycle that scraped at least one bucket"
)
collector_errors = prom.Counter(
    "backblaze_b2_collector_errors_total",
    "Total number of collection errors, by reason",
    ['reason']
)

# --- COLLECTOR STATE ---

class CollectorState:
    """Thread-safe view of collector liveness."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self.last_iteration_ts: float = time.time()
        self.last_success_ts: float | None = None
        self.authorized = False
        self.fatal = False
        self.last_error: str | None = None

    def register_thread(self, thread: threading.Thread) -> None:
        with self._lock:
            self._thread = thread

    def mark_iteration(self, ts: float) -> None:
        """Liveness heartbeat: ticks every cycle, successful or not."""
        with self._lock:
            self.last_iteration_ts = ts

    def mark_authorized(self) -> None:
        with self._lock:
            self.authorized = True
            self.fatal = False

    def mark_success(self, ts: float) -> None:
        with self._lock:
            self.last_success_ts = ts
            self.last_error = None

    def mark_failure(self, reason: str, error: str, fatal: bool) -> None:
        with self._lock:
            self.authorized = False
            self.fatal = fatal
            self.last_error = f"{reason}: {error}"

    def snapshot(self) -> dict:
        """Atomic copy of the state, for the health endpoints."""
        with self._lock:
            return {
                "thread_alive": bool(self._thread and self._thread.is_alive()),
                "last_iteration_ts": self.last_iteration_ts,
                "last_success_ts": self.last_success_ts,
                "authorized": self.authorized,
                "fatal": self.fatal,
                "last_error": self.last_error,
            }

collector_state = CollectorState()

# --- INTERNAL EXCEPTIONS ---

class FatalAuthError(Exception):
    """B2 rejected the credentials; retrying will not help on its own."""

class B2ReauthRequired(Exception):
    """The session token is no longer usable and must be re-created."""

# --- DERIVED TIMING ---

def freshness_deadline(interval: int) -> int:
    """How stale data may get before the exporter reports itself unhealhy.

    Two missed cycles, floored at 5 minutes.
    """
    return max(2 * interval, 300)

def _next_cycle_sleep(anchor: float, interval: int) -> float:
    """Seconds until the next cycle on the gird `anchor + n * interval`.

    Monotonic and anchored, so neither scrape duration nor jitter accumulates.
    """
    now = time.monotonic()
    target = anchor + (
        math.floor((now - anchor) / interval) + 1
    ) * interval
    target += random.uniform(-1, 1) * CYCLE_JITTER_FRACTION * interval
    return max(CYCLE_MIN_SLEEP_SECONDS, target - now)

# --- B2 LOGIC ---

def init_b2(key_id: str, key: str) -> B2Api:
    """Create and authorize a B2 session.

    Raises FatalAuthError if the credentials are rejected; any other B2Error
    propagates and is retireds by the caller.
    """
    b2_api = B2Api(InMemoryAccountInfo())

    try:
        b2_api.authorize_account(key_id, key, "production")

    except (InvalidAuthToken, Unauthorized) as e:
        # Credentials themselves are bad. Not retryable on its own.
        raise FatalAuthError(str(e)) from e

    logger.info(
        "B2 authorization successful",
        extra={"event": "b2_auth_success"}
    )
    return b2_api

def init_metrics(config: dict) -> None:
    """Publish the static and health series before the first scrape.

    scrape_success=0 up front is what makes a failed startup visible to
    Prometheus instead of appearing as missing series.
    """
    collector_up.set(0)

    for bucket_name in config['b2_buckets']:
        scrape_success.labels(bucket=bucket_name).set(0)
        path_latest_only.labels(bucket=bucket_name).set(
            1 if config['b2_latest_only'] else 0
        )

def update_bucket_metrics(
    b2_api: B2Api, bucket_name: str, paths: list, latest_only: bool
) -> None:
    """Scrape one bucket and publish its metrics.

    Handles no exceptions: the caller owns the retry and isolation policy.
    On failure the bucket's previous path_* values are left untouched, with
    scrape_success=0 marking them stale.
    """
    start_time = time.time()
    bucket = b2_api.get_bucket_by_name(bucket_name)

    for path in paths:
        count, size, last_ts = 0, 0, 0
        for file_version, _ in bucket.ls(
            path=path, latest_only=latest_only, recursive=True
        ):
            count += 1
            size += file_version.size
            last_ts = max(last_ts, file_version.upload_timestamp)

        path_count.labels(bucket=bucket_name, path=path).set(count)
        path_size.labels(bucket=bucket_name, path=path).set(size)
        path_last_upload.labels(bucket=bucket_name, path=path).set(
            last_ts / 1000 if last_ts else 0
        )

        logger.debug(
            "Path statistics",
            extra={
                "event": "path_stats", "bucket": bucket_name, "path": path,
                "files": count, "size_bytes": size,
                "last_upload_ts": last_ts
            }
        )

    duration = time.time() - start_time

    scrape_success.labels(bucket=bucket_name).set(1)
    scrape_duration.labels(bucket=bucket_name).set(duration)
    scrape_last_success.labels(bucket=bucket_name).set(time.time())

    if duration > SLOW_SCRAPE_THRESHOLD_SECONDS:
        logger.warning(
            "Bucket metrics update is slow",
            extra={
                "event": "metrics_update_slow", "bucket": bucket_name,
                "duration": duration
            }
        )

    logger.info(
        "Bucket metrics updated",
        extra={"event": "metrics_update_successful", "bucket": bucket_name}
    )


def update_b2_metrics(b2_api: B2Api, config: dict) -> tuple[int, int]:
    """Scrape every configured bucket.

    Returns (succeeded, failed) bucket counts.
    """
    succeeded, failed = 0, 0

    for bucket_name, paths in config['b2_buckets'].items():
        try:
            update_bucket_metrics(
                b2_api, bucket_name, paths, config['b2_latest_only']
            )
            succeeded += 1
            continue

        except InvalidAuthToken as e:
            # Session token expired mid-run. Re-authorize instead of dying.
            scrape_success.labels(bucket=bucket_name).set(0)
            raise B2ReauthRequired(str(e)) from e

        except Unauthorized as e:
            reason, level, error = "unauthorized", logging.ERROR, str(e)
        except BucketIdNotFound as e:
            reason, level, error = "bucket_not_found", logging.ERROR, str(e)
        except B2ConnectionError as e:
            reason, level, error = "connection_error", logging.WARNING, str(e)
        except B2Error as e:
            reason, level, error = "b2_api_error", logging.ERROR, str(e)
        except Exception as e:
            reason, level, error = "unexpected_error", logging.ERROR, str(e)
            logger.exception(
                "Unexpected error while processing bucket",
                extra={
                    "event": "bucket_unexpected_error", "bucket": bucket_name
                },
            )

        scrape_success.labels(bucket=bucket_name).set(0)
        collector_errors.labels(reason=reason).inc()
        failed += 1
        logger.log(
            level, "Bucket metrics update failed",
            extra={
                "event": "bucket_update_failed", "reason": reason,
                "bucket": bucket_name, "error": error
            }
        )

    return succeeded, failed

def collection_loop(config: dict) -> None:
    """Run forever. Every failure path leads back to the top of the loop."""
    interval = config['update_interval']
    backoff_max = min(RETRY_BACKOFF_MAX_SECONDS, interval)
    backoff = min(RETRY_BACKOFF_INITIAL_SECONDS, backoff_max)
    anchor = time.monotonic()
    b2_api = None

    init_metrics(config)

    while True:
        cycle_started = time.monotonic()
        collector_state.mark_iteration(time.time())
        failure = None

        try:
            if b2_api is None:
                b2_api = init_b2(
                    config['b2_app_key_id'], config['b2_app_key']
                )
                collector_up.set(1)
                collector_state.mark_authorized()

            succeeded, failed = update_b2_metrics(b2_api, config)

            if succeeded:
                now = time.time()
                collector_state.mark_success(now)
                collector_last_success.set(now)
            else:
                failure = {
                    "reason": "all_buckets_failed", "level": logging.WARNING,
                    "error": f"{failed} bucket(s) failed, none succeeded",
                    "fatal": False
                }

        except FatalAuthError as e:
            failure = {
                "reason": "auth_rejected", "level": logging.ERROR,
                "error": str(e), "fatal": True
            }

        except (B2ReauthRequired, B2ConnectionError, B2Error) as e:
            failure = {
                "reason": "b2_unavailable", "level": logging.WARNING,
                "error": str(e), "fatal": False
            }

        except Exception as e:
            logger.exception(
                "Unexpected error in collection loop",
                extra={"event": "collection_loop_unexpected_error"}
            )
            failure = {
                "reason": "unexpected_error", "level": logging.ERROR,
                "error": str(e), "fatal": False
            }

        if time.monotonic() - cycle_started >= interval:
            logger.warning(
                "Collection cycle overran the update interval",
                extra={
                    "event": "collection_cycle_overrun",
                    "cycle_duration": round(
                        time.monotonic() - cycle_started, 1
                    ),
                    "update_interval": interval
                }
            )

        if failure is None:
            backoff = min(RETRY_BACKOFF_INITIAL_SECONDS, backoff_max)
            sleep_for = _next_cycle_sleep(anchor, interval)
        else:
            # Single place that handles every failure
            b2_api = None
            collector_up.set(0)
            collector_errors.labels(reason=failure['reason']).inc()
            collector_state.mark_failure(
                failure['reason'], failure['error'], failure['fatal']
            )
            if failure['fatal']:
                for bucket_name in config['b2_buckets']:
                    scrape_success.labels(bucket=bucket_name).set(0)

            retry_in = backoff_max if failure['fatal'] else backoff
            sleep_for = max(
                CYCLE_MIN_SLEEP_SECONDS, retry_in * random.uniform(0.8, 1.2)
            )
            backoff = min(backoff * 2, backoff_max)

            logger.log(
                failure['level'], "Collection cycle failed, will retry",
                extra={
                    "event": "collection_cycle_failed",
                    "reason": failure['reason'], "error": failure['error'],
                    "retry_in_seconds": round(sleep_for, 1)
                }
            )

        time.sleep(sleep_for)

def start_collector(config: dict) -> threading.Thread:
    """Start the collection loop in a background thread."""
    interval = config['update_interval']

    logger.info(
        "Collector timing",
        extra={
            "event": "collector_timing",
            "update_interval": interval,
            "backoff_initial": min(RETRY_BACKOFF_INITIAL_SECONDS, interval),
            "backoff_max": min(RETRY_BACKOFF_MAX_SECONDS, interval),
            "jitter_fraction": CYCLE_JITTER_FRACTION,
            "freshness_deadline": freshness_deadline(interval),
        }
    )

    thread = threading.Thread(
        target=collection_loop, args=(config,), daemon=True,
        name="b2-collector"
    )
    thread.start()
    collector_state.register_thread(thread)

    logger.info(
        "Metrics update thread started",
        extra={"event": "metrics_update_thread_started"}
    )
    return thread
