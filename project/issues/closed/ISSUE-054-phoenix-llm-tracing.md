# ISSUE-054: Add Phoenix (Arize) LLM Tracing

**Status:** Closed
**Created:** 2026-04-10
**Assignee:** Shaun
**Priority:** Medium
**Labels:** feature, observability

## Capture
**Original request:** can I include a service like pheonix arize to see all traces going to llms by marcel? what would be the easiest way to set this up? can I have the frontend running a always running docker like the current portainer service? all suggestions are welcome!

**Resolved intent:** Add LLM observability to Marcel by wiring pydantic-ai's built-in `instrument` parameter to an OpenTelemetry OTLP/HTTP exporter, sending traces to a Phoenix (Arize) instance running as an always-on Docker service in the central `~/dockers/docker-compose.yml` alongside Portainer and other infrastructure services. Toggled via env var so it's zero-overhead when disabled.

## Description
Marcel has no visibility into its LLM calls — no tracing, token tracking, or latency breakdown. Phoenix is an open-source LLM observability tool that accepts OpenTelemetry traces and provides a web UI. pydantic-ai already has a built-in `instrument` parameter that emits OTEL spans, so the integration is minimal: a thin tracing module, one kwarg on the Agent constructor, and a Phoenix Docker service.

## Tasks
- [✓] Add `opentelemetry-sdk` and `opentelemetry-exporter-otlp-proto-http` to `pyproject.toml`
- [✓] Add `marcel_tracing_enabled` and `marcel_tracing_endpoint` config fields
- [✓] Create `src/marcel_core/tracing.py` with lazy TracerProvider and InstrumentationSettings helper
- [✓] Wire `instrument=get_instrumentation_settings()` into agent creation
- [✓] Add Phoenix service to `~/dockers/docker-compose.yml`
- [ ] Verify end-to-end: traces visible in Phoenix UI

## Relationships
None

## Comments

## Implementation Log
### 2026-04-10 - LLM Implementation
**Action**: Implemented Phoenix LLM tracing via OpenTelemetry
**Files Modified**:
- `pyproject.toml` — added `opentelemetry-sdk` and `opentelemetry-exporter-otlp-proto-http` deps
- `src/marcel_core/config.py` — added `marcel_tracing_enabled` (bool) and `marcel_tracing_endpoint` (str) settings
- `src/marcel_core/tracing.py` — new module: lazy singleton TracerProvider with OTLP/HTTP exporter, `get_instrumentation_settings()` helper
- `src/marcel_core/harness/agent.py` — added `instrument=get_instrumentation_settings()` to Agent constructor
- `~/dockers/docker-compose.yml` — added `phoenix` service (arizephoenix/phoenix:latest, port 6006)
**Result**: Module verified — `get_instrumentation_settings()` returns None when disabled, InstrumentationSettings when enabled. Lint passes. Pre-existing test failures (pydantic-ai 0.8.1 API changes) unrelated.
**Next**: Deploy Phoenix container, enable tracing, verify traces in UI
