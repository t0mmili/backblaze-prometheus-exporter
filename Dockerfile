# -------- Build Stage --------
FROM python:3.14-alpine AS builder

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /backblaze_exporter

# Copy dependency metadata
COPY pyproject.toml uv.lock ./

# Install dependencies
RUN uv sync --frozen --no-install-project --no-dev && \
    uv pip install gunicorn[gthread]


# -------- Runtime Stage --------
FROM python:3.14-alpine

LABEL org.opencontainers.image.title="backblaze-prometheus-exporter" \
      org.opencontainers.image.description="Prometheus Exporter for Backblaze B2 bucket metrics" \
      org.opencontainers.image.source="https://github.com/t0mmili/backblaze-prometheus-exporter" \
      org.opencontainers.image.version="0.1.0" \
      org.opencontainers.image.licenses="MIT"

ARG APP_DIR=/backblaze_exporter
WORKDIR ${APP_DIR}

# Create unprivileged user and prepare app directory
RUN addgroup -S app && adduser -S -G app app \
    && mkdir -p ${APP_DIR} \
    && chown app:app ${APP_DIR}

# Copy virtual environment from builder
COPY --from=builder --chown=app:app ${APP_DIR}/.venv ${APP_DIR}/.venv

# Copy application code
COPY --chown=app:app app ./app
COPY --chown=app:app assets ./assets
COPY --chown=app:app pyproject.toml ./

# Environment configuration
ENV GUNICORN_CMD_ARGS="--threads=4 --worker-class=gthread --bind=0.0.0.0:52000 --worker-tmp-dir=/dev/shm --log-level=warning"
ENV PATH="${APP_DIR}/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1

USER app

EXPOSE 52000

CMD ["gunicorn", "app.main:app"]
