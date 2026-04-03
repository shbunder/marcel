# KBC Banking Integration

Marcel can access KBC bank account data — balances, transactions, and spending insights — via the [EnableBanking](https://enablebanking.com) open banking API. Transaction data is cached locally in SQLite and synced every 8 hours to stay within PSD2 rate limits (4 requests/day without active SCA).

## Onboarding

Setting up the KBC integration requires an EnableBanking developer account and a linked KBC bank account. This is a one-time setup per Marcel instance.

### 1. Create an EnableBanking account

Sign up at [enablebanking.com](https://enablebanking.com) and create an application:

1. Go to the EnableBanking dashboard and create a new application.
2. Fill in the required fields:
   - **Name**: your application name (e.g. "Marcel")
   - **Environment**: select `PRODUCTION` (sandbox does not include KBC)
   - **Redirect URLs**: `https://enablebanking.com` (or your own callback URL)
   - **Account Information Mode**: select `Restricted` (read-only access to accounts, balances, and transactions)
3. Generate or upload an RSA keypair. EnableBanking uses RS256 JWT authentication — you need a private key (PEM format) for signing API requests.
4. Note the **Application ID** (UUID) from your dashboard.
5. Activate the application through the EnableBanking dashboard if it's not already active.

For detailed API documentation, see the [EnableBanking Quick Start](https://enablebanking.com/docs/api/quick-start/).

### 2. Store credentials

Each Marcel user who wants banking access needs two things in their user data directory:

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

### 3. Link your KBC account

Once credentials are stored, link your bank account through Marcel:

1. Ask Marcel to "set up KBC banking" — this calls `kbc.setup` which starts the EnableBanking authorization flow and returns an authentication URL.
2. Open the URL in your browser. You'll be redirected to KBC's login page.
3. Authenticate with KBC Mobile (itsme or card reader, depending on your KBC setup).
4. After authorization, you'll be redirected to a URL containing a `code` parameter.
5. Give Marcel the full redirect URL or just the code value — it calls `kbc.complete_setup` to exchange the code for a session.

The session is valid for ~90 days. Marcel monitors expiry and warns proactively when fewer than 7 days remain. When the consent expires, repeat step 3 to re-link.

### 4. Verify

After linking, ask Marcel "what's my balance?" or "show my recent transactions" to confirm the integration works. The first sync runs automatically within 30 seconds of startup.

## Architecture

```
src/marcel_core/kbc/
    __init__.py     # package init
    client.py       # EnableBanking REST client (JWT auth, httpx)
    cache.py        # SQLite transaction/balance cache
    sync.py         # background sync task (every 8h)
```

### Client (`client.py`)

Handles all communication with the EnableBanking API. Authentication uses RS256-signed JWTs with a 1-hour expiry. The client manages the full authorization flow (start → user authenticates → exchange code for session) and provides methods for fetching balances and transactions with automatic pagination.

### Cache (`cache.py`)

SQLite database at `data/users/{slug}/kbc_transactions.db` with WAL journaling. Three tables:

- **transactions** — all synced transactions, keyed by a stable internal ID derived from the bank's transaction ID or a composite fallback. Stores signed amounts (negative for debits, positive for credits).
- **balances** — latest balance snapshot per account and balance type (e.g. CLBD, ITAV).
- **sync_meta** — key-value store for sync state (last sync date, consent warnings).

### Sync (`sync.py`)

Runs as an asyncio background task, started in the FastAPI lifespan. Syncs all linked users every 8 hours (3 syncs/day, leaving headroom within the 4 req/day PSD2 limit). Also checks consent expiry and stores a warning in sync_meta when fewer than 7 days remain.

## Skill handlers

All handlers are in `src/marcel_core/skills/integrations/kbc.py` and registered with `@register`:

| Skill | Description |
|---|---|
| `kbc.setup` | Start the bank link authorization flow |
| `kbc.complete_setup` | Exchange auth code for session |
| `kbc.status` | Check link health and consent expiry |
| `kbc.accounts` | List linked accounts |
| `kbc.balance` | Get cached balances (falls back to live API) |
| `kbc.transactions` | Query cached transactions with filters |
| `kbc.sync` | Trigger an immediate manual sync |

The agent-facing documentation is in `src/marcel_core/skills/docs/kbc/SKILL.md` — this teaches Marcel how to translate natural language financial questions into the right `integration()` calls.

## Credentials reference

| Credential | Location | Description |
|---|---|---|
| `ENABLEBANKING_APP_ID` | User credential store | EnableBanking application UUID |
| `ENABLEBANKING_SESSION_ID` | User credential store | Stored automatically after `kbc.complete_setup` |
| `enablebanking.pem` | `data/users/{slug}/` | RSA private key for JWT signing |
