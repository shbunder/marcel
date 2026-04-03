# ISSUE-026: AG-UI Protocol + Rich Content Rendering

**Status:** WIP
**Created:** 2026-04-02
**Assignee:** Unassigned
**Priority:** High
**Labels:** feature, architecture

## Capture

**Original request:** "I want Marcel to be able to display rich content in the chat UI. Investigate: 1) does it make sense to integrate copilotkit / AGUI / A2UI in Marcel, if so how would that work? 2) for telegram, can we make use of widgets in sync with AGUI / A2UI?"

**Follow-up Q&A:**
- *Who is the primary audience?* — Building an iPhone app + web app (or macOS app). All clients.
- *What content types?* — All of it: structured data, interactive forms, real-time agent activity, media.
- *Telegram Mini App: always-on or on-demand?* — On-demand. Normal chat for simple responses, Mini App opens for rich content.
- *Generative UI (A2UI)?* — Start simpler with hardcoded widget renderers, but keep a path open to full generative UI later.
- *Web frontend on the roadmap?* — Yes.

**Resolved intent:** Adopt the AG-UI protocol as Marcel's wire format for agent-to-UI communication, replacing the current ad-hoc WebSocket messages. This enables rich, interactive content across all frontends: a new web app, a Telegram Mini App (on-demand, sharing the same web codebase), and eventually an iOS/macOS app. CopilotKit (the React framework) is explicitly excluded — Marcel will implement AG-UI directly. Phase 1 uses hardcoded widget renderers; generative UI via A2UI is a future phase.

## Description

Marcel currently streams plain text tokens over a minimal WebSocket protocol (`token`, `started`, `done`, `error`). This is sufficient for the Rust TUI and Telegram's MarkdownV2 output, but cannot represent structured data, interactive forms, tool execution progress, or any content richer than text.

The AG-UI (Agent-User Interaction Protocol) is a transport-agnostic, event-based protocol that standardizes agent-to-frontend communication. It defines typed events for text streaming, tool calls, state synchronization, lifecycle management, and extensible custom events — exactly the vocabulary Marcel needs.

Key architectural decisions from the investigation:
- **AG-UI yes, CopilotKit no.** CopilotKit is a React framework with a Node.js runtime — redundant with Marcel's Python backend and adds unnecessary dependency weight. AG-UI is just a protocol/event schema that Marcel can implement natively in Python (emitter) and each client (consumer).
- **Telegram Mini Apps are web apps.** They run in Telegram's WebView with a thin JS bridge. The web app and Telegram Mini App share a codebase — the Mini App just adds theme integration and Telegram API bindings.
- **Single web frontend, multiple shells.** The web app, Telegram Mini App, and iOS app (via WKWebView or native) all consume the same AG-UI event stream from Marcel's backend.

### Target Architecture

```
Marcel Python Backend (AG-UI event emitter)
        |
        +-- SSE / WebSocket
        |
        +--> Web App (standalone browser)
        +--> iOS App (WKWebView or SwiftUI + AG-UI client)
        +--> Telegram Mini App (same web app + Telegram JS bridge)
        +--> Rust TUI (interprets AG-UI events into ratatui widgets)
```

## Tasks

### Phase 1 — AG-UI Protocol Adoption
- [✓] ISSUE-026-a: Define Marcel's AG-UI event schema (Python dataclasses/TypedDicts mapping to AG-UI event types)
- [✓] ISSUE-026-b: Refactor `runner.py` to emit AG-UI events instead of raw token strings
- [✓] ISSUE-026-c: Update WebSocket endpoint to stream AG-UI events
- [✓] ISSUE-026-d: Update Rust TUI client to consume AG-UI events (backward-compatible: text events render as before, tool call events show activity indicators)
- [✓] ISSUE-026-e: Update Telegram webhook handler to consume AG-UI events (text events → MarkdownV2 as before)
- [✓] ISSUE-026-f: Emit `ToolCallStart`/`ToolCallArgs`/`ToolCallEnd` events when the agent invokes tools

