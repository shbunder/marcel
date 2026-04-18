# Plugin API

Marcel is moving toward a clean kernel / userspace split. The kernel is [`marcel_core`](https://github.com/shbunder/marcel) — harness, runner, storage, agent loop, tool protocol. The userspace is **marcel-zoo**, a separate repository containing the modular components (habitats) a Marcel install actually runs: integrations, skills, channels, jobs, agents. The location of the zoo checkout is configured via the `MARCEL_ZOO_DIR` environment variable (no default — discovery is a silent no-op when unset).

`marcel_core.plugin` is the stable surface zoo habitats import from. Anything re-exported there is a stability promise; anything else in `marcel_core` is internal and may change between versions.

!!! note "Status"
    The plugin surface currently covers **integrations + skill habitats**. Channel, job, and agent surfaces are being added incrementally — see the open issues in `project/issues/open/` for the roadmap (ISSUE-7d6b3f, ISSUE-a7d69a, ISSUE-e22176).

## Configuring the zoo location

Set `MARCEL_ZOO_DIR` in `.env.local` (or the environment) to point at your marcel-zoo checkout:

```bash
MARCEL_ZOO_DIR=~/projects/marcel-zoo
```

When unset, Marcel still runs — it loads only the kernel-bundled first-party integrations and the user's data-root skills. Pointing at a zoo checkout layers in additional habitats from `<MARCEL_ZOO_DIR>/integrations/` and `<MARCEL_ZOO_DIR>/skills/`.

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

### What `marcel_core.plugin` exposes

```python
from marcel_core.plugin import register, IntegrationHandler, get_logger
from marcel_core.plugin import credentials, paths, models
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

Anything not listed above is internal — zoo code that imports it owns the breakage on any future Marcel upgrade.

### First-party vs. external integrations

Internally, Marcel still ships several first-party integrations inside `src/marcel_core/skills/integrations/` (banking, icloud, news, settings). These continue to work unchanged during the zoo migration — they are discovered via the same `discover()` entry point alongside external habitats. The first complete migration target was `docker`, now living entirely in marcel-zoo (ISSUE-6ad5c7); the remaining first-party integrations follow over subsequent issues.

## See also

- [Skills](skills.md) — integration handlers vs. skill docs (SKILL.md, SETUP.md, `depends_on:`).
- [Storage](storage.md) — where `<data_root>` resolves and how per-user data is organized.
- [Architecture](architecture.md) — kernel / userspace model and where plugins sit in the overall design.
