# syntax=docker/dockerfile:1
#
# The product service image, built from product/ as the build context:
#   docker build --build-arg GIT_SHA="$(git rev-parse HEAD)" -t ospsd-team4-product product
#
# It contains no credentials or environment-specific settings. Provider
# selection and secrets (including the calendar token file, mounted as a
# secret file) are supplied when the container starts.
#
# Base images are pinned by version and digest. Python 3.14 is required by
# team5-calendar; uv matches the HW2 image (0.12.17).

ARG PYTHON_IMAGE=python:3.14.8-slim-bookworm@sha256:44e000a07b48bef1517a2b14adc93c17af728ca98858b5e24c6e1b18de1b2ef2
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.12.17@sha256:10787c682e4184e4f290de1171fd4703dc63de99221f10fe1c99002ce7fa9acc

FROM ${UV_IMAGE} AS uv

# Stage 1: install the locked dependencies and the service into /app/.venv.
FROM ${PYTHON_IMAGE} AS builder

COPY --from=uv /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

# uv needs git to fetch the tagged and commit-pinned packages. Only this
# build stage has it.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies first, so source changes reuse this layer. --frozen installs
# exactly what the committed uv.lock records.
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project --no-editable

COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

# Stage 2: the runtime image, without uv, git or sources.
FROM ${PYTHON_IMAGE}

ARG GIT_SHA=unknown
LABEL org.opencontainers.image.revision="${GIT_SHA}" \
      org.opencontainers.image.source="https://github.com/Tyler-Arciniaga/OSPSD-Team-4" \
      org.opencontainers.image.title="ospsd-team4-product"

ENV GIT_SHA="${GIT_SHA}" \
    PATH="/app/.venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000

RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin app

WORKDIR /app
COPY --from=builder /app/.venv /app/.venv

USER app
EXPOSE 8000

# The platform sets PORT; exec makes uvicorn PID 1 so it receives SIGTERM.
CMD ["sh", "-c", "exec uvicorn ospsd_team4_product.main:app --host 0.0.0.0 --port \"${PORT}\""]
