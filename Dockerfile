# syntax=docker/dockerfile:1
# Stages: `check` runs the same check as scripts/check.ps1; `runtime` is the slim, non-root image.
# Build needs the package index; running the container needs no network.
ARG PYTHON_VERSION=3.12
ARG UV_VERSION=0.13

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

FROM python:${PYTHON_VERSION}-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:${PATH}"
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app

FROM base AS check
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-install-project
COPY . .
RUN uv sync --frozen
RUN python scripts/run_check.py

FROM base AS runtime-build
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev

FROM python:${PYTHON_VERSION}-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:${PATH}"
RUN useradd --create-home --uid 10001 searchengine
COPY --from=runtime-build /opt/venv /opt/venv
COPY --from=runtime-build /app/src /app/src
USER searchengine
WORKDIR /app
# Data lives in a volume; the server (Step 22) binds 127.0.0.1 unless configured otherwise.
VOLUME ["/app/data"]
ENTRYPOINT ["search-engine"]
CMD ["health"]
