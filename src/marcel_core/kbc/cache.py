"""SQLite-backed transaction and balance cache for KBC banking data.

The cache lives at ``data/users/{slug}/kbc_transactions.db`` and stores
all synced transactions and the latest balance snapshot.  The agent
queries this cache instead of hitting the GoCardless API directly, so
we stay within PSD2 rate limits (4 requests/day without active SCA).
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from marcel_core.storage._root import data_root

log = logging.getLogger(__name__)


def _db_path(slug: str) -> Path:
    return data_root() / 'users' / slug / 'kbc_transactions.db'


def _connect(slug: str) -> sqlite3.Connection:
    path = _db_path(slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    _ensure_schema(conn)
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS transactions (
            internal_id       TEXT PRIMARY KEY,
            transaction_id    TEXT,
            account_id        TEXT NOT NULL,
            booking_date      TEXT,
            value_date        TEXT,
            amount            REAL NOT NULL,
            currency          TEXT NOT NULL DEFAULT 'EUR',
            counterparty_name TEXT,
            counterparty_iban TEXT,
            remittance_info   TEXT,
            bank_tx_code      TEXT,
            status            TEXT NOT NULL DEFAULT 'booked',
            raw_json          TEXT,
            synced_at         TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_tx_booking_date
            ON transactions(booking_date);
        CREATE INDEX IF NOT EXISTS idx_tx_account
            ON transactions(account_id);
        CREATE INDEX IF NOT EXISTS idx_tx_counterparty
            ON transactions(counterparty_name);

        CREATE TABLE IF NOT EXISTS balances (
            account_id     TEXT NOT NULL,
            balance_type   TEXT NOT NULL,
            amount         REAL NOT NULL,
            currency       TEXT NOT NULL DEFAULT 'EUR',
            reference_date TEXT,
            synced_at      TEXT NOT NULL,
            PRIMARY KEY (account_id, balance_type)
        );

        CREATE TABLE IF NOT EXISTS sync_meta (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
    """)


def _tx_internal_id(tx: dict[str, Any], account_id: str) -> str:
    """Derive a stable unique ID for a transaction.

    EnableBanking provides ``transaction_id`` or ``entry_reference``.
    Falls back to a composite key if neither is present.
    """
    if tx.get('transaction_id'):
        return tx['transaction_id']
    if tx.get('entry_reference'):
        return tx['entry_reference']
    # Composite fallback
    amt = tx.get('transaction_amount', {})
    creditor = tx.get('creditor', {})
    debtor = tx.get('debtor', {})
    parts = [
        account_id,
        tx.get('booking_date', ''),
        amt.get('amount', ''),
        creditor.get('name', debtor.get('name', '')),
        '|'.join(tx.get('remittance_information', [])),
    ]
    return '|'.join(parts)


# ── Write operations ─────────────────────────────────────────────────────────


def upsert_transactions(
    slug: str,
    account_id: str,
    transactions: list[dict[str, Any]],
    *,
    status: str = 'booked',
) -> int:
    """Upsert transactions into the cache.  Returns count of new/updated rows."""
    conn = _connect(slug)
    now = datetime.now(UTC).isoformat()
    count = 0
    try:
        for tx in transactions:
            internal_id = _tx_internal_id(tx, account_id)
            amt_obj = tx.get('transaction_amount', {})
            raw_amount = float(amt_obj.get('amount', 0))
            # EnableBanking uses credit_debit_indicator instead of signed amounts
            if tx.get('credit_debit_indicator') == 'DBIT' and raw_amount > 0:
                raw_amount = -raw_amount
            currency = amt_obj.get('currency', 'EUR')
            creditor = tx.get('creditor', {})
            debtor = tx.get('debtor', {})
            counterparty = creditor.get('name', debtor.get('name', ''))
            remittance = ' '.join(tx.get('remittance_information', []))
            bank_tx = tx.get('bank_transaction_code', {})
            bank_tx_str = bank_tx.get('description', '') if isinstance(bank_tx, dict) else str(bank_tx)
            conn.execute(
                """INSERT INTO transactions
                   (internal_id, transaction_id, account_id, booking_date,
                    value_date, amount, currency, counterparty_name,
                    counterparty_iban, remittance_info, bank_tx_code,
                    status, raw_json, synced_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(internal_id) DO UPDATE SET
                       amount = excluded.amount,
                       status = excluded.status,
                       raw_json = excluded.raw_json,
                       synced_at = excluded.synced_at
                """,
                (
                    internal_id,
                    tx.get('transaction_id', ''),
                    account_id,
                    tx.get('booking_date', ''),
                    tx.get('value_date', ''),
                    raw_amount,
                    currency,
                    counterparty,
                    _extract_iban(tx),
                    remittance,
                    bank_tx_str,
                    tx.get('status', status),
                    json.dumps(tx),
                    now,
                ),
            )
            count += 1
        conn.commit()
    finally:
        conn.close()
    return count


