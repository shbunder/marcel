"""Central configuration module.

All environment variables are declared here as a typed pydantic-settings
``Settings`` dataclass.  Reads ``.env`` and ``.env.local`` automatically so
callsites never need to call ``load_dotenv`` or ``os.environ.get`` directly.

Usage::

    from marcel_core.config import settings

    token = settings.telegram_bot_token
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=('.env', '.env.local'),
        env_file_encoding='utf-8',
        extra='ignore',
    )

    # ---------------------------------------------------------------------------
    # Server
    # ---------------------------------------------------------------------------
    marcel_port: int = 8000
    marcel_cors_origins: str = 'http://localhost:5173'
    marcel_public_url: str | None = None
    marcel_default_user: str = ''

    # ---------------------------------------------------------------------------
    # Auth
    # ---------------------------------------------------------------------------
    marcel_api_token: str = ''

    # ---------------------------------------------------------------------------
    # Data
    # ---------------------------------------------------------------------------
    marcel_data_dir: str | None = None
    marcel_credentials_key: str = ''

    # ---------------------------------------------------------------------------
    # Telegram
    # ---------------------------------------------------------------------------
    telegram_bot_token: str = ''
    telegram_webhook_secret: str = ''

    # ---------------------------------------------------------------------------
    # AI providers
    # ---------------------------------------------------------------------------
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    aws_region: str | None = None

    # Four-tier model fallback chain (ISSUE-076).
    #
    # Tier 1 (STANDARD) handles normal calls. Tier 2 (BACKUP) is a different-
    # cloud-provider backup tried when tier 1 fails with a transient/auth
    # error. Tier 3 (FALLBACK) is a local LLM whose only job, in interactive
    # turns, is to *explain* the failure to the user — not complete the task.
    # Tier 4 (POWER) is the default model for the 'power' subagent that the
    # main agent can spawn via delegate() when it decides a task exceeds its
    # standard model.
    #
    # Tiers 2 and 3 are opt-in: the chain skips them silently when unset, so
    # a fresh install behaves identically to pre-ISSUE-076 (single model, no
    # fallback).  See docs/model-tiers.md for the full behaviour matrix.
    marcel_standard_model: str = 'anthropic:claude-sonnet-4-6'
    marcel_backup_model: str | None = None  # e.g. 'openai:gpt-4o'
    marcel_fallback_model: str | None = None  # e.g. 'local:qwen3.5:4b'
    marcel_power_model: str = 'anthropic:claude-opus-4-6'

    # Local LLM (opt-in job fallback via OpenAI-compatible server like Ollama).
    # Example::
    #
    #     MARCEL_LOCAL_LLM_URL=http://127.0.0.1:11434/v1
    #     MARCEL_LOCAL_LLM_MODEL=qwen3.5:4b
    #
    # When both are set, a job with ``allow_local_fallback=True`` will re-run
    # against ``local:<model>`` after cloud retries exhaust. See
    # ``docs/local-llm.md`` for the full runtime setup.
    marcel_local_llm_url: str | None = None
    marcel_local_llm_model: str | None = None

    # ---------------------------------------------------------------------------
    # Browser
    # ---------------------------------------------------------------------------
    browser_headless: bool = True
    browser_url_allowlist: str = ''
    browser_timeout: int = 30

    # ---------------------------------------------------------------------------
    # Web search
    # ---------------------------------------------------------------------------
    brave_api_key: str | None = None
    web_search_backend: str | None = None

    # ---------------------------------------------------------------------------
    # Conversation management
    # ---------------------------------------------------------------------------
    marcel_idle_summarize_minutes: int = 60
    marcel_bash_max_output: int = 30000

    # ---------------------------------------------------------------------------
    # Observability / Tracing
    # ---------------------------------------------------------------------------
    marcel_tracing_enabled: bool = False
    marcel_tracing_endpoint: str = 'http://localhost:6006'
    marcel_tracing_project: str = 'default'

    # ---------------------------------------------------------------------------
    # Watchdog
    # ---------------------------------------------------------------------------
    marcel_health_timeout: float = 30.0
    marcel_poll_interval: float = 2.0

    # ---------------------------------------------------------------------------
    # Derived helpers
    # ---------------------------------------------------------------------------

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.marcel_cors_origins.split(',')]

    @property
    def data_dir(self) -> Path:
        if self.marcel_data_dir:
            return Path(self.marcel_data_dir)
        return Path.home() / '.marcel'


settings = Settings()
