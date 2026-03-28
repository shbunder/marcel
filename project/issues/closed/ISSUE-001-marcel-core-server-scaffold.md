# ISSUE-001: marcel-core server scaffold

**Status:** Closed
**Created:** 2026-03-26
**Assignee:** Unassigned
**Priority:** High
**Labels:** feature, phase-1

## Capture
**Original request:** Build marcel-core as a Python AI agent using claude_agent_sdk, exposing a FastAPI server that all clients connect to.

**Resolved intent:** Create the skeleton of the `marcel-core` FastAPI application: project layout, server entrypoint, health endpoint, and `make serve` target that starts the watchdog which in turn starts uvicorn. No agent logic yet — just a running server with the right shape.

## Description

The first thing that needs to exist is a runnable server. All other Phase 1 work depends on having this scaffold in place. The scaffold should define the module structure that subsequent issues will fill in, so the shape of the code matters here even though most modules start empty.

Target layout:

```
src/marcel_core/
  __init__.py
  main.py          # FastAPI app, lifespan, router registration
  api/
    __init__.py
    chat.py        # WebSocket /ws/chat endpoint (stub returning echo for now)
    health.py      # GET /health → {"status": "ok", "version": "..."}
  agent/           # empty __init__.py — filled by ISSUE-003
  storage/         # empty __init__.py — filled by ISSUE-002
  skills/          # empty __init__.py — filled by ISSUE-004
  auth/            # empty __init__.py — filled by Phase 2
  watchdog/        # empty __init__.py — filled by ISSUE-005
  telegram/        # empty __init__.py — filled by Phase 2
```

The `/ws/chat` endpoint should accept a WebSocket connection, receive a JSON message `{"text": "..."}`, and echo `{"text": "echo: ..."}` back. Real agent wiring comes in ISSUE-003.

`make serve` should start the server via uvicorn directly (watchdog comes in ISSUE-005):
```
uvicorn marcel_core.main:app --host 0.0.0.0 --port 8000 --reload
```

## Tasks
- [✓] Create `src/marcel_core/` module layout with all stub `__init__.py` files
- [✓] `main.py`: FastAPI app with lifespan context manager, register routers
- [✓] `api/health.py`: GET `/health` returning `{"status": "ok", "version": "0.1.0"}`
- [✓] `api/chat.py`: WebSocket `/ws/chat` stub (echo for now)
- [✓] Add `uvicorn` and `fastapi` to `pyproject.toml` dependencies
- [✓] Add `make serve` target to `Makefile`
- [✓] Tests: health endpoint returns 200 + correct body; WebSocket echo works
- [✓] Docs: `docs/architecture.md` stub with module layout description

## Relationships
- Blocks: [[ISSUE-002-flat-file-storage]], [[ISSUE-003-agent-loop]], [[ISSUE-004-cmd-tool-skills-registry]], [[ISSUE-005-watchdog-git-rollback]], [[ISSUE-006-marcel-cli-tui]]

## Implementation Log

### 2026-03-26 - LLM Implementation
**Action**: Built full server scaffold
**Files Modified**:
- `src/marcel_core/__init__.py` — package init with `__version__`
- `src/marcel_core/main.py` — FastAPI app, lifespan, router registration
- `src/marcel_core/api/health.py` — GET `/health`
- `src/marcel_core/api/chat.py` — WebSocket `/ws/chat` echo stub
- `src/marcel_core/agent/__init__.py` — stub
- `src/marcel_core/storage/__init__.py` — stub
- `src/marcel_core/skills/__init__.py` — stub
- `src/marcel_core/auth/__init__.py` — stub
- `src/marcel_core/watchdog/__init__.py` — stub
- `src/marcel_core/telegram/__init__.py` — stub
- `pyproject.toml` — added `fastapi>=0.115.0`, `uvicorn[standard]>=0.34.0`
- `Makefile` — added `make serve` target
- `tests/core/test_scaffold.py` — 4 tests (health 200, health body, WS echo, WS empty)
- `docs/architecture.md` — module layout and WebSocket protocol docs
- `mkdocs.yml` — cleaned up from previous project, registered `architecture.md`
**Commands Run**: `uv sync`, `uv run pytest tests/core/test_scaffold.py -v`
**Result**: 4/4 tests passing
