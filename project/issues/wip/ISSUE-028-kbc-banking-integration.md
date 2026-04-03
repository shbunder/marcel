# ISSUE-028: KBC Banking Integration via GoCardless

**Status:** WIP
**Created:** 2026-04-03
**Assignee:** Marcel
**Priority:** Medium
**Labels:** feature, integration

## Capture
**Original request:** "I want a new integration in marcel, the ability to access my data from kbc (all possible integrations with kbc). One specific example is retrieving transactions."

**Follow-up Q&A:**
- Q: GoCardless account? A: Created a standard account (no free tier found)
- Q: Which accounts? A: Main current account only
- Q: Scope? A: Read-only (balances + transactions)
- Q: Consent expiry notifications? A: Yes, proactive notification when expiry approaches
- Q: Transaction polling? A: Periodic cache sync, questions query cache to stay within 4 req/day PSD2 limit
- Q: Multi-bank? A: KBC only for now
- Q: Sync frequency? A: Every 8 hours (3/day, leaves headroom)
- Q: Query approach? A: Cache all transactions, let Marcel's LLM figure out what to query for natural language questions

**Resolved intent:** Add a KBC banking integration to Marcel using GoCardless Bank Account Data as the API intermediary. The integration caches transactions locally in SQLite (synced every 8 hours) so Marcel can answer natural language questions about spending, income, and account balances without hitting PSD2 rate limits. Read-only scope: accounts, balances, and transactions. Proactive notification when the 90-day consent is about to expire.

## Description

Marcel gains the ability to access the user's KBC bank account data (balances and transactions) via the GoCardless Bank Account Data API (formerly Nordigen). A local SQLite cache stores transactions, synced every 8 hours. The agent queries the cache to answer natural language financial questions.

### Architecture

```
src/marcel_core/
  kbc/
    __init__.py
    client.py      # GoCardless REST client (httpx)
    cache.py       # SQLite transaction/balance cache
    sync.py        # Periodic sync task (every 8h)
  skills/
    integrations/kbc.py        # @register handlers
    docs/kbc/SKILL.md          # Agent-facing documentation
```

### Credentials (in user's credentials store)
- `GOCARDLESS_SECRET_ID` — API secret ID from GoCardless dashboard
- `GOCARDLESS_SECRET_KEY` — API secret key from GoCardless dashboard
- `GOCARDLESS_REQUISITION_ID` — stored after initial bank link setup

### Skills
- `kbc.setup` — create requisition, return auth link, poll for completion
- `kbc.accounts` — list linked accounts
- `kbc.balance` — current balance
- `kbc.transactions` — query cached transactions (date range, free text)

## Tasks
- [✓] ISSUE-028-a: Add GoCardless credentials to user store
- [✓] ISSUE-028-b: Implement `kbc/client.py` — GoCardless API client
- [✓] ISSUE-028-c: Implement `kbc/cache.py` — SQLite transaction cache
- [✓] ISSUE-028-d: Implement `kbc/sync.py` — periodic sync (every 8h)
- [✓] ISSUE-028-e: Implement `skills/integrations/kbc.py` — skill handlers
- [✓] ISSUE-028-f: Create `skills/docs/kbc/SKILL.md` — agent documentation
- [✓] ISSUE-028-g: Write tests
- [✓] ISSUE-028-h: Run `make check`, verify end-to-end

## Subtasks
- [✓] ISSUE-028-a: Add GoCardless credentials to user credential store
- [✓] ISSUE-028-b: GoCardless API client with token management
- [✓] ISSUE-028-c: SQLite cache for transactions and balance snapshots
- [✓] ISSUE-028-d: Periodic sync task hooking into Marcel's scheduler
- [✓] ISSUE-028-e: Skill handler registration
- [✓] ISSUE-028-f: SKILL.md documentation
- [✓] ISSUE-028-g: Tests for client, cache, sync, and skill handlers
- [✓] ISSUE-028-h: Final checks and verification

## Relationships
- Related to: [[ISSUE-015-feat-icloud-integration]] (same integration pattern)

## Comments

## Implementation Log
### 2026-04-03 — LLM Implementation
**Action**: Full KBC banking integration via GoCardless Bank Account Data API
**Files Modified**:
- `src/marcel_core/kbc/__init__.py` — Package init
- `src/marcel_core/kbc/client.py` — GoCardless REST client with JWT token management, requisition creation, account/balance/transaction retrieval
- `src/marcel_core/kbc/cache.py` — SQLite-backed cache for transactions and balances with flexible query filters
- `src/marcel_core/kbc/sync.py` — Background sync task (every 8h) with consent expiry monitoring
- `src/marcel_core/skills/integrations/kbc.py` — 6 skill handlers: setup, status, accounts, balance, transactions, sync
- `src/marcel_core/skills/docs/kbc/SKILL.md` — Agent-facing documentation with query tips
- `src/marcel_core/main.py` — Wired sync loop into FastAPI lifespan
- `.gitignore` — Added `.claude/skills/kbc/`
- `tests/core/test_kbc.py` — 26 tests covering cache, sync, and helpers
**Commands Run**: `uv run pytest tests/core/test_kbc.py` (26 passed), `uv run ruff check` (clean), `uv run pyright` (0 errors)
**Result**: All checks passing
**Note**: User still needs to provide GOCARDLESS_SECRET_ID — only the secret_key has been stored so far
