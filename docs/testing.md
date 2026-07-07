# Testing with the Terrarium

Marcel's interface is text in → text out. Everything between — tier routing,
tool dispatch through the event bus, command policy, approvals, persistence —
is ordinary code, and ordinary code can be scenario-tested to full coverage.
The pieces that make that possible:

- **[odile](https://github.com/shbunder/odile)** (*Orchestrated Doubles for
  Isolated LLM Exercises*, a sibling repo at `~/projects/odile`) provides the
  generic parts: a **scripted model double**, in-process **fake APIs**, a
  suite-wide guard that makes real LLM calls impossible, and the abstract
  **sealed-world constructs** (`Terrarium` / `Scenario` / `TurnResult`).
- **`marcel_testing`** (in this repo, shipped in the wheel) is Marcel's
  **binding** of those constructs — a sealed Marcel world that runs the
  *real* `stream_turn()` loop around the double. See
  [the hook map](#the-terrarium-is-an-odile-binding) below.

Both are dev-only: the production image never installs odile.

## A scenario in five lines

The `terrarium` fixture is available everywhere (kernel `tests/` and every
zoo park's `tests/`) — the root `conftest.py` loads
`marcel_testing.pytest_plugin`:

```python
from marcel_testing import reply


async def test_greeting(terrarium):
    scenario = terrarium.scenario(reply('Hello!'), user='alice', channel='cli')
    result = await scenario.run('hi marcel')
    assert result.reply == 'Hello!'
```

Nothing here is mocked: the real agent is built, the real event bus runs, the
turn persists to a (temporary) conversation store. Only the LLM is a double —
a script that plays one `reply` step.

## Scripting the model

A script is the sequence of *model turns*: what the "model" does each time
the agent calls it.

```python
from marcel_testing import call_tool, reply

scenario = terrarium.scenario(
    call_tool('toolkit', id='news.sync', params={}),  # 1st model turn: call a tool
    reply('Your news is synced.'),                    # 2nd: final text
)
```

Group calls in a list (`[call_tool(...), call_tool(...)]`) for parallel tool
calls in one turn. If the agent diverges — asks for a turn you didn't script,
or ends with steps unplayed — the scenario fails with a plain-language
`ScriptError` naming the step. Pass `check_script=False` to `run()` to allow
leftover steps.

For "call every tool once" smoke tests, pydantic-ai's `TestModel` drops in:
`terrarium.scenario(model=TestModel(...))`.

## Building the world

```python
terrarium.user('bob', role='admin', profile='Bob prefers short answers.',
               memories={'coffee': '---\nname: coffee\n---\nDouble shot.'})
terrarium.seed_history('bob', 'cli', [('user', 'earlier'), ('assistant', 'context')])
```

State lives in a per-test temp dir (`terrarium.data_root`); assert on files
there after a run. The terrarium also snapshots and restores every process
global a turn touches (channel/extension/toolkit registries, the approval
registry, the command-policy singleton), so two tests never share a world.

## Faking the outside world

```python
terrarium.fake_api('https://api.weather.test').returns('/today', json={'sky': 'sunny'})
```

Any httpx request from a tool handler is answered in-process; anything that
matches no declared fake **raises** — a test cannot pass by accident of
connectivity. For a whole stateful API surface, mount an ASGI app instead of
canned responses (a [fatyma](https://github.com/shbunder/fatyma) cube is
exactly such an app):

```python
terrarium.fake_api('https://bank.example.test').mount(build_cube_app([transfers_spec]))
```

Real model-provider calls are blocked for the whole pytest session
(`pydantic_ai.models.ALLOW_MODEL_REQUESTS = False`) — the same guard
pydantic-ai uses on itself.

## Asserting on what happened

`scenario.run()` returns a `TurnResult`:

| Accessor | What it holds |
|---|---|
| `result.reply` | The assistant text the user would see |
| `result.events` | Harness stream events (`RunStarted` … `RunFinished`) |
| `result.bus_events` / `result.bus('tool_call')` | Lifecycle events recorded from the turn's event bus |
| `result.tool_calls` / `result.tool_results` | The bus's tool events, final args included |
| `result.completions` | Tool completions — a *denied* call appears here with the block reason as its result |
| `result.is_error` | Whether the turn finished in an error state |

One subtlety: a `tool_call` denied by a core handler (policy, role gate,
self-mod guard) short-circuits bus dispatch, so it does not appear in
`result.tool_calls` — assert the denial through `result.completions`.

## Approvals without a human

Ask-tier commands (see [Execution Sandbox](sandbox.md)) pause for approval.
In a terrarium, declare the verdict up front:

```python
requests = terrarium.resolve_approvals('allow_once', channel='cli')  # or allow_always / deny / expire
result = await scenario.run('clean the scratch dir')
assert len(requests) == 1          # the prompt that "reached the user"
```

`'expire'` delivers the prompt and never answers, so the turn takes the
timeout path (shrunk to milliseconds) and the request lands in the on-disk
approval queue.

## Testing zoo parks

Every park ships tests in `<habitat>/<park>/tests/`; `make check` in
marcel-zoo runs them all in the kernel venv. Use unit tests for park
internals and terrarium scenarios for the park as production runs it — load
the habitat through the kernel loader, fake its external APIs, and drive a
full scripted turn. `toolkit/news/tests/test_news_scenarios.py` in marcel-zoo
is the template.

## The Terrarium is an odile binding

Since odile 0.2.0, the sealed-world discipline — the enter/seal → exit/restore
lifecycle, the entered guard, `fake_api()`, `scenario()`'s steps-vs-`model=`
exclusivity, and `run()`'s drive-then-`assert_done()` template — lives in
odile's abstract `Terrarium`/`Scenario`/`TurnResult` base classes, so any
agent project can adopt the same testing system (the odile README documents
the implementer's contract, with a toy binding in its test suite). Marcel's
classes subclass them and implement the four hooks:

| odile hook | What Marcel does in it |
|---|---|
| `Terrarium._seal()` | Redirects the storage data root and `settings.marcel_data_dir`/`marcel_zoo_dir` to the per-test temp dir; snapshots the channel, extension, toolkit, and approval registries plus the command-policy singleton; drops the skills-registry cache; installs the bus-event recorder. |
| `Terrarium._restore()` | Puts every snapshot back exactly (odile has already stopped HTTP interception by this point). |
| `Terrarium._build_scenario(model, **context)` | Reads Marcel's context vocabulary — `user` (default `'alice'`, seeded if missing) and `channel` (default `'cli'`) — and builds Marcel's `Scenario`. |
| `Scenario._drive(text, conversation_id='conv-1')` | Streams the turn through the real `stream_turn()`, assembling `TurnResult` from the text deltas, harness events, and recorded bus events. |

Everything Marcel-specific stays in `marcel_testing`: `user()`,
`seed_history()`, `resolve_approvals()`, and the bus-event recording that
backs `TurnResult.bus_events`.

## Coverage discipline

`make check` enforces the coverage floor across `marcel_core`, `marcel_sdk`,
and `marcel_testing` — and holds `marcel_testing` itself at **100%** (the
harness cannot be the untested part). odile's own `make check` also enforces
100%. Code no scenario can reach is code with no reason to exist; when you
add a path, add the scenario that proves it.
