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

## Creating A2UI artifacts

To emit A2UI content, create an artifact with `content_type: "a2ui"`:

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

A2UI components can be streamed in real-time via AG-UI events using the `A2UIComponent` event type:

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