def upsert_balances(slug: str, account_id: str, balances: list[dict[str, Any]]) -> None:
    """Upsert balance entries for an account."""
    conn = _connect(slug)
    now = datetime.now(UTC).isoformat()
    try:
        for bal in balances:
            amt = bal.get('balance_amount', {})
            conn.execute(
                """INSERT INTO balances (account_id, balance_type, amount, currency, reference_date, synced_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(account_id, balance_type) DO UPDATE SET
                       amount = excluded.amount,
                       currency = excluded.currency,
                       reference_date = excluded.reference_date,
                       synced_at = excluded.synced_at
                """,
                (
                    account_id,
                    bal.get('balance_type', 'unknown'),
                    float(amt.get('amount', 0)),
                    amt.get('currency', 'EUR'),
                    bal.get('reference_date', ''),
                    now,
                ),
            )
        conn.commit()
    finally:
        conn.close()


def set_sync_meta(slug: str, key: str, value: str) -> None:
    """Store a sync metadata value (e.g. last_sync timestamp)."""
    conn = _connect(slug)
    try:
        conn.execute(
            'INSERT INTO sync_meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value',
            (key, value),
        )
        conn.commit()
    finally:
        conn.close()


# ── Read operations ──────────────────────────────────────────────────────────


def get_transactions(
    slug: str,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    search: str | None = None,
    min_amount: float | None = None,
    max_amount: float | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Query cached transactions with optional filters.

    Returns a list of dicts with transaction fields, ordered by booking_date descending.
    """
    conn = _connect(slug)
    try:
        clauses: list[str] = []
        params: list[Any] = []

        if date_from:
            clauses.append('booking_date >= ?')
            params.append(date_from)
        if date_to:
            clauses.append('booking_date <= ?')
            params.append(date_to)
        if search:
            clauses.append('(counterparty_name LIKE ? OR remittance_info LIKE ?)')
            pattern = f'%{search}%'
            params.extend([pattern, pattern])
        if min_amount is not None:
            clauses.append('amount >= ?')
            params.append(min_amount)
        if max_amount is not None:
            clauses.append('amount <= ?')
            params.append(max_amount)

        where = f'WHERE {" AND ".join(clauses)}' if clauses else ''
        query = f'SELECT * FROM transactions {where} ORDER BY booking_date DESC LIMIT ?'
        params.append(limit)

        rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def get_balances(slug: str) -> list[dict[str, Any]]:
    """Return the latest cached balance entries for all accounts."""
    conn = _connect(slug)
    try:
        rows = conn.execute('SELECT * FROM balances ORDER BY account_id, balance_type').fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def get_sync_meta(slug: str, key: str) -> str | None:
    """Return a sync metadata value, or None if not set."""
    conn = _connect(slug)
    try:
        row = conn.execute('SELECT value FROM sync_meta WHERE key = ?', (key,)).fetchone()
        return row['value'] if row else None
    finally:
        conn.close()


# ── Helpers ──────────────────────────────────────────────────────────────────


def _extract_iban(tx: dict[str, Any]) -> str:
    """Extract counterparty IBAN from a transaction object."""
    for field in ('creditor_account', 'debtor_account'):
        acct = tx.get(field)
        if isinstance(acct, dict):
            # EnableBanking uses 'identification' for the account ID
            iban = acct.get('iban', acct.get('identification', ''))
            if iban:
                return iban
    return ''
