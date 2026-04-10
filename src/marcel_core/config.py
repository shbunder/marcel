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

    # ---------------------------------------------------------------------------
    # Browser
    # ---------------------------------------------------------------------------
    browser_headless: bool = True
    browser_url_allowlist: str = ''
    browser_timeout: int = 30

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
