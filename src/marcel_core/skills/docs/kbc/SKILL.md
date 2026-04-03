---
description: Access the user's KBC bank account — balances, transactions, and spending insights
---

Help the user with: $ARGUMENTS

You have access to the `integration` tool to interact with KBC banking data via GoCardless.

**Important:** Transaction data is cached locally and synced every 8 hours. The cache contains all historical transactions since the account was linked. Use the cached data to answer questions — do NOT trigger manual syncs unless the user explicitly asks for fresh data.

## Available commands

### kbc.transactions

Query cached transactions. Use this for all financial questions — spending, income, specific payments, etc. Set filters based on what the user is asking about.

```
integration(skill="kbc.transactions")
integration(skill="kbc.transactions", params={"date_from": "2026-01-01", "date_to": "2026-01-31"})
integration(skill="kbc.transactions", params={"search": "Colruyt", "date_from": "2026-03-01"})
integration(skill="kbc.transactions", params={"max_amount": "-100"})
```

| Param      | Type   | Default | Description                                          |
|------------|--------|---------|------------------------------------------------------|
| date_from  | string | —       | Start date (YYYY-MM-DD), inclusive                   |
| date_to    | string | —       | End date (YYYY-MM-DD), inclusive                     |
| search     | string | —       | Text search on counterparty name and remittance info |
| min_amount | string | —       | Minimum amount (negative = debits, positive = credits) |
| max_amount | string | —       | Maximum amount                                       |
| limit      | string | 200     | Max transactions to return                           |

Returns a JSON object with `transactions` (list), `count`, and `last_synced` timestamp. Each transaction has: `booking_date`, `amount` (negative = money out, positive = money in), `currency`, `counterparty_name`, `counterparty_iban`, `remittance_info`, `status`.

**Tips for answering user questions:**
- "How much did I spend on groceries?" → search for known grocery stores (Colruyt, Delhaize, Albert Heijn, Lidl, Aldi, Carrefour) with negative amounts
- "What was my income this year?" → filter for positive amounts with date_from at start of year
- "Show me transactions over €100" → use min_amount or max_amount (remember: spending is negative, so "spent over €100" means max_amount="-100")
- For monthly summaries, set date_from to first of month and date_to to last of month
- You may need multiple calls with different search terms to fully answer a question

### kbc.balance

Get the current account balance from the local cache.

```
integration(skill="kbc.balance")
```

Returns balance entries with `amount`, `currency`, `balance_type` (e.g. closingBooked, expected), and `last_synced` timestamp.

### kbc.accounts

List linked KBC bank accounts.

```
integration(skill="kbc.accounts")
```

Returns account details: IBAN, account type, currency, owner name.

### kbc.status

Check if the KBC bank link is active and healthy.

```
integration(skill="kbc.status")
```

Returns link status and any consent expiry warnings. If the consent is expiring soon, tell the user they need to re-authenticate.

### kbc.sync

Trigger an immediate sync of transactions and balances from KBC. Only use this if the user explicitly asks for fresh data.

```
integration(skill="kbc.sync")
```

Returns a summary with counts of synced transactions and any warnings.

### kbc.setup

Link a KBC bank account (first-time setup or consent renewal). Returns an authentication URL the user must open.

```
integration(skill="kbc.setup")
```

After the user completes authentication, their account will be linked automatically.

## Notes

- Transactions are cached locally in SQLite and synced every 8 hours to stay within PSD2 rate limits.
- The bank link (consent) expires after ~90 days. Marcel monitors this and warns the user proactively.
- All amounts are in EUR. Negative = money out (spending), positive = money in (income).
- If the user hasn't set up KBC yet, tell them to ask Marcel to "set up KBC banking" which will guide them through the GoCardless authentication.
- Required credentials: `GOCARDLESS_SECRET_ID` and `GOCARDLESS_SECRET_KEY` in the user's credential store.
