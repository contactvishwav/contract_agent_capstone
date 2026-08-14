FROM python:3.12-slim-bullseye AS base

ENV PYTHONUNBUFFERED=1

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

WORKDIR /app

# Copy dependency files first (for better caching)
COPY uv.lock pyproject.toml ./

# ---------------------------------------------------------------------------
# dev: used by docker-compose.yml for local development. Installs the "dev"
# dependency group (pytest, ruff, ...), keeps hot-reload via `fastapi dev`.
# ---------------------------------------------------------------------------
FROM base AS dev

RUN uv sync

COPY . ./backend/

ENV PYTHONPATH=/app

EXPOSE 8000

# Reliability/observability audit finding #9: points at the real health
# endpoint (backend/api/monitoring_api.py's /api/monitoring/health), which
# now actually checks Neo4j/Redis and returns a genuine non-2xx status on
# failure - so this check reflects real dependency health, not just "the
# process is still running". `python` (already in this image, stdlib
# urllib only) avoids adding curl/wget as an extra dependency just for
# this. start-period covers app startup (Neo4j connectivity check,
# LLMManager construction) before failures start counting.
HEALTHCHECK --interval=30s --timeout=15s --start-period=40s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/monitoring/health', timeout=15)"

CMD ["uv", "run", "fastapi", "dev", "backend/main.py", "--host", "0.0.0.0", "--port", "8000"]

# ---------------------------------------------------------------------------
# production-build: resolves and installs only runtime dependencies (the
# "dev" group - pytest/ruff/pyyaml - is skipped via --no-dev), then copies
# in application code with test code stripped back out.
# ---------------------------------------------------------------------------
FROM base AS production-build

RUN uv sync --frozen --no-dev --no-install-project

COPY . ./backend/
RUN rm -rf ./backend/tests

RUN uv sync --frozen --no-dev

# ---------------------------------------------------------------------------
# production: final runtime image. No uv/build tools, no dev dependencies,
# no test code, runs as a non-root user, no hot-reload (`fastapi run`, not
# `fastapi dev`).
# ---------------------------------------------------------------------------
FROM python:3.12-slim-bullseye AS production

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    poppler-utils \
    tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 1000 appuser
RUN mkdir -p /var/lib/contract-agent/source-pdfs /var/lib/contract-agent/data /app/data \
    && chown -R appuser:appuser /var/lib/contract-agent /app

COPY --from=production-build --chown=appuser:appuser /app/.venv /app/.venv
COPY --from=production-build --chown=appuser:appuser /app/backend ./backend

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=15s --start-period=40s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/monitoring/health', timeout=15)"

CMD ["python", "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
