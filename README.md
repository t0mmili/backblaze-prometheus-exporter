# Backblaze Prometheus Exporter

![Docker Pulls](https://img.shields.io/docker/pulls/t0mmili/backblaze-prometheus-exporter)
![Docker Image Size](https://img.shields.io/docker/image-size/t0mmili/backblaze-prometheus-exporter)
![GitHub License](https://img.shields.io/github/license/t0mmili/backblaze-prometheus-exporter)
![GitHub Release](https://img.shields.io/github/release/t0mmili/backblaze-prometheus-exporter)

Export Prometheus metrics for Backblaze B2 buckets and paths, including file count, total size, and last upload timestamp.

Supports monitoring multiple buckets and paths with background data collection to ensure high performance.

## :bar_chart: Metrics

### Backblaze B2

- `backblaze_b2_path_files_count` - Gauge: Total number of files in the path.
- `backblaze_b2_path_files_latest_version_only` - Gauge: Whether only the latest version (1) or all versions (0) of files are processed.
- `backblaze_b2_path_last_upload_seconds` - Gauge: Unix timestamp of the most recent upload in the path.
- `backblaze_b2_path_size_bytes` - Gauge: Total size of files in the path.

### Exporter

- `backblaze_b2_scrape_duration_seconds` - Gauge: Time taken to fetch stats.
- `backblaze_b2_scrape_last_time_seconds` - Unix timestamp of the most recent successful scrape.
- `backblaze_b2_scrape_success` - Gauge: Whether the last scrape was successful (1) or failed (0).

## :stopwatch: Understanding Intervals

It is important to distinguish between how Prometheus collects data and how this exporter fetches data from Backblaze:

- `scrape_interval` **(Prometheus)**: This defines how often Prometheus hits the `/metrics` endpoint. Because this exporter uses a background thread, the `/metrics` endpoint responds instantly with cached data.
- `METRICS_UPDATE_INTERVAL` **(Exporter)**: This defines how often the background thread actually calls the Backblaze B2 API.

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
