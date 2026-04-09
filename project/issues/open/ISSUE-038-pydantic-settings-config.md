# ISSUE-038: Centralize config via pydantic-settings

**Status:** Open
**Created:** 2026-04-09
**Assignee:** Shaun Bundervoet
**Priority:** Medium
**Labels:** feature, refactor

## Capture
**Original request:** "lets use pydantic-settings across this repo. I don't want to have too many os.environ calls. I want to have all settings loaded centrally (pydantic-ai handles reading environment variables and writing it in a dataclass) and than accessed throughout the code-base, this is cleaner"

**Resolved intent:** Replace the 21 scattered `os.environ.get()` calls (across 13 files) with a single `Settings` dataclass powered by `pydantic-settings`. The `Settings` singleton reads `.env` and `.env.local` at import time, so no call site ever needs to call `load_dotenv` or reach into the environment directly. The two dynamic env lookups in the skill system (arbitrary user-supplied keys) are intentionally left as-is.

## Description

The codebase accessed 13 distinct environment variables via raw `os.environ.get()` sprinkled across auth, storage, Telegram, harness, watchdog, and API modules. This meant:
- Defaults were scattered and inconsistent
- `.env` loading depended on `load_dotenv()` being called at the right moment in `main.py`
- No single source of truth for what env vars the app requires

The fix: introduce `src/marcel_core/config.py` with a `pydantic-settings` `BaseSettings` subclass. All static env vars are declared as typed fields with defaults. A module-level `settings` singleton is imported by every callsite.

## Tasks
- [✓] Add `pydantic-settings>=2.0.0` to `pyproject.toml`
- [✓] Create `src/marcel_core/config.py` with `Settings` class and `settings` singleton
- [✓] Remove manual `load_dotenv` calls from `main.py`; import `settings` for CORS origins
- [✓] Update `auth/__init__.py` — `MARCEL_API_TOKEN`, `TELEGRAM_BOT_TOKEN`
- [✓] Update `storage/_root.py` — `MARCEL_DATA_DIR` (lazy import to avoid circular)
- [✓] Update `storage/credentials.py` — `MARCEL_CREDENTIALS_KEY`
- [✓] Update `channels/telegram/bot.py` — `TELEGRAM_BOT_TOKEN`, `MARCEL_PUBLIC_URL`
- [✓] Update `channels/telegram/formatting.py` — `MARCEL_PUBLIC_URL`
- [✓] Update `channels/telegram/webhook.py` — `TELEGRAM_WEBHOOK_SECRET`
- [✓] Update `watchdog/main.py` — `MARCEL_PORT`, `MARCEL_HEALTH_TIMEOUT`, `MARCEL_POLL_INTERVAL`
- [✓] Update `harness/agent.py` — `AWS_REGION`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`
- [✓] Update `api/chat.py` and `api/chat_v2.py` — `MARCEL_DEFAULT_USER`
- [✓] Update `skills/loader.py` — `MARCEL_DATA_DIR` via `settings.data_dir`
- [✓] Install `pydantic-settings` via `uv add`

## Relationships

## Implementation Log
