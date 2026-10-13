# The hub image. Built from the repository root: `docker compose -f deploy/docker-compose.yml build`.
FROM python:3.12-slim

# git: the hub keeps memory in a git working copy (hub/src/marcel_hub/memory.py).
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.11.2 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/venv \
    UV_CACHE_DIR=/tmp/uv-cache
WORKDIR /app/hub

# Dependencies first, so a source change does not reinstall them.
COPY hub/pyproject.toml hub/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project && rm -rf /tmp/uv-cache

# The project is installed editable, so `marcel_hub` runs from /app/hub/src and finds
# /app/hub/migrations next to it (db.MIGRATIONS_DIR, S-04.1).
COPY hub/README.md ./
COPY hub/src ./src
COPY hub/migrations ./migrations
# The hub reads JSON Schemas from contracts/ at run time (artifacts/store.py: CONTRACTS_DIR).
COPY contracts /app/contracts
RUN uv sync --frozen --no-dev && rm -rf /tmp/uv-cache
# Fail the build, not a live request, when a file the hub reads at run time is missing.
RUN /app/venv/bin/python -c "from marcel_hub import db; from marcel_hub.artifacts.store import _dashboard_validator; assert db.MIGRATIONS_DIR.is_dir(); _dashboard_validator()"

ENV PATH=/app/venv/bin:$PATH \
    PYTHONUNBUFFERED=1
EXPOSE 7420
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:7420/api/health', timeout=4).status == 200 else 1)"]
CMD ["marcel-hub"]
