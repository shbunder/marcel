# Banking Integration (KBC, ING)

Marcel can access bank account data — balances, transactions, and spending insights — via the [EnableBanking](https://enablebanking.com) open banking API. Multiple banks can be linked simultaneously. Transaction data is cached locally in SQLite and synced every 8 hours to stay within PSD2 rate limits (4 requests/day without active SCA).

Currently supported banks: **KBC**, **ING** (both Belgium). Other EnableBanking-supported banks can be added by name.

## Onboarding

Setting up the banking integration requires an EnableBanking developer account and at least one linked bank account. This is a one-time setup per Marcel instance; additional banks can be linked later.

### 1. Create an EnableBanking account

Sign up at [enablebanking.com](https://enablebanking.com) and create an application:

1. Go to the EnableBanking dashboard and create a new application.
2. Fill in the required fields:
   - **Name**: your application name (e.g. "Marcel")
   - **Environment**: select `PRODUCTION` (sandbox has limited bank coverage)
   - **Redirect URLs**: `https://enablebanking.com` (or your own callback URL)
   - **Account Information Mode**: select `Restricted` (read-only access to accounts, balances, and transactions)
3. Generate or upload an RSA keypair. EnableBanking uses RS256 JWT authentication — you need a private key (PEM format) for signing API requests.
4. Note the **Application ID** (UUID) from your dashboard.
5. Activate the application through the EnableBanking dashboard if it's not already active.

For detailed API documentation, see the [EnableBanking Quick Start](https://enablebanking.com/docs/api/quick-start/).

### 2. Store credentials

Each Marcel user who wants banking access needs two things in their user data directory. These are shared across all banks — you only set them up once.

**Private key** — copy the PEM file to the user's data directory:

```bash
cp /path/to/your/private-key.pem ~/.marcel/users/<slug>/enablebanking.pem
```

The file must be named `enablebanking.pem` and contain the RSA private key used to sign JWTs.

**Application ID** — store it in the user's encrypted credential store. This can be done through Marcel's credential management or directly:

```python
from marcel_core.storage.credentials import load_credentials, save_credentials

creds = load_credentials("shaun")
creds["ENABLEBANKING_APP_ID"] = "your-application-uuid"
save_credentials("shaun", creds)
```

### 3. Link a bank account

Once credentials are stored, link each bank through Marcel:

1. Ask Marcel to "set up KBC banking" (or "set up ING banking") — this calls `kbc.setup` with the appropriate bank name and returns an authentication URL.
2. Open the URL in your browser. You'll be redirected to the bank's login page.
3. Authenticate with your bank's app or card reader.
4. After authorization, you'll be redirected to a URL containing a `code` parameter.
5. Give Marcel the full redirect URL or just the code value — it calls `kbc.complete_setup` to exchange the code for a session.

Each bank requires its own authorization. Repeat these steps for each bank you want to link. Sessions from different banks are stored independently and don't interfere with each other.

The session per bank is valid for ~90 days. Marcel monitors expiry for all linked banks and warns proactively when fewer than 7 days remain. When a consent expires, repeat the linking steps for that specific bank.

### 4. Verify

After linking, ask Marcel "what's my balance?" or "show my recent transactions" to confirm the integration works. The first sync runs automatically within 30 seconds of startup. Balances and transactions from all linked banks appear together.

## Architecture

```
src/marcel_core/kbc/
    __init__.py     # package init
    client.py       # EnableBanking REST client (JWT auth, multi-bank sessions)
    cache.py        # SQLite transaction/balance cache
    sync.py         # background sync task (every 8h, all banks)
```

### Client (`client.py`)

Handles all communication with the EnableBanking API. Authentication uses RS256-signed JWTs with a 1-hour expiry (shared across all banks — one EnableBanking app covers multiple banks). The client stores multiple bank sessions as a JSON list in the credential store under `ENABLEBANKING_SESSIONS`, with automatic migration from the legacy single-session format.

### Cache (`cache.py`)

SQLite database at `data/users/{slug}/kbc_transactions.db` with WAL journaling. Three tables:

- **transactions** — all synced transactions from all banks, keyed by a stable internal ID derived from the bank's transaction ID or a composite fallback. Stores signed amounts (negative for debits, positive for credits).
- **balances** — latest balance snapshot per account and balance type (e.g. CLBD, ITAV, XPCD).
- **sync_meta** — key-value store for sync state (last sync date, consent warnings).

### Sync (`sync.py`)

Runs as an asyncio background task, started in the FastAPI lifespan. Iterates all stored bank sessions and syncs each one every 8 hours (3 syncs/day, leaving headroom within the 4 req/day PSD2 limit). Also checks consent expiry per bank and stores warnings in sync_meta.

## Skill handlers

All handlers are in `src/marcel_core/skills/integrations/kbc.py` and registered with `@register`:

| Skill | Description |
|---|---|
| `kbc.setup` | Start a bank link authorization flow (accepts `bank` param) |
| `kbc.complete_setup` | Exchange auth code for session (accepts `bank` param) |
| `kbc.status` | Check link health for all banks |
| `kbc.accounts` | List accounts across all linked banks |
| `kbc.balance` | Get cached balances from all banks |
| `kbc.transactions` | Query cached transactions with filters (all banks) |
| `kbc.sync` | Trigger an immediate manual sync of all banks |

The agent-facing documentation is in `src/marcel_core/skills/docs/kbc/SKILL.md` — this teaches Marcel how to translate natural language financial questions into the right `integration()` calls.

## Credentials reference

| Credential | Location | Description |
|---|---|---|
| `ENABLEBANKING_APP_ID` | User credential store | EnableBanking application UUID (shared across banks) |
| `ENABLEBANKING_SESSIONS` | User credential store | JSON list of `{bank, country, session_id}` entries (auto-managed) |
| `enablebanking.pem` | `data/users/{slug}/` | RSA private key for JWT signing (shared across banks) |

The legacy `ENABLEBANKING_SESSION_ID` key is automatically migrated to the new `ENABLEBANKING_SESSIONS` format on first access.
