# A2UI Component Catalog

Marcel uses the [A2UI protocol](https://github.com/anthropics/a2ui) (Agent-to-UI) to let skills declare structured UI components that render natively across all platforms — Telegram Mini App, iOS, and macOS.

## How it works

Skills co-locate a `components.yaml` file alongside their `SKILL.md`. Each component defines its props using JSON Schema. The agent emits structured data (`{"component": "name", "props": {...}}`), and each platform renders it using the best available method.

### Fallback chain

Every component renders everywhere, even without platform-specific code:

1. **Native widget** — hand-crafted platform widget (e.g. `CalendarWidget` in React)
2. **Generic A2UI renderer** — auto-generates UI from the JSON Schema (tables, labeled rows, lists)
3. **Raw JSON** — last resort, formatted props display

## Defining components

Create a `components.yaml` in your skill directory:

```
skills/
  banking/
    SKILL.md
    components.yaml     # declares: transaction_list, balance_card
  calendar/
    SKILL.md
    components.yaml     # declares: event_list, day_view
```

### Schema format

```yaml
components:
  - name: transaction_list
    description: List of bank transactions with running balance
    props:
      type: object
      properties:
        transactions:
          type: array
          items:
            type: object
            properties:
              date: { type: string, format: date }
              description: { type: string }
              amount: { type: number }
              balance: { type: number }
            required: [date, description, amount]
        currency:
          type: string
          default: EUR
      required: [transactions]
```

Each component has:

- **`name`** — unique identifier used in A2UI payloads
- **`description`** — human-readable description for the catalog
- **`props`** — JSON Schema defining the component's data contract

## Component registry

At startup, the skill loader discovers all `components.yaml` files and builds a flat registry. Component names must be globally unique — if two skills declare the same name, the last one loaded wins (with a warning).

## API endpoint

Clients fetch the catalog to know what components are available:

- `GET /api/components` — returns the full catalog with all component schemas
- `GET /api/components/{name}` — returns a single component schema

Both endpoints require authentication (Telegram initData or Bearer token).

## Emitting components from the agent

The primary way to render an A2UI component is the `render` action on the `marcel` tool. It validates the component against the registry, stores the payload as an artifact, and — on channels with a rich-UI frontend — delivers the component immediately (on Telegram, as a Mini App "View in app" button).

```
marcel(action="render", component="transaction_list", props={
  "transactions": [
    {"date": "2026-04-11", "description": "Colruyt", "amount": -42.18, "balance": 1854.22},
    {"date": "2026-04-10", "description": "Salary", "amount": 2500.00, "balance": 1896.40}
  ],
  "currency": "EUR"
})
```

The action takes:

| Param | Required | Description |
|-------|----------|-------------|
| `component` | yes | Component name as declared in a skill's `components.yaml` |
| `props` | yes | A dict matching the component's JSON Schema |
| `name` | no | Optional title override (defaults to a humanized form of the component name) |

It returns a short confirmation string with the artifact id, or an error message the agent can relay to the user if validation failed (e.g. unknown component name, unserializable props).

### Channel gating

The system prompt builder (`harness/context.py`) only advertises the A2UI component catalog to channels that can actually render it. The `channel_supports_rich_ui(channel)` helper in `channels/adapter.py` is the single source of truth — it currently returns `True` for `telegram`, `websocket`, `app`, `ios`, and `macos`, and `False` for `cli` and `job`. Text-only channels never see the `## A2UI Components` section in their prompt and therefore never call `marcel(action="render")`.

When adding a new rich-UI channel, update `_RICH_UI_CHANNELS` in `channels/adapter.py` to pick up the gating automatically.

### Lower-level API

For in-process callers (e.g. other tools that need to produce an A2UI artifact without going through the agent), use `create_artifact` directly:

```python
from marcel_core.storage.artifacts import create_artifact

artifact_id = create_artifact(
    user_slug="shaun",
    conversation_id="conv-123",
    content_type="a2ui",
    content='{"events": [{"date": "Today", "title": "Dentist", "time": "10:00"}]}',
    title="Today's Calendar",
    component_name="calendar",
)
```

The `content` field contains JSON-serialized props matching the component's schema. The `component_name` tells the frontend which component to render.

## AG-UI streaming

A2UI components can be streamed in real-time via AG-UI events using the `A2UIComponent` event type defined in `harness/runner.py`. This event type is currently reserved for a future streaming-render path and is not yielded by any existing tool — the `marcel(action="render")` action uses the side-effect delivery pattern instead (same as `generate_chart`).

```python
from marcel_core.harness.runner import A2UIComponent

yield A2UIComponent(
    component="calendar",
    props={"events": [...]},
    artifact_id="optional-artifact-id",
)
```

## Built-in components

Marcel ships with built-in components in the `ui` default skill:

| Component | Description |
|-----------|-------------|
| `calendar` | Event list grouped by date with time and location |
| `checklist` | Interactive checklist with toggleable items |

The `banking` skill adds:

| Component | Description |
|-----------|-------------|
| `transaction_list` | Bank transactions with running balance |
| `balance_card` | Account balance summary card |

## Adding a new component

1. Add a `components.yaml` to your skill directory (or extend an existing one)
2. Define the component name, description, and props schema
3. The component renders everywhere immediately via the generic fallback
4. Optionally, add a native widget implementation per platform for polish
