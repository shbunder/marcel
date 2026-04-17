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

    # Three-tier model ladder with per-tier cross-cloud backup (ISSUE-e0db47,
    # extends ISSUE-076).
    #
    # FAST (Haiku-class) — short lookups, chat, one-liners. Auto-selected by
    # the session classifier for simple first messages.
    # STANDARD (Sonnet-class) — daily driver. Auto-selected when the classifier
    # sees complexity, or reached via a fast→standard frustration bump.
    # POWER (Opus-class) — only reached via an explicit skill
    # (``preferred_tier: power``) or subagent (``model: power``). Never
    # auto-selected by the classifier, so no runaway Opus cost from chat.
    #
    # Each tier has an optional cross-cloud backup tried when the primary
    # fails with a transient/auth error. MARCEL_FALLBACK_MODEL is a shared
    # last-resort local LLM whose only job is to *explain* the failure to the
    # user — not complete the task. Backups and fallback are opt-in; the chain
    # skips them silently when unset. See docs/model-tiers.md.
    marcel_fast_model: str = 'anthropic:claude-haiku-4-5-20251001'
    marcel_fast_backup_model: str | None = None  # e.g. 'openai:gpt-4o-mini'
    marcel_standard_model: str = 'anthropic:claude-sonnet-4-6'
    marcel_standard_backup_model: str | None = None  # e.g. 'openai:gpt-4o'
    marcel_power_model: str = 'anthropic:claude-opus-4-6'
    marcel_power_backup_model: str | None = None  # e.g. 'openai:gpt-4o' or 'openai:o1'
    marcel_fallback_model: str | None = None  # e.g. 'local:qwen3.5:4b'

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
