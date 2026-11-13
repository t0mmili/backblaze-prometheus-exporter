# Backblaze Prometheus Exporter

![Docker Pulls](https://img.shields.io/docker/pulls/t0mmili/backblaze-prometheus-exporter)
![Docker Image Size](https://img.shields.io/docker/image-size/t0mmili/backblaze-prometheus-exporter)
![GitHub License](https://img.shields.io/github/license/t0mmili/backblaze-prometheus-exporter)
![GitHub Release](https://img.shields.io/github/release/t0mmili/backblaze-prometheus-exporter)

Export Prometheus metrics for Backblaze B2 buckets and paths, including file count, total size, and last upload timestamp.

Supports monitoring multiple buckets and paths with background data collection to ensure high performance.

The background collector is resilient by design: transient B2 failures (DNS, network, expired session token, API errors) are retired with exponential backoff, and a failure on one bucket never stops the others.

## :bar_chart: Metrics

### Backblaze B2

- `backblaze_b2_path_files_count` - Gauge: Total number of files in the path.
- `backblaze_b2_path_files_latest_version_only` - Gauge: Whether only the latest version (1) or all versions (0) of files are processed.
- `backblaze_b2_path_last_upload_timestamp_seconds` - Gauge: Unix timestamp (seconds) of the most recent upload in the path. `0` means the path contains no files
- `backblaze_b2_path_size_bytes` - Gauge: Total size of files in the path.

### Exporter

- `backblaze_b2_scrape_duration_seconds` - Gauge: Time taken to fetch stats for a bucket.
- `backblaze_b2_scrape_last_success_timestamp_seconds` - Gauge: Unix timestamp (seconds) of the most recent successful scrape of the bucket.
- `backblaze_b2_scrape_success` - Gauge: Whether the last scrape was successful (1) or failed (0).

### Collector
- `backblaze_b2_collector_up` - Gauge: Wheather the collection loop currently holds a valid B2 authorization (1) or not (0).
- `backblaze_b2_collector_last_success_timestamp_seconds` - Gauge: Unix timestamp (seconds) of the most recent cycle that scraped at least one bucket.
- `backblaze_b2_collector_errors_total` - Gauge: Collection errors by `reason`. Per-bucket reasons (`bucket_not_found`, `unauthorized`, `connection_error`, `b2_api_error`, `unexpected_error`) and per-cycle reasons (`auth_rejected`, `all_buckets_failed`, `b2_unavailable`) are both counted here, so one cycle contribute several increments.

### :rotating_light: Example Alert

```promql
# The exporter is serving, but the collector cannot reach or authorize to B2
backblaze_b2_collector_up == 0

# Data is stale (default interval 3600s -> deadline 7200s)
time() - backblaze_b2_collector_last_success_timestamp_seconds > 7200

# A single bucket is failing while the rest work
backblaze_b2_scrape_success == 0

# No upload in a path for 24h
time() - (backblaze_b2_path_last_upload_timestamp_seconds)
```

## :heartbeat: Health Endpoints

| Endpoint | Question it answers | Status 200 | Status 503 |
| --- | --- | --- | --- |
| `/healthz` | Liveness: is the collector thread alive and iterating? | Thread alive and last iteration within the deadline | Thread dead, or no iteration within the deadline |
| `/readyz` | Readiness: is there fresh B2 data behind this process? | Authorized and last success within the deadline | Not authorized, never succeeded, or data stale |

`/healthz` deliberately does **not** depend on B2 being reachable, so an upstream outage cannot cause a restart loop.

`/readyz` does, so an exporter with stale data is removed from service while the pod keeps running and retrying.

> [!CAUTION]
> Do not point probes at `/`. The landing page always returns 200 regardless of collector state.

## :stopwatch: Understanding Intervals

It is important to distinguish between how Prometheus collects data and how this exporter fetches data from Backblaze:

- `scrape_interval` **(Prometheus)**: This defines how often Prometheus hits the `/metrics` endpoint. Because this exporter uses a background thread, the `/metrics` endpoint responds instantly with cached data.
- `METRICS_UPDATE_INTERVAL` **(Exporter)**: This defines how often the background thread actually calls the Backblaze B2 API.

`METRICS_UPDATE_INTERVAL` is the only timing knob - retry and health behaviour are derived from it:

| Derived value | Rule | Default (interval = 3600) |
| --- | --- | --- |
| First retry delay | `min(5, interval)` s | 5 s |
| Maximum retry delay | `min(300, interval)` s | 300 s |
| Health deadline (`/healthz`, `/readyz`) | `max(2 × interval, 300)` s | 7200 s |
| Cycle jitter | ±5% of the interval | ±180 s |

Cycles are scheduled on a fixed grid (`start + n × interval`) on the monotonic clock, so scrape duration and jitter do not accumulate: the cadence stays on schedule even after a long outage. Jitter keeps multiple replicas from hitting the B2 API in lockstep.

## :open_file_folder: Bucket and Path Configuration

`B2_BUCKETS_CONFIG` is a JSON object mapping bucket names to a list of paths, validated at startup against schema.

```json
{
  "my-backups": ["postgres", "etcd-snapshots"],
  "my-archive": [""]
}
```

Rules:

- At least **one bucket** and at least **one path** per bucket. An empty object or an empty path list is rejected at startup.
- Use `""` to cover the **whole bucket**.
- Paths are prefixes within the bucket and must **not start** with `/`.
- Paths must be **unique** per bucket.
- Overlapping paths (for example `backups` and `backups/db`) are allowed. Files in the nested path are counted in both.

## :rocket: Production Deployment

The Docker image uses **Gunicorn** as the WSGI server, with threaded worker model.

Gunicorn is configured via the `GUNICORN_CMD_ARGS` environment variable.

> [!CAUTION]
> The exporter is designed to run with a single worker (`--workers=1`). Do not increase the number of workers.  
> Each worker process spawns its own background thread, which calls Backblaze API.

### Default Configuration

The image ships with the following default settings:

- Workers: `1` (Ensures only one background scraper runs)
- Threads: `4` (Allows the web server to handle multiply concurrent requests)
- Worker class: `gthread`
- Bind address: `0.0.0.0:52000`
- Worker temp directory: `/dev/shm` (Improves performance by avoiding disk I/O for heartbeats)
- Log level: `warning`

### Runtime Details

The container runs as a non-root `app` user.  
Python output is unbuffered (`PYTHONUNBUFFERED=1`) for proper logging behavior.

Exporter logs are single-line JSON on stdout, with an `event` field on every record for filtering. Gunicorn's own access and error logs are plain text and are not routed through the JSON formatter.

## :gear: Usage

### :pencil: Prerequisites

A Backblaze B2 API key with read access to the monitored buckets.

### :page_facing_up: Env variables

| Variable | Description | Default | Required |
| --- | --- | --- | --- |
| `B2_APPLICATION_KEY_ID` | B2 API key ID | - | :ballot_box_with_check: |
| `B2_APPLICATION_KEY_FILE` | Path to file containing B2 API key | - | :ballot_box_with_check: |
| `B2_BUCKETS_CONFIG` | JSON map of buckets and paths | - | :ballot_box_with_check: |
| `B2_FILES_LATEST_ONLY` | Count only latest file versions | `false` | |
| `METRICS_UPDATE_INTERVAL` | Seconds between B2 API refreshes | `3600` | |
| `LOG_LEVEL` | Exporter log level | `INFO` | |
| `B2SDK_LOG_LEVEL` | Log level for the `b2sdk` library | `WARNING` | |
| `URLLIB3_LOG_LEVEL` | Log level for `urllib3.connectionpool` | `WARNING` | |
| `WERKZEUG_LOG_LEVEL` | Log level for Werkzeug (development server) | `WARNING` | |

Local development only:

| Variable | Description | Default |
| --- | --- | --- |
| `B2_APPLICATION_KEY` | B2 API key in plaintext (insecure) | - |
| `FLASK_PORT` | Flask port | `52000` |

### :whale: Run with Docker

1. Pull the image:
```bash
$ docker pull t0mmili/backblaze-prometheus-exporter:latest
```

2. Run:
```bash
$ docker run -d -p 52000:52000 -v $(pwd)/b2_api_key:/backblaze_exporter/api_key:ro \
    -e B2_APPLICATION_KEY_ID=yourkeyid \
    -e B2_APPLICATION_KEY_FILE=/backblaze_exporter/api_key \
    -e B2_BUCKETS_CONFIG='{"my-bucket": ["some/path/to/check", "some/other/path/to/check"]}' \
    --name backblaze-prometheus-exporter \
    t0mmili/backblaze-prometheus-exporter
```

### :computer: Run with CLI

1. Install dependencies:

```bash
$ uv sync --locked
```

2. Run:

```bash
$ uv run -m app.main
```

## :handshake: Contribution

Contributions are greatly appreciated! If you want to report a bug or request a feature, please [open an issue](https://github.com/t0mmili/backblaze-prometheus-exporter/issues).

## :page_facing_up: License

This project is licensed under the [MIT License](https://github.com/t0mmili/backblaze-prometheus-exporter/blob/main/LICENSE).

## :link: Credits

This exporter was originally based on [backblaze-prometheus-exporter](https://github.com/axiom-data-science/backblaze-prometheus-exporter) by **axiom-data-science**. It is used under the [MIT License](https://github.com/t0mmili/backblaze-prometheus-exporter/blob/main/LICENSE).
