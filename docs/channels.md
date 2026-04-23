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

## UDS isolation — design

Channel habitats today run **in-process** — the kernel imports each
habitat's `__init__.py` and shares its Python heap. Phases 1 + 2 of
the UDS isolation story
([ISSUE-f60b09](https://github.com/shbunder/marcel/blob/main/project/issues/closed/ISSUE-260420-f60b09-uds-isolation-phase-1.md)
and
[ISSUE-14b034](https://github.com/shbunder/marcel/blob/main/project/issues/closed/ISSUE-260422-14b034-zoo-integrations-to-uds.md))
landed UDS for the toolkit kind. Channel UDS is architecturally the
most complex piece because channels are **bidirectional**:

- **Inbound** — an external transport pushes webhooks into the
  kernel's FastAPI server, which must route to the habitat.
- **Outbound** — the kernel calls `send_message`/`send_photo`/
  `send_artifact_link` to push text, images, and artifact links out.
- **Mid-turn notify** — while handling an inbound webhook, the habitat
  runs a full agent turn that can itself call
  `marcel(action="notify")`, which pushes progress messages back
  through the same habitat. Habitat → kernel → habitat, recursively.

What follows is the design shipped under
[ISSUE-931b3f](https://github.com/shbunder/marcel/blob/main/project/issues/closed/ISSUE-260422-931b3f-channels-and-jobs-to-uds.md);
the kernel implementation + Telegram migration are carved out to a
follow-up **(Phase 3a)**.

### What UDS isolation buys channels

- **Dependency isolation** — a channel that needs a specific
  `python-telegram-bot` or `pysignal` version pins it without
  coordinating with other habitats.
- **Failure isolation** — a channel that segfaults or deadlocks in
  its webhook handler doesn't take the kernel down; the supervisor
  restarts it.
- **Concurrency per habitat** — the bridge's accept loop handles
  multiple inbound webhooks in parallel without blocking the kernel.

### Outbound path — trivial extension of toolkit UDS

The existing `ChannelPlugin` methods map directly to JSON-RPC methods
on the habitat's UDS socket, same framing as toolkit handlers today:

| Protocol method | RPC method name | Params |
|---|---|---|
| `send_message(user_slug, text) -> bool` | `channel.send_message` | `{"user_slug": str, "text": str}` |
| `send_photo(user_slug, image_bytes, caption) -> bool` | `channel.send_photo` | `{"user_slug": str, "image_b64": str, "caption": str\|None}` |
| `send_artifact_link(user_slug, artifact_id, title) -> bool` | `channel.send_artifact_link` | `{"user_slug": str, "artifact_id": str, "title": str}` |
| `resolve_user_slug(external_id) -> str\|None` | `channel.resolve_user_slug` | `{"external_id": str}` |

Binary payloads (`send_photo`'s `image_bytes`) cross the wire as
base64 — the JSON-RPC envelope is JSON, not binary-tolerant. Kernel
code never sees the encoding: the proxy coroutine base64-encodes on
send, the habitat bridge base64-decodes on receive, and the habitat's
real `send_photo` gets back `bytes`.

Capabilities (`markdown`, `rich_ui`, `streaming`, `progress_updates`,
`attachments`) are read from `channel.yaml` at kernel startup and
cached on the kernel side — no RPC on every
`channel_has_rich_ui()` call.

### Inbound path — generic HTTP-to-UDS proxy

The kernel owns the FastAPI app; a UDS-isolated channel's habitat
subprocess doesn't have network access. The solution is a **generic
proxy route** mounted by the kernel for every UDS-isolated channel:

```text
POST   /channels/{name}/{path:path}     → channel.webhook (POST)
GET    /channels/{name}/{path:path}     → channel.webhook (GET)
PUT    /channels/{name}/{path:path}     → channel.webhook (PUT)
DELETE /channels/{name}/{path:path}     → channel.webhook (DELETE)
PATCH  /channels/{name}/{path:path}     → channel.webhook (PATCH)
```

Rather than an inprocess channel's `APIRouter` being mounted
directly, the proxy handler for each inbound request:

1. Reads the full `Request` — method, path, query params, all
   headers (including HMAC-validated tokens like
   `X-Telegram-Bot-Api-Secret-Token`), body bytes.
2. Serialises into a JSON-RPC `channel.webhook` call:

   ```json
   {
     "jsonrpc": "2.0",
     "id": <auto>,
     "method": "channel.webhook",
     "params": {
       "method": "POST",
       "path": "/webhook",
       "query": {"k": "v"},
       "headers": {"X-Telegram-Bot-Api-Secret-Token": "..."},
       "body_b64": "<base64-encoded body>"
     }
   }
   ```

3. Forwards over the habitat's UDS socket with no timeout short of
   the kernel's request deadline (agent turns can run up to 120 s —
   see `_ASSISTANT_TIMEOUT` in
   `_marcel_ext_channels.telegram.webhook`).
4. Reconstructs a FastAPI `Response` from the RPC return:

   ```json
   {
     "jsonrpc": "2.0",
     "id": <echoed>,
     "result": {
       "status_code": 200,
       "headers": {"content-type": "application/json"},
       "body_b64": "<base64-encoded response body>"
     }
   }
   ```

Header preservation matters: HMAC validation in the habitat needs the
original bytes, so the proxy MUST NOT decode/re-encode any header
values. Pass them through as strings.

### Habitat-side: bridge exposes `channel.webhook`

The habitat's `_uds_bridge` entrypoint gains a `channel.webhook`
handler (alongside whatever toolkit-style handlers it may also
expose). The handler:

1. Reconstructs a FastAPI-compatible `Request` object from the RPC
   params. The ASGI scope is built from scratch; the body is an
   async generator that yields the base64-decoded bytes.
2. Dispatches through the habitat's own `APIRouter` — the **same
   router** the habitat would have mounted in-process. No duplicate
   code path.
3. Collects the `Response` (status + headers + body) and returns
   it through the JSON-RPC envelope.

This keeps the habitat's own webhook code identical whether it runs
inprocess or UDS-isolated. The only difference is where the
`Request` comes from (direct FastAPI dispatch vs. reconstructed from
RPC).

### Mid-turn notify — the reverse path

The Telegram webhook handler runs a full agent turn:

```python
async for event in stream_turn(user_slug, channel='telegram', ...):
    if isinstance(event, TextDelta):
        buffer.append(event.text)
    # elif ToolCallEvent for marcel(action="notify") …
```

When the agent emits a `marcel(action="notify", message="...")` call,
the kernel's notify tool calls `get_channel('telegram').send_message`
which — under UDS isolation — is a `channel.send_message` RPC back to
the habitat.

That's **two concurrent RPCs on the same socket in different
directions** for the duration of one inbound webhook:

1. The outer `channel.webhook` call (kernel → habitat, runs for up
   to 120 s).
2. One or more `channel.send_message` calls (kernel → habitat, each
   fires while the outer call is still pending).

The existing toolkit UDS bridge serialises on one request per
connection. Channel UDS therefore requires:

- **Per-call connection** for push methods (kernel opens a new
  connection for each `channel.send_message`). The bridge's existing
  accept loop handles parallel connections already, so this is a
  kernel-side change only.
- **Long-lived connection tolerance** for the outer `channel.webhook`
  call — the bridge must not time out the idle-on-return connection
  just because the handler takes 120 s.

Neither change is invasive: the kernel's per-call-connection proxy
is how `_make_uds_proxy` already works for toolkits (see
`src/marcel_core/toolkit/__init__.py`). Channel UDS extends that
shape without modifying it.

### Identity resolution

`resolve_user_slug(external_id) -> str | None` runs on **every
inbound webhook** (to map a Telegram user ID to a Marcel user slug).
Over UDS it becomes a `channel.resolve_user_slug` RPC — one extra
round-trip per webhook.

Two options:

- **(a) Naive — every call is an RPC.** Adds one UDS round-trip
  (~1-3 ms on kernel-local sockets) per inbound webhook. Simple,
  correct, always fresh.
- **(b) Cached — the habitat maintains an in-memory map and the
  kernel signals a refresh on user-add events.** Zero RPC on the
  hot path. Complexity: cache invalidation across user-add /
  user-delete events.

**Start with (a).** Profile after the migration lands — only move
to (b) if Telegram's per-message latency profile shows the extra
round-trip mattering. Single-digit milliseconds at the head of a
120-s agent turn is noise.

### Telegram-specific constraints

Migrating Telegram under this design requires:

- **`_marcel_ext_channels.telegram.webhook` imports** — the habitat
  subprocess needs `marcel_core.harness.runner.stream_turn`,
  `marcel_core.harness.turn_router.resolve_turn_for_user`,
  `marcel_core.memory.*`, `marcel_core.storage.artifacts.*`. The
  full kernel surface, installed into the habitat venv via
  `uv pip install -e $REPO_ROOT` (same pattern as toolkit UDS Phase 2).
- **HMAC validation** — the telegram bridge reads
  `X-Telegram-Bot-Api-Secret-Token` from the RPC-forwarded headers.
  The proxy route must preserve the header verbatim (no
  normalisation, no case-folding beyond HTTP's own rules).
- **Mini-App artifact delivery** — `send_artifact_link` pushes a
  Telegram inline keyboard with a `WebAppInfo` button. Pure text
  payload, fits the existing `channel.send_artifact_link` RPC
  shape without a photo-style base64 step.
- **Cold-start latency** — the first webhook after a kernel restart
  blocks on the supervisor's `spawn_habitat` wait. Measure; if
  the bridge import graph is too heavy (pydantic-ai alone is
  several hundred ms), the `_SOCKET_READY_TIMEOUT` in
  `_uds_supervisor` may need bumping past its current 5 s default.

### Configuration surface

`channel.yaml` gains an `isolation:` key matching `toolkit.yaml`:

```yaml
name: telegram
description: Telegram Bot API channel
isolation: uds
requires:
  credentials:
    - TELEGRAM_BOT_TOKEN
    - TELEGRAM_WEBHOOK_SECRET
capabilities:
  markdown: true
  rich_ui: true
  streaming: false
  progress_updates: true
  attachments: true
```

Default stays `inprocess` — operators opt in per habitat.

### What this design does NOT cover (out of scope for Phase 3a)

- **Signal / Discord channel habitats** — orthogonal features, not
  blockers.
- **WebSocket channel UDS** — the built-in WebSocket channel isn't a
  habitat; it lives in the kernel and doesn't need UDS.
- **Job habitats** — confirmed YAML-only today; zero Python code to
  isolate. Phase 3 closes the jobs half as designed.
- **Per-habitat rate limiting / auth** — separate concern, tracked
  independently.

### Implementation follow-up

This design ships in this issue; the kernel implementation + Telegram
migration are carved out to a separate follow-up that references this
section as its starting point. See
[`project/issues/open/`](https://github.com/shbunder/marcel/tree/main/project/issues/open/)
for the active queue.

Until the follow-up ships, channel habitats are in-process and must
treat `send_message` failures as soft errors so one flaky transport
does not cascade into the whole delivery pipeline.

## See also

- [Habitats](habitats.md) — the five-kind taxonomy.
- [Telegram](channels/telegram.md) — the sole concrete channel habitat
  shipped today.
- [Plugins (toolkit)](plugins.md) — the sibling Python-habitat kind; its
  UDS model (Phase 1 in ISSUE-f60b09) is the reference for the future
  channels UDS work.
- [Architecture](architecture.md) — where channels fit in the kernel as
  a whole.
