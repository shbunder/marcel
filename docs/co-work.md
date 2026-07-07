# The co-work loop — `code_exec` and `promote_extension`

Marcel's co-work loop lets the agent **prototype a computation, prove it works,
then keep it** as a durable capability — without a human hand-writing an
extension. Two admin-tier tools make the loop (F3):

1. **`code_exec`** — run Python **cells** in a persistent, sandboxed notebook.
   Iterate until the script does what you want.
2. **`promote_extension`** — render a proven script into a `register(marcel)`
   [extension](extensions.md), commit it to the zoo, and deploy it via the one
   legal [restart path](self-modification.md).

```
   code_exec (prototype, sandboxed)  ──prove──►  promote_extension (durable tool)
        ▲          │                                     │
        └── iterate cells ──┘                     next redeploy: a real toolkit
```

Both are **admin-tier** — they flow through the [event bus](extensions.md#the-lifecycle-event-bus)
(self-mod guard → role gate → command policy). Non-admins never see them.

## `code_exec` — the sandboxed notebook

`code_exec(code)` runs one Python **cell**. Within a turn, cells share **one
persistent namespace** — a name bound in one cell is visible in the next, like a
Jupyter kernel or a REPL. That is the *notebook* state model: the agent builds a
computation up incrementally.

```python
# cell 1
import json
data = json.loads(toolkit("http.get", url="https://api.example.com/rates"))
len(data)                      # → the repr of a trailing expression is echoed
```
```python
# cell 2 — `data` is still here
rate = data["EUR"]
f"1 USD = {rate} EUR"          # → '1 USD = 0.92 EUR'
```

Each cell returns:

- its captured **stdout** (a cell's own `print` output — never mixed into the
  protocol stream), then
- the **`repr` of a trailing bare expression** (REPL-style), or
- on failure, a **traceback trimmed to the cell's own frames** (the agent sees
  its cell line, not the harness internals).

### What a cell has in scope

| Name | What it is |
|---|---|
| `toolkit(tool_id, **params)` | Call any Marcel toolkit handler (`"family.action"`) and get its string result. Raises `ToolkitError` on failure — catchable in the cell. |
| `get_logger(name="cell")` | A stdlib logger; its output is captured with the cell's stdout. |
| `user_slug` | The acting user's slug. |
| `ToolkitError` | So a cell can `except ToolkitError` a failed `toolkit()` call. |

`toolkit()` is the cell's one line to the outside world. The cell runs in the
sandbox; the **handler runs in the kernel** (with its credentials, DB, and
network) and the result is passed back. A cell reaches only the toolkits the
admin already has — no escalation.

### The sandbox and the RPC boundary

`code_exec` runs each cell inside the [bubblewrap sandbox](sandbox.md): a
read-only view of the filesystem, writes confined to the workspace, the
self-modification boundary read-only, and **network off** (unlike admin `bash`,
which keeps network). The sandboxed **worker** process holds only the untrusted
cell code; every `toolkit()` call is a small RPC back to the kernel, which is
where anything privileged actually happens.

Because it runs model-written code, `code_exec` **requires** the sandbox. Where
unprivileged user namespaces are unavailable (see
[Enabling it in the deployment](sandbox.md#enabling-it-in-the-deployment)),
`code_exec` **refuses** rather than run cells unconfined — unlike `bash`, which
falls back to an unsandboxed run under the command policy.

### Session lifetime

A `code_exec` session is **per turn**: opened lazily on the first cell, reused
across cells so the namespace persists, and closed when the turn ends. A cell
that outruns `MARCEL_CODE_EXEC_TIMEOUT_SECONDS` is terminated and its session
closed (the agent gets a clean "timed out"). The worker also dies with the
kernel, so a missed close cannot orphan a sandbox process.

### The command policy and `code_exec`

`code_exec` is on the command-execution surface, but it is **not** shell-token
classified — its payload is Python, and `shlex`-tokenising a cell is meaningless
(ordinary Python quoting would false-flag as "malformed"). The **sandbox is its
containment**, so it rides the policy default (*allow*); an operator tightens it
with an explicit [`Rule`](extensions.md#the-lifecycle-event-bus) on its `code`
arg (deny it, or require approval). `allow_always` and approval previews target
`code` for `code_exec` (and `command` for `bash`).

## `promote_extension` — script → durable tool

Once a script is proven, `promote_extension(tool_id, code, description="")`
turns it into a permanent capability:

```python
promote_extension(
    tool_id="rates.usd_to_eur",              # "family.action", lowercase
    code='return toolkit("http.get", url=...)',   # the async handler body
    description="Convert USD to EUR via the live rate",
)
```

`code` becomes the body of an async toolkit handler `(params, user_slug) -> str`.
Promotion:

1. **validates** the ids and **compile-checks** the rendered module;
2. writes `<zoo>/extensions/<family>/__init__.py` — a real
   [`register(marcel)`](extensions.md) module exposing `tool_id`;
3. **commits it to the zoo** — *before* any restart, so `git revert` cleanly
   undoes it (**Core principle: Recoverable**);
4. requests a redeploy via the [restart flag](self-modification.md) — never a
   direct restart.

The new tool loads on the next redeploy. A promotion that fails to import is
isolated by the loader, so a bad promotion degrades to an inert file, never a
restart loop.

## Configuration

| Setting | Default | Meaning |
|---|---|---|
| `MARCEL_CODE_EXEC_ENABLED` | `true` | Offer `code_exec` to admins. Requires the sandbox regardless — with no sandbox it refuses. |
| `MARCEL_CODE_EXEC_TIMEOUT_SECONDS` | `30` | Per-cell wall-clock limit; a cell that exceeds it is terminated and its session closed. |

See also: the [sandbox](sandbox.md), [extensions](extensions.md), and the
[self-modification](self-modification.md) restart path.
