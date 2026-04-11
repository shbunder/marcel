# ISSUE-063: A2UI Component Catalog for Multi-Platform Skill UI

**Status:** Open
**Created:** 2026-04-11
**Assignee:** Unassigned
**Priority:** Medium
**Labels:** feature, architecture

## Capture

**Original request:** "Can you explain me how A2UI relates to AGUI? I'm wondering if there are standards to have agents generate dynamic interactive UI content?" followed by "Does it make sense to also use A2UI for the Telegram mini-app to show more components related to different skills?" and "I would like to have other places to contact Marcel, like an iOS app and macOS app."

**Follow-up Q&A:**
- *Does A2UI make sense for Marcel?* — Initially leaned no (Marcel controls both sides, single frontend), but the plan for iOS and macOS native apps changes the calculus — 3+ platforms consuming the same agent output makes a declarative UI protocol valuable.
- *How to make the component catalog easy to extend?* — Co-locate component schemas with skills (`components.yaml`), implement a generic fallback renderer per platform so new components work everywhere immediately, and progressively add native implementations for polished UX.

**Resolved intent:** Adopt A2UI as Marcel's declarative UI protocol for agent-generated rich content, layered on top of the existing AG-UI event transport. Skills declare their UI components via co-located schema files. Each platform (Telegram Mini App, iOS, macOS) maintains a component catalog with a generic fallback renderer — new skills produce UI that works everywhere out of the box, with optional native implementations for polish. This continues the work outlined in ISSUE-026 Phase 4 (subtasks p/q/r).

## Description

Marcel currently renders rich content via a hardcoded `ContentType` literal (`markdown | image | chart_data | html | checklist | calendar`) with per-platform rendering logic — React widgets in the Telegram Mini App, HTML formatting for Telegram chat, ratatui in the Rust CLI. Adding a new content type requires coordinated changes across the Python backend, TypeScript types, and every frontend renderer.

With iOS and macOS apps on the roadmap, this scales poorly. A2UI (Google's agent-to-UI protocol) solves this by letting the agent emit structured, declarative component descriptions as JSON. Each platform renders them with native widgets from a pre-approved catalog, with automatic fallback for unimplemented components.

### How A2UI and AG-UI work together

- **AG-UI** is the transport layer — how events flow between the agent backend and frontends (streaming, bi-directional, event-based)
- **A2UI** is the content layer — what UI the agent wants to display (declarative component descriptions as JSON)

AG-UI natively supports A2UI payloads as custom events. Marcel already implements AG-UI; this issue adds A2UI on top.

### Component catalog design

Skills declare their UI components via `components.yaml` in the skill directory:

```
skills/
  banking/
    SKILL.md
    components.yaml     # declares: transaction_list, balance_card, ...
  calendar/
    SKILL.md
    components.yaml     # declares: event_list, day_view, ...
```

Each component is defined by a JSON Schema describing its props:

```yaml
components:
  - name: transaction_list
    description: List of bank transactions with running balance
    props:
      transactions:
        type: array
        items:
          properties:
            date: { type: string, format: date }
            description: { type: string }
            amount: { type: number }
            balance: { type: number }
      currency: { type: string, default: "EUR" }
```

The agent emits: `{ "component": "transaction_list", "props": { ... } }` — structured data, not layout.

### Fallback chain per platform

```
Native implementation (SwiftUI TransactionList / React TransactionList)
  -> Generic A2UI renderer (auto-generates from schema: labels, lists, tables)
    -> Raw markdown (last resort)
```

Adding a new skill with custom UI requires only steps 1-2:
1. Create `skills/newskill/SKILL.md`
2. Create `skills/newskill/components.yaml`

It renders everywhere immediately via the generic fallback. Native implementations (step 3) are optional and per-platform.

## Tasks

### Phase 1 — Component Schema & Registry
- [ ] ISSUE-063-a: Define the A2UI component schema format (YAML with JSON Schema props, aligned with A2UI spec v0.9)
- [ ] ISSUE-063-b: Extend skill loader (`skills/loader.py`) to discover and parse `components.yaml` files alongside `SKILL.md`
- [ ] ISSUE-063-c: Build component registry that aggregates schemas from all skills at startup
- [ ] ISSUE-063-d: Add `/api/components` endpoint so clients can fetch the full catalog
- [ ] ISSUE-063-e: Extend the `Artifact` model to support A2UI component payloads (new `content_type: "a2ui"` with structured JSON content)

### Phase 2 — Generic Renderer (React / Telegram Mini App)
- [ ] ISSUE-063-f: Build generic A2UI renderer in the web app — auto-generates UI from component schema (labels, lists, tables, basic inputs)
- [ ] ISSUE-063-g: Migrate existing CalendarWidget and ChecklistWidget to A2UI component definitions as proof of concept
- [ ] ISSUE-063-h: Update Telegram Mini App Viewer to use generic renderer with fallback to current hardcoded widgets

### Phase 3 — AG-UI Transport Integration
- [ ] ISSUE-063-i: Define A2UI custom event type in AG-UI event schema (extends existing custom event support)
- [ ] ISSUE-063-j: Enable real-time streaming of A2UI components (incremental updates via AG-UI events, not just static artifacts)

### Phase 4 — Native Platform Catalogs (iOS / macOS)
- [ ] ISSUE-063-k: Define Swift `ComponentCatalog` protocol for iOS/macOS (maps component names to SwiftUI views)
- [ ] ISSUE-063-l: Implement generic SwiftUI renderer (same fallback pattern: schema -> auto-generated Form/List/VStack)
- [ ] ISSUE-063-m: Native SwiftUI implementations for high-value components (calendar, checklist, transaction list)

## Relationships
- Continues: [[ISSUE-026-agui-rich-content]] (Phase 4 subtasks p/q/r)
- Related to: [[ISSUE-050-artifact-mini-app]] (current artifact rendering system)

## Comments
### 2026-04-11 - Research & Design Discussion
Conducted web research on A2UI and AG-UI relationship. Key findings:

- **A2UI** (Google, Apache 2.0) is a declarative, streaming UI protocol where agents emit JSON component descriptions. Clients maintain a catalog of trusted, pre-approved components. Security model: declarative data, not executable code.
- **AG-UI** (CopilotKit) is the event-based transport protocol Marcel already uses. AG-UI natively supports A2UI payloads — they're complementary layers, not competitors.
- CopilotKit is a launch partner for both protocols. AWS Bedrock added AG-UI support in March 2026.
- The emerging agentic stack: MCP (tools/context) + A2A (agent coordination) + AG-UI (agent-frontend transport) + A2UI (agent-generated UI).

Design decision: co-locate component schemas with skills rather than centralizing them. This keeps skills self-contained (aligning with Marcel's "lightweight, self-contained, removable" principle) and makes the catalog automatically grow as skills are added.

## Implementation Log
