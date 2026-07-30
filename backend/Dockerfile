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

RUN useradd --create-home --uid 1000 appuser

COPY --from=production-build --chown=appuser:appuser /app/.venv /app/.venv
COPY --from=production-build --chown=appuser:appuser /app/backend ./backend

USER appuser

EXPOSE 8000

CMD ["fastapi", "run", "backend/main.py", "--host", "0.0.0.0", "--port", "8000"]
