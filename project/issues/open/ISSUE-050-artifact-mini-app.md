# ISSUE-050: Artifact-Based Telegram Mini App

**Status:** Open
**Created:** 2026-04-10
**Assignee:** Claude
**Priority:** High
**Labels:** feature, telegram, frontend

## Capture
**Original request:** "I want to redo the mini-apps 'View in app' logic in Telegram. Marcel should be able to send rich content to Telegram (like graphs, or pictures). When Marcel sends this but cannot show it in a bubble it gives button to view it in the mini-app. The button should preferably stay linked to that specific content (also when the conversation progresses and new view-buttons are shown, old buttons should still show that specific rich content). Completely get rid of the conversation like mini-app that I can also open under show next to the input screen, it doesn't make sense. Basically the mini-app should just contain 'containers' of rich content linked to earlier responses."

**Follow-up Q&A:**
- Storage: User chose separate artifact store (flat JSON files under data/artifacts/)
- Rendering: Both server-side (images) and client-side (charts/interactive)
- Menu button: Repurpose as artifact gallery (list of all past artifacts)

**Resolved intent:** Replace the current turn-index-based "View in app" system and chat-mode Mini App with a stable artifact-based architecture. Each rich content response creates a persistent artifact with a UUID. The Mini App becomes a pure viewer/gallery — no chat interface, no WebSocket. Old buttons keep working via legacy fallback.

## Description

The current Telegram Mini App has two problems: (1) it includes a full chat interface that duplicates Telegram itself, and (2) "View in app" buttons are addressed by conversation turn index, which is fragile and tightly coupled to conversation structure.

This issue introduces an **artifact** concept — a stored piece of rich content with a stable UUID. When Marcel's response contains rich content (calendars, checklists, tables, images), the backend creates an artifact and the "View in app" button links to it by ID. The Mini App is redesigned from a chat+viewer dual-mode app to a pure artifact viewer with a gallery.

## Tasks
- [ ] Create artifact storage module (`src/marcel_core/storage/artifacts.py`)
- [ ] Create artifact API endpoints (`src/marcel_core/api/artifacts.py`)
- [ ] Register artifacts router in `main.py`
- [ ] Integrate artifact creation into Telegram webhook pipeline
- [ ] Add `detect_content_type()`, `extract_title()`, `artifact_markup()` to bot.py
- [ ] Update `_format_response()` to use artifact IDs instead of turn indices
- [ ] Update `web_app_url_for()` to support artifact IDs
- [ ] Rewrite frontend types (remove chat types, add artifact types)
- [ ] Refactor Viewer component to fetch artifacts by ID (with legacy fallback)
- [ ] Create Gallery component for artifact listing
- [ ] Rewrite App.tsx (remove chat mode, add gallery/viewer routing)
- [ ] Delete chat-mode frontend files (Chat, InputBar, StreamingMessage, MessageBubble, ToolIndicator, useChat)
- [ ] Update CSS (remove chat styles, add gallery styles)
- [ ] Add deprecation warning to old `/api/message/` endpoint
- [ ] Verify: tests pass, frontend builds, end-to-end flow works

## Relationships
- Related to: [[ISSUE-026-agui-rich-content]] (builds on the rich content system from Phase 2)
- Related to: [[ISSUE-030-view-in-app-fixes]] (replaces the turn-index fix with artifact IDs)

## Comments

## Implementation Log
