# ISSUE-010: Rewrite CLI with Textual TUI Framework

**Status:** Open
**Created:** 2026-03-29
**Assignee:** Claude
**Priority:** High
**Labels:** feature, cli

## Capture
**Original request:** "New feature, let's completely redo the Marcel CLI, I've learned that many use react or rust or whatever to build the CLI, we will do the same. To get you inspired look at the codex-cli; you can find the repo here: /home/shbunder/repos/codex/codex-rs. I still want the same header as now, and connection to the Marcel backend, but with the new cli that has similar features to show updates etc from the backend."

**Resolved intent:** Replace the current prompt_toolkit-based REPL with a full Textual TUI application, inspired by codex-cli's architecture. The new CLI should have a proper component-based layout with: the existing header (preserved), a scrollable chat history area with streaming markdown rendering, a fixed input area, and a status bar. The WebSocket connection to the Marcel backend stays the same, but responses should render as styled markdown with smooth streaming — similar to how codex-cli handles it with adaptive chunking and newline-gated rendering.

## Requirements
1. Fixed header panel at top — same 3-column responsive layout with mascot, runtime info, and server info
2. Scrollable chat history area showing user messages and Marcel's responses
3. Markdown rendering for responses (code blocks, bold, italic, lists, links)
4. Streaming token display — tokens appear as they arrive from the WebSocket
5. Fixed input area at bottom with slash command completion
6. Status bar showing connection state, model, conversation info
7. All existing slash commands work (/help, /clear, /config, /model, /status, /cost, /memory, /compact, /reconnect, /exit)
8. Terminal resize handled natively by Textual (no manual polling)
9. Keeps existing config system (config.toml), chat client (WebSocket), and mascot renderer
10. Brand colors preserved (#cc5e76 blush rose, #2ec4b6 deep teal, etc.)

## Design

### Framework: Textual

Textual is the Python equivalent of ratatui (used by codex-cli). Built on Rich, it provides:
- CSS-based styling with hot-reload
- Widget composition and layout
- Built-in Markdown widget
- Async message passing (like codex-cli's AppEvent)
- Native resize handling
- Mouse support and scrolling

### Architecture

```
MarcelApp(App)                    # Textual application
├── HeaderWidget(Static)          # Fixed top — reuses _render_header() from current code
├── ChatView(ScrollableContainer) # Middle — scrollable chat history
│   ├── UserMessage(Static)       # User input bubble
│   ├── AssistantMessage(Static)  # Marcel response with markdown
│   └── StreamingMessage(Static)  # In-flight response being streamed
├── InputWidget(Input)            # Fixed bottom — text input with completion
└── StatusBar(Static)             # Footer — connection, model, cost
```

### Streaming Pipeline (inspired by codex-cli)

```
WebSocket tokens → StreamBuffer (accumulate) → newline gate → Markdown render → update widget
```

- Tokens accumulate in a buffer
- On newline (or finalize), the completed line is rendered as Markdown
- The StreamingMessage widget updates reactively
- Auto-scroll keeps the latest content visible

### Files

| File | Purpose |
|------|---------|
| `app.py` | MarcelApp — main Textual application, replaces current REPL |
| `widgets/header.py` | HeaderWidget — ported from current _render_header() |
| `widgets/chat.py` | ChatView, UserMessage, AssistantMessage, StreamingMessage |
| `widgets/input.py` | InputWidget with slash command completion |
| `widgets/status_bar.py` | StatusBar with connection state |
| `marcel.tcss` | Textual CSS for layout and brand styling |
| `main.py` | Entrypoint (minimal changes) |
| `chat.py` | WebSocket client (unchanged) |
| `config.py` | Config loader (unchanged) |
| `mascot.py` | Mascot renderer (unchanged) |

### Key Patterns from codex-cli

- **Event-driven**: Textual's message system replaces codex-cli's AppEvent enum
- **Streaming**: Newline-gated accumulation like codex-cli's StreamController
- **Component isolation**: Each widget owns its rendering, communicates via messages
- **Responsive layout**: Textual CSS handles resize natively (no polling needed)

## Tasks
- [ ] ISSUE-010-a: Add textual dependency and create file scaffold
- [ ] ISSUE-010-b: Implement HeaderWidget (port existing header)
- [ ] ISSUE-010-c: Implement ChatView with streaming markdown
- [ ] ISSUE-010-d: Implement InputWidget with slash command completion
- [ ] ISSUE-010-e: Implement StatusBar
- [ ] ISSUE-010-f: Wire up MarcelApp — layout, WebSocket integration, command handling
- [ ] ISSUE-010-g: Create Textual CSS stylesheet
- [ ] ISSUE-010-h: Write tests
- [ ] ISSUE-010-i: Update docs and version

## Implementation Log
