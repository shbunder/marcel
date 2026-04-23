# Channels

A **channel habitat** is a concrete transport plugin — Telegram, Signal,
Discord — that lets Marcel receive messages from the outside world and
push responses back. Channel habitats live at
`<MARCEL_ZOO_DIR>/channels/<name>/` and self-register with the kernel
at discovery time so `main.py` can mount their routers, the prompt
builder can query their capabilities, and push helpers (`notify`,
charts, artifact links) can deliver without importing transport-
specific modules.

See [Habitats](habitats.md) for how channels fit alongside the other
four kinds. This page is the kind-level deep dive; see
[Telegram](channels/telegram.md) for the one concrete channel habitat
shipped in the zoo today.

!!! note "Kernel-native surfaces are not channel habitats"
    `websocket`, `cli`, `app`, `ios`, and `macos` are built into the
    kernel — they handle their own routing and delivery. Only concrete
    transport plugins (Telegram and its future siblings) ship as
    habitats. The distinction matters for channel-prompt resolution
    (see [Channel guidance in the system prompt](#channel-guidance-in-the-system-prompt)).

## Bidirectional architecture

Every channel habitat has two sides — inbound (receiving messages) and
outbound (pushing replies or notifications):

```text
Inbound                                Outbound
─────────────────────────              ──────────────────────────────
External service                       Kernel code
    │                                      │
    ▼                                      ▼
FastAPI router (channel.router)        get_channel(name).send_message
    │                                      │
    ▼                                      ▼
Handler calls stream_turn(...)         Habitat translates to transport
    │                                      │
    ▼                                      ▼
Agent loop produces response  ───────► send_message / send_photo /
                                         send_artifact_link
```

The inbound side is a regular [FastAPI](https://fastapi.tiangolo.com/)
`APIRouter` mounted at `main.py` startup. Webhooks, POST endpoints for
REST-style integrations, WebSocket upgrade handshakes — any HTTP-shaped
inbound path lives in the router.

The outbound side is the three push methods on the `ChannelPlugin`
Protocol (`send_message`, `send_photo`, `send_artifact_link`). Kernel
code reaches them via `get_channel(name).send_message(user_slug, text)`
— never by importing transport-specific modules. This is the seam that
makes the channel layer pluggable: adding a new transport never
requires touching the scheduler, the job executor, or the notify tool.

## Directory layout

```text
<MARCEL_ZOO_DIR>/channels/<name>/
├── __init__.py          # required — imports register_channel() at load time
├── channel.yaml         # required — name, description, capabilities, requires
├── CHANNEL.md           # optional — operator-copyable template for channel guidance
├── <transport>.py       # bot.py, webhook.py, sessions.py, formatting.py, …
└── tests/               # habitat-owned tests (run from the zoo checkout)
```

The habitat's `__init__.py` **must** call `register_channel(plugin)` at
import time — discovery is side-effect-driven, mirroring the toolkit
registry. Use **relative imports** inside the habitat
(`from . import bot, sessions`); the kernel loads habitats under the
private `_marcel_ext_channels.<name>` namespace, not under
`marcel_core.*`, so absolute imports of sibling modules will fail at
runtime.

## Minimal example

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

After a kernel restart, `list_channels()` contains `'demo'`, `main.py`
mounts `router` at `/channels/demo/*`, and kernel code can push via
`get_channel('demo').send_message(user_slug, text)`.

## The `ChannelPlugin` protocol

[`marcel_core.plugin.channels.ChannelPlugin`](https://github.com/shbunder/marcel/blob/main/src/marcel_core/plugin/channels.py)
is the runtime-checkable Protocol every habitat satisfies — duck-typed,
so a plugin may be a class instance, a dataclass, or any object
exposing the right names:

| Member | Purpose |
|---|---|
| `name: str` | Unique channel identifier. Must equal the directory name. |
| `capabilities: ChannelCapabilities` | Declares `markdown`, `rich_ui`, `streaming`, `progress_updates`, `attachments`. The prompt builder reads `rich_ui` to decide whether to inject the A2UI component catalog. |
| `router: APIRouter \| None` | Optional FastAPI router. Kernel-internal transports (e.g. the WebSocket) can return `None` if routing lives elsewhere. |
| `async send_message(user_slug, text) -> bool` | Deliver a text message; `False` when the recipient is not registered. |
| `async send_photo(user_slug, image_bytes, caption=None) -> bool` | Deliver an image; `False` when unsupported or unresolved. |
| `async send_artifact_link(user_slug, artifact_id, title) -> bool` | Deliver an artifact (e.g. a Telegram Mini App button). Only meaningful when `capabilities.rich_ui` is true. |
| `resolve_user_slug(external_id) -> str \| None` | Map a transport-side identity (e.g. a Telegram user id, stringified) to a Marcel user slug. Channels without a separate identity space return `None`. |

### `ChannelCapabilities`

| Field | Default | Meaning |
|---|---|---|
| `markdown` | `True` | Supports markdown formatting. |
| `rich_ui` | `False` | Supports cards, buttons, structured artifacts (A2UI components). Drives whether the prompt builder injects the component catalog. |
| `streaming` | `True` | Real-time token streaming. `False` means buffer and send the final message. |
| `progress_updates` | `True` | Can show intermediate progress (via `marcel(action="notify")`). |
| `attachments` | `False` | Can receive and send files. |

Capabilities are the declared truth about what the transport supports.
A habitat that returns `False` from a delivery method but declares the
capability as `True` is a habitat bug — kernel code trusts the flags.

## Channel metadata (`channel.yaml`)

| Key | Required | Notes |
|---|---|---|
| `name` | yes | Must equal the directory name. |
| `description` | no | One-line description for tooling. |
| `requires.credentials` | no | List of env-var names the channel needs (e.g. `TELEGRAM_BOT_TOKEN`). Today this is advisory — the kernel does not yet gate discovery on credential presence; the habitat's own `__init__.py` is expected to fail gracefully when required env vars are missing. |
| `capabilities` | no | Redundant declaration of what `ChannelCapabilities(...)` in `__init__.py` already says. Kept for tooling that wants to inspect the habitat without running its Python. |

## Discovery and error isolation

[`marcel_core.plugin.channels.discover()`](https://github.com/shbunder/marcel/blob/main/src/marcel_core/plugin/channels.py)
is called once from `main.py` at module load before the router-mount
loop. It walks `<MARCEL_ZOO_DIR>/channels/` and imports every
subdirectory.

- A habitat that raises at import time is logged and skipped; siblings
  continue loading. The net effect: a user's zoo can have one broken
  channel without taking the whole install down.
- If `MARCEL_ZOO_DIR` is unset or `<zoo>/channels/` does not exist,
  `discover()` is a silent no-op — the kernel still boots, just without
  any channel habitat mounted.
- Subsequent calls to `discover()` are idempotent: already-loaded
  habitats are skipped via their presence in `sys.modules`.

The orchestrator wraps `discover()` as
[`ChannelHabitat.discover_all`](https://github.com/shbunder/marcel/blob/main/src/marcel_core/plugin/habitat.py)
so logging and admin tooling see channels on the same uniform surface
as the other four kinds.

## Channel guidance in the system prompt

The kernel injects a per-channel system-prompt fragment that tells the
agent how to format for that surface. Resolution order (first hit
wins):

1. **`<MARCEL_DATA_DIR>/channels/<name>.md`** — per-install override.
   Drop any file here and it becomes the channel's guidance on the
   next turn (cold read, no restart).
2. **`src/marcel_core/channel_prompts/<name>.md`** — bundled prompts
   for kernel-native surfaces: `app.md`, `cli.md`, `ios.md`, `job.md`,
   `websocket.md`.
3. **Generic fallback.** `Respond in a format appropriate for the
   <name> channel.`

Habitat channels ship a `CHANNEL.md` at the top of their directory as a
**template** the operator can copy to `<data_root>/channels/<name>.md`
to activate it. The file is not auto-wired today — the kernel
deliberately separates transport code (habitat) from agent guidance
(data root) so an operator can tune prompt behaviour without touching
the habitat package.

See [`build_instructions_async`](https://github.com/shbunder/marcel/blob/main/src/marcel_core/harness/context.py)
for the full system-prompt assembly — the channel block is one of five
H1 sections.

## Push delivery from kernel code

Kernel code never imports a transport module. It asks the registry:

```python
from marcel_core.plugin.channels import get_channel

tg = get_channel('telegram')
if tg is not None:
    await tg.send_message(user_slug, text)
```

Examples of push-delivery sites in the kernel:

- **`marcel(action="notify", message=...)`** — the agent's explicit
  progress-update tool. Looks up the current channel from
  `deps.channel`, calls `send_message`.
- **Scheduler** — a job with `notify: always` / `on_failure` / `on_output`
  pushes its result through `get_channel(job.channel).send_message`
  after the run.
- **`generate_chart`** — renders a matplotlib figure and delivers via
  `send_photo` on channels with `capabilities.attachments=True`.

Because every push goes through the registry, the scheduler and notify
tool never need to know whether the transport is Telegram, WebSocket,
or a future SMS channel.

## UDS roadmap

Channel habitats today run **in-process** — the kernel imports each
habitat's `__init__.py` and shares its Python heap. Under
[ISSUE-f60b09](https://github.com/shbunder/marcel/blob/main/project/issues/closed/ISSUE-260420-f60b09-uds-isolation-phase-1.md)
the kernel gained a UDS isolation path for toolkit habitats; channels
are explicitly out of scope in Phase 1 and tracked under **ISSUE-931b3f**
for Phase 3.

What UDS isolation would buy channel habitats:

- **Dependency isolation** — a channel that needs a specific
  `python-telegram-bot` version could pin it without coordinating with
  other habitats.
- **Failure isolation** — a channel that segfaults or deadlocks in its
  webhook handler wouldn't take the kernel down; the supervisor would
  restart it.

The main design question deferred to that issue: inbound routers
currently mount directly into the kernel's FastAPI app. A UDS-isolated
channel needs a proxy router in the kernel that forwards webhook
bodies over the socket to the habitat subprocess, which hosts the real
router. Same JSON-RPC framing as toolkits, but carrying an HTTP
request/response instead of a handler call.

Until that ships, channel habitats are in-process and must treat
`send_message` failures as soft errors so one flaky transport does not
cascade into the whole delivery pipeline.

## See also

- [Habitats](habitats.md) — the five-kind taxonomy.
- [Telegram](channels/telegram.md) — the sole concrete channel habitat
  shipped today.
- [Plugins (toolkit)](plugins.md) — the sibling Python-habitat kind; its
  UDS model (Phase 1 in ISSUE-f60b09) is the reference for the future
  channels UDS work.
- [Architecture](architecture.md) — where channels fit in the kernel as
  a whole.
