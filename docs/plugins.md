# Plugin API

Marcel is moving toward a clean kernel / userspace split. The kernel is [`marcel_core`](https://github.com/shbunder/marcel) — harness, runner, storage, agent loop, tool protocol. The userspace is **marcel-zoo**, a separate repository containing the modular components (habitats) a Marcel install actually runs: integrations, skills, channels, jobs, agents. The location of the zoo checkout is configured via the `MARCEL_ZOO_DIR` environment variable (no default — discovery is a silent no-op when unset).

`marcel_core.plugin` is the stable surface zoo habitats import from. Anything re-exported there is a stability promise; anything else in `marcel_core` is internal and may change between versions.

!!! note "Status"
    The plugin surface currently covers **integrations, skill habitats, and channel habitats**. Job and agent surfaces are being added incrementally — see the open issues in `project/issues/open/` for the roadmap (ISSUE-a7d69a, ISSUE-e22176).

## Configuring the zoo location

Set `MARCEL_ZOO_DIR` in `.env.local` (or the environment) to point at your marcel-zoo checkout:

```bash
MARCEL_ZOO_DIR=~/projects/marcel-zoo
```

When unset, Marcel still runs — the kernel ships zero first-party integrations, so only the user's data-root skills are available. Pointing at a zoo checkout layers in habitats from `<MARCEL_ZOO_DIR>/integrations/` and `<MARCEL_ZOO_DIR>/skills/`.

## Integration habitat

An external integration lives at `<MARCEL_ZOO_DIR>/integrations/<name>/` and is discovered automatically on the next registry load. The directory is a Python package — `__init__.py` is required and runs at discovery time to trigger `@register` decorators. A sibling `integration.yaml` declares the integration's metadata (see [Integration metadata](#integration-metadata) below).

### Minimal example

`<MARCEL_ZOO_DIR>/integrations/demo/__init__.py`:

```python
from marcel_core.plugin import get_logger, register

log = get_logger(__name__)


@register("demo.ping")
async def ping(params: dict, user_slug: str) -> str:
    log.info("demo.ping called for %s", user_slug)
    return "pong"
```

`<MARCEL_ZOO_DIR>/integrations/demo/integration.yaml`:

```yaml
name: demo
description: Trivial demo integration
provides:
  - demo.ping
requires: {}
```

Calling `integration(id="demo.ping")` from the agent dispatches to the handler above. No changes to kernel code, no entry in `skills.json`, no restart beyond whatever the user's normal reload path is.

## Integration metadata

Each integration habitat ships an `integration.yaml` next to its `__init__.py`. The kernel uses it to resolve a skill habitat's `depends_on:` (see [Skills](skills.md)) back to the integration's requirements.

| Key | Required | Description |
|---|---|---|
| `name` | yes (when set) | Must equal the directory name. Defaults to the directory name when omitted. |
| `description` | no | One-line description shown in tooling. |
| `provides` | no | List of handler names registered by this integration. Every entry must start with `<name>.`. Used for documentation and consistency checks; the source of truth for dispatch is still `@register`. |
| `requires` | no | Dict of resources the integration needs to function. Recognised keys: `credentials`, `env`, `files`, `packages`. Unknown keys log a warning and are ignored. |

Validation rules — any failure logs an error and skips metadata registration; the handlers continue to dispatch normally, but `depends_on:` resolution against this integration will return `None` (treated as "requirements not met"):

- `name` must equal the directory name.
- `provides` must be a list of strings, all in the `<name>.*` namespace.
- `requires` must be a mapping.

A habitat without `integration.yaml` logs a warning and registers no metadata — perfectly valid for integrations that no skill habitat depends on, but means future skills cannot link to it via `depends_on:`.

### Directory-name ↔ handler-namespace rule

The directory name must match the `family` segment of every handler name registered by the package:

| Integration dir | Allowed handler names | Rejected handler names |
|---|---|---|
| `demo/` | `demo.ping`, `demo.status` | `container.start`, `other.x` |
| `banking/` | `banking.balance`, `banking.transactions` | `money.total` |

If any handler registered by the package falls outside the namespace, **the entire integration is rolled back**: no partial state leaks into the registry. The failure is logged at ERROR level; discovery of sibling integrations continues normally.

This rule exists so the integration's dotted handler prefix is a stable reverse-lookup to its source directory — useful for skills that declare `depends_on:` (see [Skills](skills.md), ISSUE-6ad5c7).

### Error isolation

Errors in one external integration never abort discovery of its siblings:

- `__init__.py` raises at import time → logged, that integration is skipped, siblings load.
- Handler registered outside the directory's namespace → logged, that integration rolled back, siblings load.
- Directory without `__init__.py` → logged as a warning, treated as not a habitat.

The net effect is that a user's marcel-zoo checkout can have one broken habitat without taking the rest of the install down.

## Scheduled jobs from habitats

A habitat can declare periodic background work by adding a `scheduled_jobs:` block to its `integration.yaml`. Each entry becomes a system-scope [`JobDefinition`](architecture.md#agent-loop-sequence) at scheduler startup — same retry, alerting, notification, and observability story as kernel jobs. No kernel code changes required.

### Why declarative

Marcel's periodic jobs are not raw cron handlers; they are full **agent jobs** dispatched through the LLM executor. That keeps two cases on one pipeline:

- **Deterministic case** — "call handler X on cron Y, report the result." The default auto-generated `system_prompt` and `task` cover this — declare three fields and you are done.
- **LLM-creative case** — "every morning, summarize today's calendar and surface conflicts." Override `task` / `system_prompt` / `model` per entry to inject the prompt the agent should run.

The alternative imperative shape (a `register_scheduled(scheduler)` callback in `__init__.py`) was considered and rejected — see [ISSUE-82f52b](https://github.com/shbunder/marcel/blob/main/project/issues/closed/ISSUE-260418-82f52b-scheduled-jobs-from-habitats.md): data is auditable without import side effects, validates uniformly, and rolls back uniformly. We will add an imperative path the day a habitat actually needs it; today none does.

### Schema

```yaml
# integration.yaml
name: news
provides:
  - news.sync

scheduled_jobs:
  - name: "News digest"
    handler: news.sync                # required, must be in provides:
    cron: "0 7 * * *"                 # required (XOR with interval_seconds)
    params:                           # optional dict — passed to the handler
      sources: "rss,atom"
    description: "Pull every feed and summarise"  # optional
    notify: on_failure                # optional: always | on_failure | on_output | silent (default)
    channel: telegram                 # optional, default 'telegram'
    timezone: "Europe/Brussels"       # optional, applies to cron only
    task: "Summarize today's news as bullets."   # optional override
    system_prompt: "You are the morning briefer." # optional override
    model: "anthropic:claude-sonnet-4-6"          # optional override
```

| Key | Required | Notes |
|---|---|---|
| `name` | yes | Unique within the habitat *and* across every other loaded habitat's job names. Used as the `JobDefinition.name`. |
| `handler` | yes | Must appear in this habitat's `provides:` list. |
| `cron` | XOR with `interval_seconds` | Standard 5-field cron expression validated by croniter. |
| `interval_seconds` | XOR with `cron` | Positive integer. |
| `params` | no | Dict passed to the handler. Stringify your values — Marcel does not coerce types. |
| `notify` | no | `silent` by default. `on_failure` is the right choice for sync jobs that should only ping the user when something breaks. |
| `task`, `system_prompt`, `model` | no | Per-entry overrides. The defaults synthesize a "call handler X with these params and report" prompt — fine for deterministic syncs, swap them out for anything richer. |

### Lifecycle

- **First discovery.** Each spec is materialized as a `JobDefinition` with a deterministic ID (`sha256("<habitat>:<name>")[:12]`), saved to the same `<data_root>/jobs/<slug>/` flat layout as kernel jobs. The job is `template='habitat:<name>'` so reconciliation can find it later.
- **Subsequent restarts.** Already-on-disk jobs (matched by stable ID) are left untouched — user edits to the JOB.md file survive. Add a new entry → it appears next startup.
- **Reconciliation.** On every scheduler rebuild, any job with `template='habitat:<name>'` whose habitat is no longer in the metadata registry — or whose entry name no longer appears in that habitat's `scheduled_jobs:` — is **deleted from disk**. Uninstalling a habitat (removing its directory) cleanly removes its jobs too.

### Validation and rollback

`scheduled_jobs:` is the **strict** part of `integration.yaml`. Where a malformed `provides:` only suppresses metadata (handlers keep dispatching), a malformed `scheduled_jobs:` entry **rolls back the entire habitat**: handlers are removed from the registry, no metadata is published, the scheduler never sees the broken state.

This is the same all-or-nothing principle the directory-name ↔ handler-namespace check uses (ISSUE-6ad5c7). The reasoning: a half-shipped scheduled job is a silent gap users would not notice — they would only learn about it the next time the missing job *should have* fired.

Conditions that trigger habitat rollback:

- `scheduled_jobs:` is not a list, or an entry is not a mapping
- Missing/empty `name`, missing `handler`
- `handler` not declared in `provides:`
- Neither (or both) of `cron` / `interval_seconds` set
- `cron` fails croniter validation
- `interval_seconds` is non-positive or boolean
- Duplicate `name` within the habitat
- `name` collision against a different already-loaded habitat
- `notify` not in `{always, on_failure, on_output, silent}`
- `params` is not a mapping

All such failures log at ERROR level naming the offending habitat and entry; sibling habitats continue loading.

### What `marcel_core.plugin` exposes

```python
from marcel_core.plugin import register, IntegrationHandler, get_logger
from marcel_core.plugin import credentials, paths, models, rss
```

#### Top-level

| Symbol | Purpose |
|---|---|
| `register(skill_name)` | Decorator that registers an async handler. Validates the `family.action` naming convention. |
| `IntegrationHandler` | Type alias for the handler signature: `Callable[[dict, str], Awaitable[str]]`. |
| `get_logger(name)` | Returns a module logger. Prefer this over `logging.getLogger` directly so future plugin-specific filtering hooks can be added without rewriting habitats. |

#### `marcel_core.plugin.credentials`

Per-user credential storage. Encrypted with `MARCEL_CREDENTIALS_KEY` when set, plaintext fallback otherwise — habitats need not care which.

| Symbol | Purpose |
|---|---|
| `load(slug) -> dict[str, str]` | Read every key/value pair stored for the user. Returns `{}` when no file exists. |
| `save(slug, creds: dict[str, str])` | Overwrite the user's credential file with *creds*. Writes are atomic and chmod'd to `0600`. |

`save()` replaces the entire blob, so the standard pattern is read–mutate–write rather than per-key set:

```python
from marcel_core.plugin import credentials

creds = credentials.load(user_slug)
creds["MY_SERVICE_API_KEY"] = new_value
credentials.save(user_slug, creds)
```

#### `marcel_core.plugin.paths`

Per-user filesystem helpers. Hides the data-root layout so a habitat never sees `<data_root>/users/{slug}/...` literally.

| Symbol | Purpose |
|---|---|
| `user_dir(slug) -> Path` | The user's data directory. **Not** created by this call — caller does `mkdir(parents=True, exist_ok=True)` on the specific subpath it needs. |
| `cache_dir(slug) -> Path` | The user's cache subdirectory, created on first call. Use this for any `*.db` / `*.json` cache file the habitat owns. |
| `list_user_slugs() -> list[str]` | The slugs of every existing user — used by integration sync loops that need to enumerate linked accounts. Returns `[]` when no users dir exists. |

```python
from marcel_core.plugin import paths

cache_file = paths.cache_dir(user_slug) / "mything.db"
key_file = paths.user_dir(user_slug) / "signing_key.pem"
for slug in paths.list_user_slugs():
    sync_one_user(slug)
```

#### `marcel_core.plugin.models`

Model registry + per-channel preference, used by the settings habitat to render and persist model choices.

| Symbol | Purpose |
|---|---|
| `all_models() -> dict[str, str]` | Curated `model_id -> display_name` mapping (Anthropic + OpenAI + optional local model). |
| `default_model() -> str` | The currently-configured tier-1 model, read live from `settings.marcel_standard_model`. |
| `get_channel_model(slug, channel) -> str \| None` | The user's preferred model for a channel, or `None` when unset (use `default_model()` as fallback). |
| `set_channel_model(slug, channel, model)` | Persist the user's preferred model for a channel. |

```python
from marcel_core.plugin import models

current = models.get_channel_model(user_slug, "telegram") or models.default_model()
models.set_channel_model(user_slug, "telegram", "anthropic:claude-sonnet-4-6")
```

#### `marcel_core.plugin.rss`

RSS / Atom feed fetcher, used by the news habitat to pull syndication feeds without reaching into `marcel_core.tools.*`.

| Symbol | Purpose |
|---|---|
| `fetch_feed(url, max_articles=50) -> list[dict[str, str]]` | Fetch and parse an RSS / Atom URL. Each article dict has `title`, `link`, `description`, `published`, `category` (keys present when the source provides them). Raises `ValueError` for non-XML / empty bodies, `httpx.HTTPStatusError` for non-2xx responses — callers log and move on. |

```python
from marcel_core.plugin import rss

articles = await rss.fetch_feed("https://www.vrt.be/vrtnws/nl.rss.articles.xml")
for art in articles:
    print(art["title"], art["link"])
```

Anything not listed above is internal — zoo code that imports it owns the breakage on any future Marcel upgrade.

### First-party vs. external integrations

The kernel ships zero first-party integrations. Every real integration lives in marcel-zoo as an external habitat — `docker` (ISSUE-6ad5c7), `icloud` (ISSUE-e7d127), `news` (ISSUE-d5f8ab), and `banking` (ISSUE-13c7f2) have all migrated out. The settings integration handler was retired as dead code under ISSUE-e1b9c4 — the live settings surface is the `marcel(action="...")` utility tool, not an `integration(id="settings.*")` handler.

## Channel habitat

A channel habitat is a concrete transport plugin — Telegram, Signal, Discord, etc. It lives at `<MARCEL_ZOO_DIR>/channels/<name>/` and self-registers with the kernel at discovery time so `main.py` can mount routers, the prompt builder can query capabilities, and push helpers (`notify`, `charts`, `ui`) can deliver without importing transport-specific modules.

Kernel-native surfaces (`websocket`, `cli`, `app`, `ios`, `macos`) are **not** channel habitats — they are built into the kernel. Only concrete transport plugins (Telegram and its future siblings) ship as habitats.

### Directory layout

```
<MARCEL_ZOO_DIR>/channels/<name>/
├── __init__.py          # required — imports register_channel() at load time
├── channel.yaml         # required — name, capabilities, requires
├── CHANNEL.md           # agent-visible formatting hint for this channel
├── <transport>.py       # bot.py, webhook.py, sessions.py, formatting.py, …
└── tests/               # habitat-owned tests (run from the zoo checkout)
```

The habitat's `__init__.py` must call `register_channel(plugin)` at import time — discovery is side-effect-driven, mirroring the integration registry. Use **relative imports** inside the habitat (`from . import bot, sessions`); the kernel loads habitats under the private `_marcel_ext_channels.<name>` namespace, not under `marcel_core.*`, so absolute imports of sibling modules will fail at runtime.

### Minimal example

`<MARCEL_ZOO_DIR>/channels/demo/__init__.py`:

```python
from fastapi import APIRouter

from marcel_core.channels.adapter import ChannelCapabilities
from marcel_core.plugin import register_channel

router = APIRouter(prefix='/channels/demo')


class DemoChannel:
    name = 'demo'
    capabilities = ChannelCapabilities(
        markdown=True,
        rich_ui=False,
        streaming=False,
        progress_updates=False,
        attachments=False,
    )
    router = router

    async def send_message(self, user_slug: str, text: str) -> bool:
        return False

    async def send_photo(self, user_slug, image_bytes, caption=None) -> bool:
        return False

    async def send_artifact_link(self, user_slug, artifact_id, title) -> bool:
        return False

    def resolve_user_slug(self, external_id: str) -> str | None:
        return None


register_channel(DemoChannel())
```

`<MARCEL_ZOO_DIR>/channels/demo/channel.yaml`:

```yaml
name: demo
description: Trivial demo channel
requires:
  credentials: []
capabilities:
  markdown: true
  rich_ui: false
  streaming: false
  progress_updates: false
  attachments: false
```

After a kernel restart, `list_channels()` contains `'demo'`, `main.py` mounts `router` at `/channels/demo/*`, and kernel code can push via `get_channel('demo').send_message(user_slug, text)`.

### The `ChannelPlugin` protocol

`marcel_core.plugin.channels.ChannelPlugin` is the runtime-checkable Protocol every habitat satisfies — duck-typed, so a plugin may be a class instance, a dataclass, or any object exposing the right names:

| Member | Purpose |
|---|---|
| `name: str` | Unique channel identifier. Must equal the directory name. |
| `capabilities: ChannelCapabilities` | Declares `markdown`, `rich_ui`, `streaming`, `progress_updates`, `attachments`. The prompt builder reads `rich_ui` to decide whether to inject the A2UI component catalog. |
| `router: APIRouter \| None` | Optional FastAPI router. Kernel-internal transports (e.g. the websocket) can return `None` if routing lives elsewhere. |
| `async send_message(user_slug, text) -> bool` | Deliver a text message; `False` when the recipient is not registered. |
| `async send_photo(user_slug, image_bytes, caption=None) -> bool` | Deliver an image; `False` when unsupported or unresolved. |
| `async send_artifact_link(user_slug, artifact_id, title) -> bool` | Deliver an artifact (e.g. Mini App button). Only meaningful when `capabilities.rich_ui` is true. |
| `resolve_user_slug(external_id) -> str \| None` | Map a transport-side identity (e.g. a Telegram user id) to a Marcel user slug. Channels without a separate identity space return `None`. |

### Channel metadata (`channel.yaml`)

| Key | Required | Notes |
|---|---|---|
| `name` | yes | Must equal the directory name. |
| `description` | no | One-line description for tooling. |
| `requires.credentials` | no | List of env-var names the channel needs (e.g. `TELEGRAM_BOT_TOKEN`). Today this is advisory — the kernel does not yet gate discovery on credential presence. |
| `capabilities` | no | Redundant declaration of what `ChannelCapabilities(...)` in `__init__.py` already says. Kept for tooling that wants to inspect the habitat without running its Python. |

### Discovery and error isolation

`marcel_core.plugin.channels.discover()` is called once from `main.py` at module load before the router-mount loop. It walks `<MARCEL_ZOO_DIR>/channels/` and imports every subdirectory. A habitat that raises at import time is logged and skipped; siblings continue loading. If `MARCEL_ZOO_DIR` is unset or `<zoo>/channels/` does not exist, `discover()` is a silent no-op — the kernel still boots, just without any channel habitat.

Subsequent calls to `discover()` are idempotent: already-loaded habitats are skipped via their presence in `sys.modules`.

## See also

- [Skills](skills.md) — integration handlers vs. skill docs (SKILL.md, SETUP.md, `depends_on:`).
- [Storage](storage.md) — where `<data_root>` resolves and how per-user data is organized.
- [Architecture](architecture.md) — kernel / userspace model and where plugins sit in the overall design.
