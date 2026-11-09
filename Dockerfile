# syntax=docker/dockerfile:1
#
# The deployable artifact: the service and its locked dependencies, built
# from one commit. It contains no credentials or environment-specific
# settings; those are supplied when the container starts (docs/DEPLOYMENT.md).
#
# Build:  docker build --build-arg GIT_SHA="$(git rev-parse HEAD)" -t ospsd-team4 .
#
# Base images are pinned by version (matching CI: Python 3.13.5, uv 0.12.17)
# and by digest, so a rebuild of the same commit uses the same layers.

ARG PYTHON_IMAGE=python:3.13.5-slim-bookworm@sha256:4c2cf9917bd1cbacc5e9b07320025bdb7cdf2df7b0ceaccb55e9dd7e30987419
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.12.17@sha256:10787c682e4184e4f290de1171fd4703dc63de99221f10fe1c99002ce7fa9acc

FROM ${UV_IMAGE} AS uv

# Stage 1: install the locked runtime dependencies into /app/.venv.
FROM ${PYTHON_IMAGE} AS builder

COPY --from=uv /uv /bin/uv

# Compile bytecode at build time, copy files out of uv's cache, and use the
# image's Python instead of downloading one.
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app

# Dependencies first, so code changes reuse this cached layer.
COPY pyproject.toml uv.lock ./
COPY contract/pyproject.toml contract/README.md contract/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project --no-install-workspace

# Then the shared contract package (a workspace member, installed into the
# environment) and the service code. The root project is not a package, so
# the service runs from /app.
COPY contract/src contract/src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

# Stage 2: the runtime image, without uv, build caches or sources it does not run.
FROM ${PYTHON_IMAGE}

# The commit this image was built from. Recorded as a label and exposed to
# the service, which reports it in GET /readyz.
ARG GIT_SHA=unknown
LABEL org.opencontainers.image.revision="${GIT_SHA}" \
      org.opencontainers.image.source="https://github.com/Tyler-Arciniaga/OSPSD-Team-4" \
      org.opencontainers.image.title="ospsd-team4-issue-service"

ENV GIT_SHA="${GIT_SHA}" \
    PATH="/app/.venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000 \
    IDEMPOTENCY_DB_PATH=/app/data/idempotency.sqlite3

# Run as an unprivileged user. Only the data directory is writable by it.
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin app \
    && mkdir -p /app/data \
    && chown app:app /app/data

WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY app ./app

USER app
EXPOSE 8000

# The platform sets PORT; exec makes uvicorn PID 1 so it receives SIGTERM.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port \"${PORT}\""]
