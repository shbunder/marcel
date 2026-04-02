FROM python:3.12-slim

# System dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends git curl && \
    rm -rf /var/lib/apt/lists/*

# Install uv (fast Python package manager)
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"

WORKDIR /app

# Install Python dependencies (cached layer — only rebuilds when deps change)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --all-extras --no-dev --no-install-project

# Copy source code (in production, source is bind-mounted so this layer
# serves as a fallback and for the initial build)
COPY src/ ./src/

# Install the project itself in editable mode
RUN uv sync --frozen --all-extras --no-dev

EXPOSE 7420

ENV MARCEL_PORT=7420

HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:${MARCEL_PORT}/health || exit 1

# Run via the watchdog (PID 1) which manages uvicorn and handles rollback
CMD ["uv", "run", "python", "-m", "marcel_core.watchdog.main"]
