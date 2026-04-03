"""Tests for KBC banking integration — client, cache, and sync."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from marcel_core.kbc.cache import (
    get_balances,
    get_sync_meta,
    get_transactions,
    set_sync_meta,
    upsert_balances,
    upsert_transactions,
)

# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _use_tmp_data_root(tmp_path, monkeypatch):
    """Point data_root() at a temporary directory for all tests."""
    import marcel_core.storage._root as root

    monkeypatch.setattr(root, '_DATA_ROOT', tmp_path)
    (tmp_path / 'users' / 'test').mkdir(parents=True)


def _make_tx(
    *,
    amount: str = '42.50',
    indicator: str = 'DBIT',
    counterparty: str = 'Colruyt',
    booking_date: str = '2026-04-01',
    tx_id: str = 'tx-001',
    remittance: str = 'Payment for groceries',
) -> dict:
    """Create an EnableBanking-style transaction object."""
    party_field = 'creditor' if indicator == 'DBIT' else 'debtor'
    return {
        'transaction_id': tx_id,
        'booking_date': booking_date,
        'value_date': booking_date,
        'transaction_amount': {'amount': amount, 'currency': 'EUR'},
        'credit_debit_indicator': indicator,
        party_field: {'name': counterparty},
        f'{party_field}_account': {'iban': 'BE12345678901234'},
        'remittance_information': [remittance],
        'status': 'BOOK',
        'bank_transaction_code': {'description': 'Payment'},
    }


def _make_balance(
    *,
    amount: str = '1234.56',
    balance_type: str = 'CLBD',
    ref_date: str = '2026-04-01',
) -> dict:
    """Create an EnableBanking-style balance object."""
    return {
        'balance_amount': {'amount': amount, 'currency': 'EUR'},
        'balance_type': balance_type,
        'reference_date': ref_date,
    }


# ── Cache: transactions ──────────────────────────────────────────────────────


class TestCacheTransactions:
    def test_upsert_and_query(self):
        tx = _make_tx()
        count = upsert_transactions('test', 'acct-1', [tx])
        assert count == 1

        rows = get_transactions('test')
        assert len(rows) == 1
        assert rows[0]['counterparty_name'] == 'Colruyt'
        # DBIT with positive amount → stored as negative
        assert rows[0]['amount'] == -42.50
        assert rows[0]['booking_date'] == '2026-04-01'

    def test_credit_transaction_stays_positive(self):
        tx = _make_tx(indicator='CRDT', amount='1000.00', counterparty='Employer')
        upsert_transactions('test', 'acct-1', [tx])
        rows = get_transactions('test')
        assert rows[0]['amount'] == 1000.0

    def test_upsert_is_idempotent(self):
        tx = _make_tx()
        upsert_transactions('test', 'acct-1', [tx])
        upsert_transactions('test', 'acct-1', [tx])
        rows = get_transactions('test')
        assert len(rows) == 1

    def test_upsert_updates_existing(self):
        tx = _make_tx(amount='42.50')
        upsert_transactions('test', 'acct-1', [tx])

        tx['transaction_amount']['amount'] = '99.99'
        upsert_transactions('test', 'acct-1', [tx])

        rows = get_transactions('test')
        assert len(rows) == 1
        assert rows[0]['amount'] == -99.99

    def test_filter_by_date_range(self):
        txs = [
            _make_tx(tx_id='tx-jan', booking_date='2026-01-15'),
            _make_tx(tx_id='tx-feb', booking_date='2026-02-15'),
            _make_tx(tx_id='tx-mar', booking_date='2026-03-15'),
        ]
        upsert_transactions('test', 'acct-1', txs)

        rows = get_transactions('test', date_from='2026-02-01', date_to='2026-02-28')
        assert len(rows) == 1
        assert rows[0]['booking_date'] == '2026-02-15'

    def test_filter_by_search(self):
        txs = [
            _make_tx(tx_id='tx-1', counterparty='Colruyt'),
            _make_tx(tx_id='tx-2', counterparty='Delhaize'),
        ]
        upsert_transactions('test', 'acct-1', txs)

        rows = get_transactions('test', search='Colruyt')
        assert len(rows) == 1
        assert rows[0]['counterparty_name'] == 'Colruyt'

    def test_filter_by_amount_range(self):
        txs = [
            _make_tx(tx_id='tx-small', amount='10.00'),
            _make_tx(tx_id='tx-big', amount='500.00'),
        ]
        upsert_transactions('test', 'acct-1', txs)

        rows = get_transactions('test', max_amount=-100.0)
        assert len(rows) == 1
        assert rows[0]['amount'] == -500.0

    def test_limit(self):
        txs = [_make_tx(tx_id=f'tx-{i}', booking_date=f'2026-01-{i + 1:02d}') for i in range(10)]
        upsert_transactions('test', 'acct-1', txs)
        rows = get_transactions('test', limit=3)
        assert len(rows) == 3

    def test_multiple_accounts(self):
        upsert_transactions('test', 'acct-1', [_make_tx(tx_id='tx-a1')])
        upsert_transactions('test', 'acct-2', [_make_tx(tx_id='tx-a2')])
        rows = get_transactions('test')
        assert len(rows) == 2

    def test_composite_id_fallback(self):
        """Transactions without IDs get a composite key."""
        tx = {
            'booking_date': '2026-04-01',
            'transaction_amount': {'amount': '25.00', 'currency': 'EUR'},
            'credit_debit_indicator': 'DBIT',
            'creditor': {'name': 'Test Shop'},
            'remittance_information': ['Purchase'],
        }
        count = upsert_transactions('test', 'acct-1', [tx])
        assert count == 1
        rows = get_transactions('test')
        assert len(rows) == 1


# ── Cache: balances ──────────────────────────────────────────────────────────


class TestCacheBalances:
    def test_upsert_and_query(self):
        bal = _make_balance()
        upsert_balances('test', 'acct-1', [bal])

        rows = get_balances('test')
        assert len(rows) == 1
        assert rows[0]['amount'] == 1234.56
        assert rows[0]['balance_type'] == 'CLBD'

    def test_upsert_updates_existing(self):
        upsert_balances('test', 'acct-1', [_make_balance(amount='100.00')])
        upsert_balances('test', 'acct-1', [_make_balance(amount='200.00')])

        rows = get_balances('test')
        assert len(rows) == 1
        assert rows[0]['amount'] == 200.0

    def test_multiple_balance_types(self):
        bals = [
            _make_balance(balance_type='CLBD', amount='100.00'),
            _make_balance(balance_type='ITAV', amount='150.00'),
        ]
        upsert_balances('test', 'acct-1', bals)
        rows = get_balances('test')
        assert len(rows) == 2


# ── Cache: sync metadata ────────────────────────────────────────────────────


class TestSyncMeta:
    def test_set_and_get(self):
        set_sync_meta('test', 'last_sync_date', '2026-04-01')
        assert get_sync_meta('test', 'last_sync_date') == '2026-04-01'

    def test_get_missing_returns_none(self):
        assert get_sync_meta('test', 'nonexistent') is None

    def test_update_overwrites(self):
        set_sync_meta('test', 'key', 'first')
        set_sync_meta('test', 'key', 'second')
        assert get_sync_meta('test', 'key') == 'second'


# ── Sync ─────────────────────────────────────────────────────────────────────


class TestSync:
    @pytest.mark.asyncio
    async def test_sync_account_no_session(self):
        from marcel_core.kbc.sync import sync_account

        with patch(
            'marcel_core.kbc.sync.client.get_session',
            new_callable=AsyncMock,
            side_effect=RuntimeError('No KBC bank link found'),
        ):
            summary = await sync_account('test')
            assert any('No KBC bank link' in w for w in summary['warnings'])

    @pytest.mark.asyncio
    async def test_sync_account_not_authorized(self):
        from marcel_core.kbc.sync import sync_account

        with patch(
            'marcel_core.kbc.sync.client.get_session',
            new_callable=AsyncMock,
            return_value={'status': 'EXPIRED', 'accounts': []},
        ):
            summary = await sync_account('test')
            assert any('expected AUTHORIZED' in w for w in summary['warnings'])

    @pytest.mark.asyncio
    async def test_sync_account_success(self):
        from marcel_core.kbc.sync import sync_account

        mock_session = {
            'status': 'AUTHORIZED',
            'accounts': [{'uid': 'acct-1'}],
        }
        mock_balances = [_make_balance()]
        mock_txs = [_make_tx()]

        with (
            patch(
                'marcel_core.kbc.sync.client.get_session',
                new_callable=AsyncMock,
                return_value=mock_session,
            ),
            patch(
                'marcel_core.kbc.sync.client.get_balances',
                new_callable=AsyncMock,
                return_value=mock_balances,
            ),
            patch(
                'marcel_core.kbc.sync.client.get_all_transactions',
                new_callable=AsyncMock,
                return_value=mock_txs,
            ),
        ):
            summary = await sync_account('test')
            assert summary['synced'] == 1
            assert not summary['warnings']

            rows = get_transactions('test')
            assert len(rows) == 1
            bals = get_balances('test')
            assert len(bals) == 1

    @pytest.mark.asyncio
    async def test_check_consent_expiry_warns(self):
        from marcel_core.kbc.sync import check_consent_expiry

        # Session valid_until is 5 days from now
        valid_until = (datetime.now(UTC) + timedelta(days=5)).isoformat()
        mock_session = {
            'status': 'AUTHORIZED',
            'access': {'valid_until': valid_until},
        }

        with patch(
            'marcel_core.kbc.sync.client.get_session',
            new_callable=AsyncMock,
            return_value=mock_session,
        ):
            warning = await check_consent_expiry('test')
            assert warning is not None
            assert 'expires' in warning

    @pytest.mark.asyncio
    async def test_check_consent_expiry_ok(self):
        from marcel_core.kbc.sync import check_consent_expiry

        # Session valid_until is 80 days from now
        valid_until = (datetime.now(UTC) + timedelta(days=80)).isoformat()
        mock_session = {
            'status': 'AUTHORIZED',
            'access': {'valid_until': valid_until},
        }

        with patch(
            'marcel_core.kbc.sync.client.get_session',
            new_callable=AsyncMock,
            return_value=mock_session,
        ):
            warning = await check_consent_expiry('test')
            assert warning is None


# ── Client helpers ───────────────────────────────────────────────────────────


class TestClientHelpers:
    def test_tx_internal_id_prefers_transaction_id(self):
        from marcel_core.kbc.cache import _tx_internal_id

        tx = {'transaction_id': 'tx-1', 'entry_reference': 'ref-1'}
        assert _tx_internal_id(tx, 'acct') == 'tx-1'

    def test_tx_internal_id_falls_back_to_entry_reference(self):
        from marcel_core.kbc.cache import _tx_internal_id

        tx = {'entry_reference': 'ref-1'}
        assert _tx_internal_id(tx, 'acct') == 'ref-1'

    def test_tx_internal_id_composite_fallback(self):
        from marcel_core.kbc.cache import _tx_internal_id

        tx = {
            'booking_date': '2026-04-01',
            'transaction_amount': {'amount': '25.00'},
            'creditor': {'name': 'Shop'},
            'remittance_information': ['Purchase'],
        }
        result = _tx_internal_id(tx, 'acct-1')
        assert 'acct-1' in result
        assert '2026-04-01' in result

    def test_extract_iban_creditor(self):
        from marcel_core.kbc.cache import _extract_iban

        tx = {'creditor_account': {'iban': 'BE123'}}
        assert _extract_iban(tx) == 'BE123'

    def test_extract_iban_debtor(self):
        from marcel_core.kbc.cache import _extract_iban

        tx = {'debtor_account': {'iban': 'BE456'}}
        assert _extract_iban(tx) == 'BE456'

    def test_extract_iban_identification_fallback(self):
        from marcel_core.kbc.cache import _extract_iban

        tx = {'creditor_account': {'identification': 'BE789'}}
        assert _extract_iban(tx) == 'BE789'

    def test_extract_iban_none(self):
        from marcel_core.kbc.cache import _extract_iban

        assert _extract_iban({}) == ''
