# Toolkit habitats

A **toolkit habitat** is a Python package that registers handlers the
agent can call through the `toolkit` (legacy name: `integration`) tool.
It is the *executable* layer of Marcel's userspace — the code that
actually talks to external services, runs shell commands, or encodes
deterministic logic. Toolkit handlers are what skills *reach for*; the
skill layer (see [Skills](skills.md)) teaches the agent *when* to call
them.

See [Habitats](habitats.md) for how toolkits fit alongside the other
four kinds.

Marcel is split into a clean kernel / userspace boundary. The kernel is
[`marcel_core`](https://github.com/shbunder/marcel) — harness, runner,
storage, agent loop, tool protocol, scheduler. The userspace is
**marcel-zoo**, a separate repository of habitats. The zoo location is
configured via the `MARCEL_ZOO_DIR` environment variable (no default —
discovery is a silent no-op when unset).

`marcel_core.plugin` is the stable surface zoo habitats import from.
Anything re-exported there is a stability promise; anything else in
`marcel_core` is internal and may change between versions.

## Configuring the zoo location

Set `MARCEL_ZOO_DIR` in `.env.local` (or the environment) to point at
your marcel-zoo checkout:

```bash
MARCEL_ZOO_DIR=~/projects/marcel-zoo
```

When unset, Marcel still runs — the kernel ships zero first-party
habitats of any kind, so only the user's data-root skills are
available. Pointing at a zoo checkout layers in habitats from
`<MARCEL_ZOO_DIR>/toolkit/`, `<MARCEL_ZOO_DIR>/skills/`,
`<MARCEL_ZOO_DIR>/channels/`, `<MARCEL_ZOO_DIR>/agents/`, and
`<MARCEL_ZOO_DIR>/jobs/`.

## Directory layout

A toolkit habitat lives at `<MARCEL_ZOO_DIR>/toolkit/<name>/` and is
discovered automatically on the next registry load. The directory is a
Python package — `__init__.py` is required and runs at discovery time
to trigger `@marcel_tool` decorators. A sibling `toolkit.yaml` declares
the habitat's metadata (see [Metadata](#metadata)).

```text
<MARCEL_ZOO_DIR>/toolkit/<name>/
├── __init__.py          # required — decorators registered on import
├── toolkit.yaml         # required — name, description, provides, requires
└── <module>.py          # optional — any internal modules the habitat owns
```

## Minimal example

`<MARCEL_ZOO_DIR>/toolkit/demo/__init__.py`:

```python
from marcel_core.plugin import get_logger, marcel_tool

log = get_logger(__name__)


@marcel_tool("demo.ping")
async def ping(params: dict, user_slug: str) -> str:
    log.info("demo.ping called for %s", user_slug)
    return "pong"
```

`<MARCEL_ZOO_DIR>/toolkit/demo/toolkit.yaml`:

```yaml
name: demo
description: Trivial demo toolkit
provides:
  - demo.ping
requires: {}
```

Calling `toolkit(id="demo.ping")` from the agent dispatches to the
handler above. No changes to kernel code, no entry in `skills.json`,
no restart beyond whatever the user's normal reload path is.

## Metadata

Each toolkit habitat ships a `toolkit.yaml` next to its `__init__.py`.
The kernel uses it to resolve a skill habitat's `depends_on:` (see
[Skills](skills.md)) back to the toolkit's requirements.