### Phase 2 — Web App + Telegram Mini App
- [✓] ISSUE-026-g: Build minimal web app (React + Vite) consuming AG-UI event stream via WebSocket
- [✓] ISSUE-026-h: Implement hardcoded widget renderers for initial content types: calendar view, checklist, streaming text with markdown
- [✓] ISSUE-026-i: Wrap web app as Telegram Mini App (Telegram JS bridge adapter, theme integration, back button wiring)
- [✓] ISSUE-026-j: Wire bot to send inline keyboard buttons that open the Mini App on-demand for rich content
- [✓] ISSUE-026-k: Authentication flow for Mini App (validate Telegram initData via HMAC-SHA256, map to Marcel user)

### Phase 3 — iOS / macOS App
- [ ] ISSUE-026-l: iOS app — either native SwiftUI consuming AG-UI directly, or WKWebView wrapper around the web app
- [ ] ISSUE-026-m: macOS app (if separate from iOS — may be a universal app)

### Phase 4 — Generative UI
- [ ] ISSUE-026-n: Define a `Custom` event schema for typed widget specs (agent emits `{"type": "calendar_view", "events": [...]}` etc.)
- [ ] ISSUE-026-o: Evaluate A2UI (Google's declarative UI protocol) for full generative UI — agent describes needed UI as structured JSON, frontend renders dynamically
- [ ] ISSUE-026-p: Implement generative UI rendering pipeline if A2UI evaluation is positive

## Relationships
- Related to: [[ISSUE-025-slash-command-suggestions]] (TUI interaction improvements)

## Comments
### 2026-04-02 - Investigation Summary
Conducted a thorough investigation of CopilotKit, AG-UI protocol, A2UI, and Telegram Mini Apps. Key findings:

**AG-UI** is a lightweight, transport-agnostic event protocol (MIT licensed) with SDKs in Python, Rust, TypeScript, and more. It defines ~25 event types across 7 categories: lifecycle, text messages, tool calls, state management, activity, reasoning, and custom/raw events. State sync uses JSON Patch (RFC 6902). Backed by CopilotKit, adopted by Google, AWS, Microsoft, LangChain, Oracle, and others.

**CopilotKit** is the React framework built on AG-UI. Excluded from this design because: (1) Marcel has no React frontend and its value is in React hooks, (2) its Node.js runtime would be redundant with Marcel's Python backend, (3) violates Marcel's "lightweight over bloated" principle.

**Telegram Mini Apps** are standard web apps loaded in Telegram's WebView. You host and build them yourself (HTML/CSS/JS). Telegram provides a JS bridge (`window.Telegram.WebApp`) for theme colors, user identity, haptic feedback, popups, storage, and device sensors. No widget builder or declarative config — it's just your web page in a WebView.

**A2UI** (Google) is a companion protocol to AG-UI where agents emit structured JSON describing what UI they need. Deferred to Phase 4.

## Implementation Log

### 2026-04-02 14:00 - LLM Implementation
**Action**: Implemented Phase 1 subtasks a, b, c, e, f — AG-UI event protocol adoption
**Files Modified**:
- `src/marcel_core/agent/events.py` — Created: 9 AG-UI event types as frozen dataclasses (RunStarted, RunFinished, RunError, TextMessageStart, TextMessageContent, TextMessageEnd, ToolCallStart, ToolCallEnd, ToolCallResult) + AgentEvent union type + _truncate helper
- `src/marcel_core/agent/runner.py` — Refactored: stream_response() now yields AgentEvent instead of str | TurnResult. Added ToolUseBlock/ToolResultBlock processing from SDK stream events. Removed TurnResult (replaced by RunFinished).
- `src/marcel_core/agent/__init__.py` — Updated exports: removed TurnResult, added all event types
- `src/marcel_core/api/chat.py` — Updated: consumes AgentEvent types. TextMessageContent → backward-compatible `{"type": "token"}`. New AG-UI events (text boundaries, tool calls) sent via to_dict(). RunFinished → backward-compatible `{"type": "done"}`.
- `src/marcel_core/telegram/webhook.py` — Updated: collects TextMessageContent events, ignores all others
- `tests/core/test_agent.py` — Updated helpers (_collect_stream returns events list), adapted existing tests, added 7 new tests: text_message_boundaries, emits_tool_call_events, tool_call_splits_text_messages, tool_result_from_assistant_message, fallback_path_has_text_boundaries, run_started_includes_thread_id, tool_call_events_sent_over_websocket
**Commands Run**: `make check` (format + lint pass, pyright has 5 pre-existing errors unrelated to this change), `uv run pytest tests/ -v` (185/185 pass)
**Result**: Success — all tests passing, wire protocol backward-compatible
**Next**: ISSUE-026-d (Rust TUI update to display tool call activity)

### 2026-04-02 15:00 - LLM Implementation
**Action**: Implemented ISSUE-026-d — Rust TUI tool call activity indicators
**Files Modified**:
- `src/marcel_cli/src/chat.rs` — Extended `ChatResponse` with `tool_call_id`, `tool_name`, `is_error`, `summary` fields. Added `ToolCallStart` and `ToolCallEnd` variants to `ChatEvent`. Parse `tool_call_start`/`tool_call_end` message types from WebSocket.
- `src/marcel_cli/src/ui.rs` — Added `ToolActivity` struct and `active_tools: Vec<ToolActivity>` to `ChatView`. Added `start_tool()`/`end_tool()` methods. Renders yellow `⚙ ToolName …` indicator lines below streaming text. Clears active tools on `finish_stream()` and `clear()`. Added `YELLOW` color constant.
- `src/marcel_cli/src/app.rs` — Handle `ToolCallStart`/`ToolCallEnd` events in the main event loop, updating `ChatView` state and scrolling to bottom.
- `src/marcel_cli/src/print.rs` — Handle new `ChatEvent` variants: ignored in text/json modes, emitted as JSON in stream-json mode.
**Commands Run**: `cargo build`, `cargo clippy -- -D warnings`, `cargo fmt`
**Result**: Success — builds clean, no warnings, no clippy issues
**Next**: Phase 1 complete. Phase 2 (web app + Telegram Mini App) is next.

### 2026-04-02 16:30 - LLM Implementation
**Action**: Implemented Phase 2 subtasks g + h — React web frontend with streaming chat, tool indicators, and widget renderers
**Files Modified**:
- `src/marcel_core/main.py` — Added CORSMiddleware (origins from MARCEL_CORS_ORIGINS env var), SPA fallback serving index.html from src/web/dist/ for non-API routes, /assets StaticFiles mount
- `src/web/package.json` — Created: React 19, react-markdown 9, remark-gfm 4, Vite 6, TypeScript 5
- `src/web/vite.config.ts` — Created: React plugin, outDir dist, dev proxy /ws → ws://localhost:7421, /health + /conversations → http://localhost:7421
- `src/web/tsconfig.json` — Created: strict mode, JSX react-jsx, ES2020, bundler moduleResolution
- `src/web/index.html` — Created: minimal HTML shell with #root div
- `src/web/src/vite-env.d.ts` — Created: Vite client type reference
- `src/web/src/main.tsx` — Created: React root entry point
- `src/web/src/App.tsx` — Created: main layout with header (title, status dot, new chat button), useChat hook integration
- `src/web/src/types.ts` — Created: Message, ActiveTool, ChatConfig interfaces + ServerEvent discriminated union
- `src/web/src/hooks/useChat.ts` — Created: WebSocket state machine managing messages, streamingText, activeTools, conversationId. Auto-reconnect with exponential backoff (1s→30s). Processes all AG-UI event types.
- `src/web/src/components/Chat.tsx` — Created: scrollable message list with auto-scroll, empty state, InputBar
- `src/web/src/components/MessageBubble.tsx` — Created: user/assistant/error/system messages with react-markdown + remark-gfm, calendar + checklist widget detection
- `src/web/src/components/StreamingMessage.tsx` — Created: memoized in-flight markdown rendering with blinking cursor
- `src/web/src/components/ToolIndicator.tsx` — Created: yellow gear icon + tool name with pulse animation
- `src/web/src/components/InputBar.tsx` — Created: textarea with Enter to send, Shift+Enter for newline
- `src/web/src/widgets/CalendarWidget.tsx` — Created: detects markdown tables with date columns → styled event cards
- `src/web/src/widgets/ChecklistWidget.tsx` — Created: detects GFM task lists → interactive checkboxes (client-side state)
- `src/web/src/styles/global.css` — Created: full dark theme with custom properties, all component styles, animations
- `Makefile` — Added web-install, web-build, web-dev targets
- `.gitignore` — Added src/web/node_modules/ and src/web/dist/
**Commands Run**: `npm install` (166 packages, 0 vulnerabilities), `npm run build` (tsc + vite build, success)
**Result**: Success — production build generates dist/index.html + dist/assets/
**Next**: Phase 2 subtasks i-k (Telegram Mini App) are future work

### 2026-04-03 10:00 - LLM Implementation
**Action**: Implemented Phase 2 subtasks i+j+k — Telegram Mini App integration
**Files Modified**:
- `src/marcel_core/auth/__init__.py` — Added `verify_telegram_init_data()`: HMAC-SHA256 validation of Telegram initData per official spec, returns parsed user dict or None. Uses only stdlib (hmac, hashlib, urllib.parse, json, time).
- `src/marcel_core/api/chat.py` — Added dual auth path: WebSocket handler now accepts either `token` (existing) or `initData` (Telegram Mini App). When authenticated via initData, user slug is resolved from Telegram user ID via `get_user_slug()` and locked for the connection (prevents impersonation).
- `src/marcel_core/telegram/bot.py` — Added `reply_markup` kwarg to `send_message()` (passed to both MarkdownV2 and plain text attempts). Added `has_rich_content()` (detects markdown tables and GFM task lists), `rich_content_markup()` (returns InlineKeyboardMarkup with web_app URL), `set_menu_button()` (one-time setup for bot menu button). Added `MARCEL_PUBLIC_URL` env var support.
- `src/marcel_core/telegram/webhook.py` — Rich content detection: when response contains tables or task lists, attaches "View in app" inline keyboard button via `rich_content_markup()`.
- `src/web/index.html` — Added Telegram Web App JS SDK script tag
- `src/web/src/telegram.ts` — Created: Mini App detection adapter with `getTelegramWebApp()`, TypeScript interface for `window.Telegram.WebApp`
- `src/web/src/App.tsx` — Mini App mode: detects Telegram WebView, sets initData auth, calls tg.ready()/expand(), hides header, wires BackButton to new chat / close
- `src/web/src/types.ts` — Added optional `initData` field to ChatConfig
- `src/web/src/hooks/useChat.ts` — Sends `initData` instead of `token` when in Mini App mode, uses `telegram-app` channel
- `src/web/src/styles/global.css` — Added Telegram theme CSS overrides (maps --tg-theme-* vars to app custom properties), hides header in Mini App mode
- `.env` — Added `MARCEL_PUBLIC_URL` env var with documentation
**Commands Run**: `npm run build` (success), `uv run ruff format` + `ruff check` (clean), `uv run pytest tests/ -x -v` (185/185 pass)
**Result**: Success — all tests passing, frontend builds clean
**Next**: Phase 3 (iOS/macOS app) and Phase 4 (generative UI) are future work
