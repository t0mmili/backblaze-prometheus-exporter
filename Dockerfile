ARG PYTHON_IMAGE=python:3.14-alpine
ARG UV_IMAGE=ghcr.io/astral-sh/uv:latest

FROM ${UV_IMAGE} AS uv

# -------- Build Stage --------
FROM ${PYTHON_IMAGE} AS builder

# Install uv
COPY --from=uv /uv /uvx /bin/

ARG APP_DIR=/backblaze_exporter
WORKDIR ${APP_DIR}

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

# Copy dependency metadata
COPY pyproject.toml uv.lock ./

# Install dependencies
RUN uv sync --locked --no-dev --no-install-project

COPY app ./app
COPY assets ./assets
COPY README.md ./
RUN uv sync --locked --no-dev

# -------- Runtime Stage --------
FROM ${PYTHON_IMAGE}

ARG APP_DIR=/backblaze_exporter
ARG APP_VERSION=0.0.0-dev

LABEL org.opencontainers.image.title="backblaze-prometheus-exporter" \
      org.opencontainers.image.description="Prometheus Exporter for Backblaze B2 bucket metrics" \
      org.opencontainers.image.source="https://github.com/t0mmili/backblaze-prometheus-exporter" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.licenses="MIT"

WORKDIR ${APP_DIR}

# Create unprivileged user and prepare app directory
RUN addgroup -S -g 10001 app  \
    && adduser -S -u 10001 -G app app \
    && mkdir -p ${APP_DIR} \
    && chown app:app ${APP_DIR}

COPY --from=builder --chown=app:app ${APP_DIR}/.venv ${APP_DIR}/.venv
COPY --chown=app:app app ./app
COPY --chown=app:app assets ./assets
COPY --chown=app:app gunicorn.conf.py ./
COPY --chown=app:app pyproject.toml README.md ./

# Environment configuration
ENV GUNICORN_CMD_ARGS="--workers=1 --threads=4 --worker-class=gthread --bind=0.0.0.0:52000 --worker-tmp-dir=/dev/shm --log-level=warning"
ENV PATH="${APP_DIR}/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER 10001:10001

EXPOSE 52000

CMD ["gunicorn", "app.main:app"]