| Key | Required | Description |
|---|---|---|
| `name` | yes (when set) | Must equal the directory name. Defaults to the directory name when omitted. |
| `description` | no | One-line description shown in tooling. |
| `provides` | no | List of handler names registered by this toolkit. Every entry must start with `<name>.`. Used for documentation and consistency checks; the source of truth for dispatch is still the `@marcel_tool` decorator. |
| `requires` | no | Dict of resources the toolkit needs to function. Recognised keys: `credentials`, `env`, `files`, `packages`. Unknown keys log a warning and are ignored. |
| `isolation` | no | `inprocess` (default) or `uds`. See [Isolation modes](#isolation-modes). |
| `scheduled_jobs` | no | List of background job declarations — see [Scheduled jobs](#scheduled-jobs). |

Validation rules — any failure logs an error and skips metadata
registration; the handlers continue to dispatch normally, but
`depends_on:` resolution against this toolkit will return `None`
(treated as "requirements not met"):

- `name` must equal the directory name.
- `provides` must be a list of strings, all in the `<name>.*` namespace.
- `requires` must be a mapping.

A habitat without `toolkit.yaml` logs a warning and registers no
metadata — perfectly valid for toolkits that no skill depends on, but
means future skills cannot link to it via `depends_on:`.

### Directory-name ↔ handler-namespace rule

The directory name must match the `family` segment of every handler
name registered by the package:

| Toolkit dir | Allowed handler names | Rejected handler names |
|---|---|---|
| `demo/` | `demo.ping`, `demo.status` | `container.start`, `other.x` |
| `banking/` | `banking.balance`, `banking.transactions` | `money.total` |

If any handler registered by the package falls outside the namespace,
**the entire toolkit is rolled back**: no partial state leaks into the
registry. The failure is logged at ERROR level; discovery of sibling
toolkits continues normally.

This rule exists so a toolkit's dotted handler prefix is a stable
reverse-lookup to its source directory — useful for skills that
declare `depends_on:` (see [Skills](skills.md), ISSUE-6ad5c7).

### Error isolation

Errors in one toolkit never abort discovery of its siblings:

- `__init__.py` raises at import time → logged, that toolkit is
  skipped, siblings load.
- Handler registered outside the directory's namespace → logged, that
  toolkit rolled back, siblings load.
- Directory without `__init__.py` → logged as a warning, treated as
  not a habitat.

The net effect is that a user's marcel-zoo checkout can have one
broken toolkit without taking the rest of the install down.

## Isolation modes

A toolkit's `toolkit.yaml` can declare one of two `isolation:` modes,
controlling whether its Python code runs inside the kernel or in a
separate OS process. This is the target architecture from
[ISSUE-f60b09](https://github.com/shbunder/marcel/blob/main/project/issues/closed/ISSUE-260420-f60b09-uds-isolation-phase-1.md)
— Phase 1 shipped the mechanism, existing habitats still default to
`inprocess`.

```yaml
# toolkit.yaml
name: docker
description: Manage docker containers on the home NUC
isolation: uds            # optional; default = inprocess
provides:
  - docker.list
  - docker.status
requires:
  packages: [docker]      # phase 2 installs these into the habitat's own .venv
```

**`isolation: inprocess`** — today's default. The habitat's
`__init__.py` is imported into the kernel process via
`importlib.util.spec_from_file_location`; `@marcel_tool` populates the
kernel-local registry directly. Zero latency overhead, shared Python
heap, shared venv.

**`isolation: uds`** — the habitat runs as a separate subprocess with
its own venv, listening on a UDS socket under
`<data_root>/sockets/<name>.sock` (mode `0600`, user-only). The kernel
registers proxy coroutines that forward calls over the socket using
JSON-RPC 2.0 framed with a 4-byte big-endian length prefix:

```text
[4-byte BE length][JSON body]
```

Request body:

```json
{"jsonrpc": "2.0", "id": 1, "method": "docker.list",
 "params": {"params": {...}, "user_slug": "alice"}}
```

Response bodies — success echoes the id with `result`; errors echo the
id with `error` and a JSON-RPC-standard code:

| Code | Meaning |
|---|---|
| `-32700` | parse error (malformed JSON or framing) |
| `-32601` | method not found |
| `-32000` | handler raised an exception — message carries `<ExceptionClass>: <str>` |

The kernel's supervisor (`marcel_core.plugin._uds_supervisor`) spawns
each UDS habitat at `lifespan()` startup, polls `Popen.poll()` every
two seconds, and respawns any that exit uncleanly with exponential
backoff (1 s → 2 s → 4 s → … capped at 60 s). On `lifespan()` teardown,
SIGTERM to every child, five-second grace window, then SIGKILL.

What UDS buys over `inprocess`:

- **Dependency isolation** — habitat A can use `caldav==0.11` while
  habitat B uses `caldav==0.12`.
- **Failure isolation** — a habitat that segfaults or deadlocks
  doesn't take the kernel down; the supervisor restarts it.
- **Concurrent calls per habitat** — the bridge's accept loop handles
  multiple clients in parallel; stdio-based patterns serialise on one
  pipe.

What it costs:

- Per-call RPC overhead (≈ connect + JSON round-trip, dominated by
  kernel-local UDS latency — single-digit milliseconds).
- Connection timing: during a supervisor respawn, the
  `unlink-then-bind` window can produce transient
  `ConnectionRefusedError`/`FileNotFoundError`; the proxy retries up
  to 3 s with exponential backoff before surfacing the error.
- Credentials flow in RPC params — kernel decrypts, passes over UDS.
  Same level of exposure as in-process for a single-user home NUC;
  not suitable for multi-tenant / third-party-habitat scenarios
  without a capability model.

The long-term direction is `isolation: uds` for every Python habitat —
toolkits in **Phase 2** (shipped under ISSUE-14b034: `docker`, `news`,
`banking`, `icloud` all run UDS-isolated today), channels/jobs in
**Phase 3** (ISSUE-931b3f), with the `inprocess` path removed in
**Phase 4** (ISSUE-807a26). Markdown-only habitats (skills, subagents)
stay in-process because there is no Python code to isolate.

### Migrating an inprocess habitat to UDS

Each migration touches two files in the zoo and runs one kernel-side
script. The kernel itself does not change.

1. **Declare `isolation: uds`** in the habitat's `toolkit.yaml`:

   ```yaml
   name: myhabitat
   description: What this habitat does
   isolation: uds              # ← the one line that flips the shape
   provides:
     - myhabitat.action
   ```

2. **Write `<habitat>/pyproject.toml`** with the habitat's
   *non-kernel* deps. `marcel-core` is installed automatically by
   `scripts/zoo-setup.sh` so the bridge subprocess can import it —
   you only declare deps the habitat itself uniquely needs.

   Habitats that use only stdlib + kernel-transitive deps (`httpx`,
   `PyJWT`, `yaml`) declare an empty `dependencies = []` list. The
   migration is still worthwhile: UDS buys **failure isolation** even
   when there is no dep isolation to win.

   ```toml
   [project]
   name = "marcel-toolkit-myhabitat"
   version = "0.1.0"
   requires-python = ">=3.11,<3.13"
   dependencies = [
       "some-pinned-lib>=1.2.3",   # or [] for stdlib-only habitats
   ]
   ```

3. **Drop the same deps from the zoo root `pyproject.toml`.** Once a
   habitat owns its deps, duplicating them at the root causes version
   drift. The rule of thumb: the root `pyproject.toml` contains only
   deps for *inprocess* habitats; every UDS habitat is self-contained.

4. **Run `./scripts/zoo-setup.sh`.** The script walks
   `<zoo>/toolkit/*/`, detects `isolation: uds`, creates
   `<habitat>/.venv` via `uv venv --python 3.12`, and installs
   `marcel-core` (editable, from the kernel checkout) + the habitat's
   declared deps into it. Idempotent on re-runs.

5. **Verify.** Import-smoke the bridge entry point from inside the
   habitat venv:

   ```bash
   ./toolkit/myhabitat/.venv/bin/python -c \
       "import marcel_core.plugin._uds_bridge; print('ok')"
   ```

   Then run the kernel's `discover()` and check the supervisor
   spawned the subprocess — the kernel logs
   `uds-supervisor: spawned habitat 'myhabitat' (pid=...)` on
   success.

**Rollback** is a one-line edit: remove `isolation: uds` from the
habitat's `toolkit.yaml` and the kernel drops back to `inprocess`
dispatch on the next discovery. No kernel restart required beyond
whatever `request_restart()` would normally trigger.

## Scheduled jobs

A toolkit can declare periodic background work by adding a
`scheduled_jobs:` block to its `toolkit.yaml`. Each entry becomes a
system-scope [`JobDefinition`](jobs.md#data-models) at scheduler
startup — same retry, alerting, notification, and observability story
as other jobs. No kernel code changes required.

### Why declarative

Marcel's periodic jobs are not raw cron handlers; they are full **jobs**
dispatched through the scheduler. That keeps two cases on one pipeline:

- **Deterministic case** — "call handler X on cron Y, report the
  result." Declare three fields and you are done. If the handler
  owns its own idempotency and the caller doesn't want LLM
  involvement, combine this with `dispatch_type: tool` on the job
  (see [Jobs](jobs.md#dispatch-types)) to skip the agent turn
  entirely.
- **LLM-creative case** — "every morning, summarize today's calendar
  and surface conflicts." Override `task` / `system_prompt` / `model`
  per entry to inject the prompt the agent should run.

The alternative imperative shape (a `register_scheduled(scheduler)`
callback in `__init__.py`) was considered and rejected — see
[ISSUE-82f52b](https://github.com/shbunder/marcel/blob/main/project/issues/closed/ISSUE-260418-82f52b-scheduled-jobs-from-habitats.md):
declarative data is auditable without import side effects, validates
uniformly, and rolls back uniformly.

### Schema

```yaml
# toolkit.yaml
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
| `name` | yes | Unique within the toolkit *and* across every other loaded habitat's job names. Used as the `JobDefinition.name`. |
| `handler` | yes | Must appear in this toolkit's `provides:` list. |
| `cron` | XOR with `interval_seconds` | Standard 5-field cron expression validated by croniter. |
| `interval_seconds` | XOR with `cron` | Positive integer. |
| `params` | no | Dict passed to the handler. Stringify your values — Marcel does not coerce types. |
| `notify` | no | `silent` by default. `on_failure` is the right choice for sync jobs that should only ping the user when something breaks. |
| `task`, `system_prompt`, `model` | no | Per-entry overrides. The defaults synthesise a "call handler X with these params and report" prompt. |

### Lifecycle

- **First discovery.** Each spec is materialised as a `JobDefinition`
  with a deterministic ID (`sha256("<toolkit>:<name>")[:12]`), saved
  to the same `<data_root>/jobs/<slug>/` flat layout as every other
  job. The job is `template='habitat:<name>'` so reconciliation can
  find it later.
- **Subsequent restarts.** Already-on-disk jobs (matched by stable
  ID) are left untouched — user edits to the JOB.md file survive.
  Add a new entry → it appears next startup.
- **Reconciliation.** On every scheduler rebuild, any job with
  `template='habitat:<name>'` whose habitat is no longer in the
  metadata registry — or whose entry name no longer appears in that
  habitat's `scheduled_jobs:` — is **deleted from disk**. Uninstalling
  a toolkit (removing its directory) cleanly removes its jobs too.

### Validation and rollback

`scheduled_jobs:` is the **strict** part of `toolkit.yaml`. Where a
malformed `provides:` only suppresses metadata (handlers keep
dispatching), a malformed `scheduled_jobs:` entry **rolls back the
entire toolkit**: handlers are removed from the registry, no metadata
is published, the scheduler never sees the broken state.

This is the same all-or-nothing principle the
directory-name ↔ handler-namespace check uses (ISSUE-6ad5c7). The
reasoning: a half-shipped scheduled job is a silent gap users would
not notice — they would only learn about it the next time the missing
job *should have* fired.

Conditions that trigger toolkit rollback:

- `scheduled_jobs:` is not a list, or an entry is not a mapping
- Missing/empty `name`, missing `handler`
- `handler` not declared in `provides:`
- Neither (or both) of `cron` / `interval_seconds` set
- `cron` fails croniter validation
- `interval_seconds` is non-positive or boolean
- Duplicate `name` within the toolkit
- `name` collision against a different already-loaded habitat
- `notify` not in `{always, on_failure, on_output, silent}`
- `params` is not a mapping

All such failures log at ERROR level naming the offending toolkit and
entry; sibling toolkits continue loading.

## What `marcel_core.plugin` exposes

```python
from marcel_core.plugin import marcel_tool, get_logger, ToolkitHandler
from marcel_core.plugin import credentials, paths, models, rss
```

### Top-level

| Symbol | Purpose |
|---|---|
| `marcel_tool(handler_name)` | Decorator that registers an async handler. Validates the `family.action` naming convention. `@register` is a back-compat alias (see [Back-compat aliases](#back-compat-aliases)). |
| `ToolkitHandler` | Type alias for the handler signature: `Callable[[dict, str], Awaitable[str]]`. |
| `get_logger(name)` | Returns a module logger. Prefer this over `logging.getLogger` directly so future plugin-specific filtering hooks can be added without rewriting habitats. |
| `register_channel(plugin)` | Register a channel habitat with the channel registry. See [Channels](channels.md). |

### `marcel_core.plugin.credentials`

Per-user credential storage. Encrypted with `MARCEL_CREDENTIALS_KEY`
when set, plaintext fallback otherwise — habitats need not care which.

| Symbol | Purpose |
|---|---|
| `load(slug) -> dict[str, str]` | Read every key/value pair stored for the user. Returns `{}` when no file exists. |
| `save(slug, creds: dict[str, str])` | Overwrite the user's credential file with *creds*. Writes are atomic and chmod'd to `0600`. |

`save()` replaces the entire blob, so the standard pattern is
read–mutate–write rather than per-key set:

```python
from marcel_core.plugin import credentials

creds = credentials.load(user_slug)
creds["MY_SERVICE_API_KEY"] = new_value
credentials.save(user_slug, creds)
```

### `marcel_core.plugin.paths`

Per-user filesystem helpers. Hides the data-root layout so a habitat
never sees `<data_root>/users/{slug}/...` literally.

| Symbol | Purpose |
|---|---|
| `user_dir(slug) -> Path` | The user's data directory. **Not** created by this call — caller does `mkdir(parents=True, exist_ok=True)` on the specific subpath it needs. |
| `cache_dir(slug) -> Path` | The user's cache subdirectory, created on first call. Use this for any `*.db` / `*.json` cache file the habitat owns. |
| `list_user_slugs() -> list[str]` | The slugs of every existing user — used by sync loops that need to enumerate linked accounts. Returns `[]` when no users dir exists. |

```python
from marcel_core.plugin import paths

cache_file = paths.cache_dir(user_slug) / "mything.db"
key_file = paths.user_dir(user_slug) / "signing_key.pem"
for slug in paths.list_user_slugs():
    sync_one_user(slug)
```

### `marcel_core.plugin.models`

Model registry + per-channel preference, used by the settings toolkit
to render and persist model choices.

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

### `marcel_core.plugin.rss`

RSS / Atom feed fetcher, used by the news toolkit to pull syndication
feeds without reaching into `marcel_core.tools.*`.

| Symbol | Purpose |
|---|---|
| `fetch_feed(url, max_articles=50) -> list[dict[str, str]]` | Fetch and parse an RSS / Atom URL. Each article dict has `title`, `link`, `description`, `published`, `category` (keys present when the source provides them). Raises `ValueError` for non-XML / empty bodies, `httpx.HTTPStatusError` for non-2xx responses — callers log and move on. |

```python
from marcel_core.plugin import rss

articles = await rss.fetch_feed("https://www.vrt.be/vrtnws/nl.rss.articles.xml")
for art in articles:
    print(art["title"], art["link"])
```

Anything not listed above is internal — zoo code that imports it owns
the breakage on any future Marcel upgrade.

## Where toolkits live

The kernel ships zero bundled toolkits. Every real toolkit lives in
marcel-zoo — `docker` (ISSUE-6ad5c7), `icloud` (ISSUE-e7d127), `news`
(ISSUE-d5f8ab), and `banking` (ISSUE-13c7f2) have all migrated out. The
settings toolkit handler was retired as dead code under ISSUE-e1b9c4 —
the live settings surface is the `marcel(action="...")` utility tool,
not a `toolkit(id="settings.*")` handler.

## Back-compat aliases

The five-habitat taxonomy rename (ISSUE-3c1534) is in a multi-phase
rollout. During the transition, the kernel accepts the legacy names so
existing zoo checkouts keep working:

| Legacy name | Canonical name | Phase removed |
|---|---|---|
| `integrations/` directory | `toolkit/` | Phase 5 |
| `integration.yaml` | `toolkit.yaml` | Phase 5 |
| `@register(...)` | `@marcel_tool(...)` | Phase 5 |
| `integration(id=...)` tool | `toolkit(id=...)` | Phase 5 |
| `IntegrationHandler` | `ToolkitHandler` | Phase 5 |

The kernel walks both `toolkit/` and `integrations/` directories and
accepts both decorator names during the migration. A habitat in
`integrations/` with a `integration.yaml` still loads identically to
its modern counterpart. New habitats should use the canonical names
exclusively.

## See also

- [Habitats](habitats.md) — the five-kind taxonomy.
- [Skills](skills.md) — the paired markdown layer that teaches the
  agent *when* to reach for a toolkit handler.
- [Jobs](jobs.md) — how `scheduled_jobs:` entries and standalone
  `template.yaml` files coexist under one job system.
- [Channels](channels.md) — the sibling transport habitat kind.
- [Storage](storage.md) — where `<data_root>` resolves and how
  per-user data is organised.
- [Architecture](architecture.md) — kernel / userspace model and where
  habitats sit in the overall design.
