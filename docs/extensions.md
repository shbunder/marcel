# Extensions — `register(marcel)` and the `marcel-sdk` wall

This page is for **developers building extensions on Marcel**. It covers the
one extension entrypoint and the one import surface you use. (The *why* —
the core-internal design rationale — lives in the ADRs on the marcel-admin
board; you don't need it to write an extension.)

An **extension** is a Python module exposing `def register(marcel)`. Running
that function *is* the registration — no base class, no manifest. Extensions
import **only** [`marcel_sdk`](#the-marcel-sdk-import-wall), never
`marcel_core` internals.

This model sits alongside the [five-kind habitat taxonomy](habitats.md):
`register(marcel)` is the single object through which an extension registers
any of those kinds.

## Writing an extension

An extension lives at `$MARCEL_ZOO_DIR/extensions/<name>/__init__.py` (a
package) or `$MARCEL_ZOO_DIR/extensions/<name>.py` (a single file):

```python
from marcel_sdk import get_logger

log = get_logger(__name__)


def register(marcel):
    # A connector habitat — an MCP server + per-user auth, by park path.
    marcel.connector("parks/demo")

    # An event-bus subscription — observe or gate every tool call.
    def audit(event, ctx):
        log.info("%s called %s", ctx.user_slug, event.tool_name)

    marcel.on("tool_call", audit)
```

Marcel discovers and runs every `register(marcel)` at startup. A broken
extension is logged and skipped — never fatal to the others. Discovery is
idempotent, so a redeploy re-runs cleanly.

## The `marcel` object — `ExtensionAPI`

`marcel` implements the `marcel_sdk.ExtensionAPI` protocol. Every capability
is a method:

| Method | Registers | Status |
|---|---|---|
| `marcel.on(event, handler)` | A lifecycle [event-bus](#the-lifecycle-event-bus) subscription, applied to every turn. | **Live**. |
| `marcel.channel(plugin)` | A channel plugin (transport + formatting). | **Live**. |
| `marcel.connector(source)` | A [connector](connectors.md) habitat by its `connector.yaml` directory path. | **Live** (FEAT-260707-acb2b6) — discovered by the connector loader, subject to the same validation, role and [enablement](marketplace.md) filters as zoo habitats; zoo/data habitats override on a name collision. |
| `marcel.skill(source)` | A skill habitat by path. | Recorded; loader wiring lands in F1. |
| `marcel.job(source)` | A job template by path. | Recorded; loader wiring lands in F1. |
| `marcel.agent(source)` | A subagent by path. | Recorded; loader wiring lands in F1. |
| `marcel.command(name, handler)` | A platform/slash command. | Recorded; wiring lands in F1. |
| `marcel.tool(name)` | **Deprecated no-op** — the toolkit habitat [retired](plugins.md) (FEAT-260718-c232d9). The decorator warns and does **not** register the handler; register a connector instead. | Shim for one release. |

`@marcel_tool("x.y")` (importable from `marcel_sdk` and
`marcel_core.plugin`) is the same shim in decorator form: it imports, warns,
and registers nothing. Port stragglers to a connector — see
[Toolkit habitats — retired](plugins.md).

## The `marcel-sdk` import wall

`marcel_sdk` is the **only** package an extension should import. It carries
its own `marcel_sdk.__version__`, **decoupled from the `marcel-core` kernel
version**: a change to kernel internals that keeps this surface intact does
not bump the SDK; a change to the surface does, with a migration note. That
is your compatibility boundary — pin against it, not against kernel
internals.

What it exposes:

- **Contracts**: `ExtensionAPI`, `ToolResult`, and the event bus —
  `EventBus`, `EventContext`, and the event types (`ToolCallEvent`,
  `ToolResultEvent`, …).
- **Helpers**: `credentials`, `paths`, `models`, `rss`, and `get_logger`.
  (`marcel_tool` is still importable, but only as the one-release
  [deprecation shim](plugins.md#the-deprecation-shim) — it registers
  nothing.)

Importing `marcel_core.*` from an extension defeats the wall — the kernel
may refactor internals at any time, so an extension that reaches past
`marcel_sdk` owns its own breakage.

## The lifecycle event bus

A typed event bus fires at fixed points in every turn. Subscribe with
`marcel.on(event, handler)`. A handler is `(event, ctx) -> None` — sync or
async. It **observes** by reading the event, **mutates** by writing the
event's fields in place, and **blocks** (where supported) via
`event.deny(reason)`. Handlers run in subscription order; a blocked
`tool_call` short-circuits the rest.

| Event | When | A handler can |
|---|---|---|
| `session_start` | turn begins | observe |
| `input` | user's cleaned text | observe |
| `before_agent_start` | system prompt built | rewrite `event.system_prompt` |
| `before_provider_request` | before each model call | observe `event.model` / `event.tier` |
| `tool_call` | before a tool runs | mutate `event.args` in place; `event.deny(reason)` to block |
| `tool_result` | after a tool runs | rewrite `event.result` / `event.is_error` |
| `agent_end` | response complete | observe `event.response_text` |
| `resources_discover` | discovery | append to `event.skill_paths` / `event.prompt_paths` |

`ctx` (`EventContext`) carries the turn's `user_slug` and `role`.

### `tool_call` / `tool_result` in practice

```python
def register(marcel):
    def guard(event, ctx):
        # mutate args in place …
        if event.tool_name == "run_command":
            event.args["command"] = sanitise(event.args["command"])
        # … or block outright
        if event.tool_name == "run_command" and "rm -rf /" in event.args.get("command", ""):
            event.deny("refusing a destructive command")

    marcel.on("tool_call", guard)

    def redact(event, ctx):
        event.result = event.result.replace(a_secret(), "***")

    marcel.on("tool_result", redact)
```

Three of Marcel's own behaviours already ride this bus as `tool_call`
handlers, so you can rely on them and pattern-match them:

- **Role-gating** — admin-tier tools are blocked in non-admin turns (on top
  of never being registered there).
- **Self-modification path guard** — writes to `CLAUDE.md`, the auth module,
  core config, or `.env*` are blocked unless `.claude/.unlock-safety` exists.
- **Command policy + human approval** — a declarative allow/ask/deny policy
  over the shell-command surface (`run_command`, `start_command`). It **denies** shell
  commands that touch the self-mod boundary and **asks** before genuinely
  destructive ones. An `ask` pauses the turn and forwards a plain-language
  prompt with *Allow once / Always / Deny* buttons to the user's Telegram;
  the action runs only on an explicit allow. No answer within
  `marcel_approval_timeout_seconds` (default 120) → deny, with an auditable
  pending record queued for later under `~/.marcel/approvals/`. "Allow
  always" amends the policy for that exact command. Disable the whole layer
  with `marcel_command_policy_enabled=false`.

These run in that order (a denial short-circuits the rest), and your own
`tool_call` handlers run after them — so an extension can add its own
allow/deny rules on top.

## Relationship to the five habitat kinds

The [five-kind taxonomy](habitats.md) describes *what* you can build
(skill / connector / channel / job / subagent). `register(marcel)` is *how*
an extension registers them — through one object instead of five separate
discovery paths. Both coexist today: existing per-kind habitats load as
before; new extensions use `register(marcel)`.
