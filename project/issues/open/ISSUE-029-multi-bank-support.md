# ISSUE-029: Multi-Bank Support (ING + KBC)

**Status:** Open
**Created:** 2026-04-03
**Assignee:** Marcel
**Priority:** Medium
**Labels:** feature, integration

## Capture
**Original request:** "I've added an ING account as well, can you check?"

**Follow-up Q&A:**
- Q: Extend integration to support ING? A: Yes

**Resolved intent:** Generalize the KBC-only banking integration to support multiple banks via EnableBanking. The user has linked both KBC and ING accounts and expects balances and transactions from both to appear in Marcel's responses. The integration should store multiple session IDs, sync all banks in the background loop, and make all skills work across banks transparently.

## Description

The existing EnableBanking integration (ISSUE-028) was hardcoded for KBC only — single session ID, hardcoded ASPSP name. This issue generalizes it to support multiple banks by storing sessions as a JSON list, parameterizing the bank name in setup/auth flows, and iterating all sessions during sync.

## Tasks
- [✓] ISSUE-029-a: Refactor client.py for multi-session storage with migration
- [✓] ISSUE-029-b: Update sync.py to iterate all bank sessions
- [✓] ISSUE-029-c: Update skill handlers for multi-bank
- [✓] ISSUE-029-d: Update SKILL.md agent documentation
- [✓] ISSUE-029-e: Update tests (34 tests, including multi-bank sync)
- [✓] ISSUE-029-f: Update docs/integration-kbc.md
- [✓] ISSUE-029-g: Verify end-to-end with live KBC + ING data

## Subtasks
- [✓] ISSUE-029-a: Multi-session credential storage with legacy migration
- [✓] ISSUE-029-b: Sync loop iterates all sessions
- [✓] ISSUE-029-c: Skills accept bank parameter, status shows all banks
- [✓] ISSUE-029-d: SKILL.md documents multi-bank usage
- [✓] ISSUE-029-e: Tests cover multi-bank sync, session storage, migration
- [✓] ISSUE-029-f: Onboarding docs updated for multi-bank
- [✓] ISSUE-029-g: Live verification — KBC (3 accounts) + ING (1 account) synced

## Relationships
- Extends: [[ISSUE-028-kbc-banking-integration]]

## Comments

## Implementation Log
### 2026-04-03 — LLM Implementation
**Action**: Multi-bank support for EnableBanking integration
**Files Modified**:
- `src/marcel_core/kbc/client.py` — Multi-session storage (ENABLEBANKING_SESSIONS JSON list), legacy migration, parameterized bank/country in start_authorization and create_session, get_session takes explicit session_id, list_accounts iterates all sessions
- `src/marcel_core/kbc/sync.py` — sync_account iterates all stored sessions, check_consent_expiry returns list of warnings with bank names, _get_linked_slugs checks both new and legacy credential keys
- `src/marcel_core/skills/integrations/kbc.py` — setup/complete_setup accept bank param, status shows all banks, balance fallback iterates all sessions
- `src/marcel_core/skills/docs/kbc/SKILL.md` — Updated for multi-bank, documented bank param on setup/complete_setup
- `tests/core/test_kbc.py` — 34 tests (was 28): added multi-bank sync, session storage, legacy migration, null field handling
**Commands Run**: `uv run pytest tests/core/test_kbc.py` (34 passed), `uv run ruff check` (clean), `uv run pyright` (0 errors)
**Result**: All checks passing. Live sync verified: KBC (3 accounts) + ING (1 account, 3687.44 EUR balance)
