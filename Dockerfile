FROM python:3.12-slim

# System dependencies + Docker CLI (to manage sibling containers via mounted socket)
RUN apt-get update && \
    apt-get install -y --no-install-recommends git curl gnupg \
        # Chromium/Playwright runtime dependencies
        libglib2.0-0 libnspr4 libnss3 libatk1.0-0 libatk-bridge2.0-0 \
        libcups2 libxdamage1 libxkbcommon0 libpango-1.0-0 libcairo2 \
        libasound2 libdrm2 libgbm1 libxrandr2 libxcomposite1 libxfixes3 \
        libdbus-1-3 libexpat1 libxext6 && \
    curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /usr/share/keyrings/docker.gpg && \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/docker.gpg] https://download.docker.com/linux/debian $(. /etc/os-release && echo $VERSION_CODENAME) stable" > /etc/apt/sources.list.d/docker.list && \
    apt-get update && \
    apt-get install -y --no-install-recommends docker-ce-cli && \
    apt-get purge -y gnupg && apt-get autoremove -y && \
    rm -rf /var/lib/apt/lists/*

# Install uv (fast Python package manager)
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"

WORKDIR /app

# Install Python dependencies (cached layer — only rebuilds when deps change)
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --all-extras --no-dev --no-install-project

# Copy source code (in production, source is bind-mounted so this layer
# serves as a fallback and for the initial build)
COPY src/ ./src/

# Install the project itself
RUN uv sync --frozen --all-extras --no-dev

# The kernel ships zero bundled skills and zero bundled subagents —
# both live in marcel-zoo and are loaded from MARCEL_ZOO_DIR at runtime.
# seed_defaults() still copies channel prompts and routing.yaml from
# src/marcel_core/defaults/ to the data root (~/.marcel/) on first start.

# Create non-root user matching the host user (UID/GID passed at build time)
ARG USER_UID=1000
ARG USER_GID=1000
ARG DOCKER_GID=988
RUN groupadd -g ${USER_GID} marcel && \
    useradd -m -u ${USER_UID} -g marcel -s /bin/bash marcel && \
    groupadd -g ${DOCKER_GID} dockerhost && usermod -aG dockerhost marcel && \
    chown -R marcel:marcel /app
ENV PATH="/home/marcel/.local/bin:${PATH}"
USER marcel

EXPOSE 7420

ENV MARCEL_PORT=7420

HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:${MARCEL_PORT}/health || exit 1

# Run via the watchdog (PID 1) which manages uvicorn and handles rollback
CMD ["uv", "run", "python", "-m", "marcel_core.watchdog.main"]
