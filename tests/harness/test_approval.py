"""Tests for the command-approval registry + its audit/queue persistence."""

from __future__ import annotations

import asyncio

import pytest

from marcel_core.harness.approval import (
    ApprovalOutcome,
    ApprovalRegistry,
    ApprovalRequest,
    approval_registry,
)
from marcel_core.storage import _root, approvals as approval_store


@pytest.fixture(autouse=True)
def _data_root(tmp_path, monkeypatch):
    monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)


def _req(reg: ApprovalRegistry) -> ApprovalRequest:
    return reg.new_request(
        user_slug='shaun',
        channel='telegram',
        tool_name='bash',
        summary='run: docker rm -f marcel',
        args={'command': 'docker rm -f marcel'},
    )


def test_new_request_has_unique_id_and_timestamp():
    reg = ApprovalRegistry()
    a, b = _req(reg), _req(reg)
    assert a.id != b.id
    assert a.created_at
    assert a.tool_name == 'bash'


async def test_resolve_allow_once_wakes_waiter():
    reg = ApprovalRegistry()
    req = _req(reg)

    async def approve_soon():
        # Let wait() register the future first.
        await asyncio.sleep(0.01)
        assert reg.resolve(req.id, ApprovalOutcome.ALLOW_ONCE) is True

    outcome, _ = await asyncio.gather(reg.wait(req, timeout=2), approve_soon())
    assert outcome is ApprovalOutcome.ALLOW_ONCE
    assert req.id not in reg.pending_ids()


async def test_resolve_deny():
    reg = ApprovalRegistry()
    req = _req(reg)

    async def deny_soon():
        await asyncio.sleep(0.01)
        reg.resolve(req.id, ApprovalOutcome.DENY)

    outcome, _ = await asyncio.gather(reg.wait(req, timeout=2), deny_soon())
    assert outcome is ApprovalOutcome.DENY


async def test_expiry_denies_queues_and_audits():
    reg = ApprovalRegistry()
    req = _req(reg)

    outcome = await reg.wait(req, timeout=0.05)
    assert outcome is ApprovalOutcome.EXPIRED

    # Queued for later approval...
    queued = approval_store.list_queued()
    assert [r['id'] for r in queued] == [req.id]
    record = approval_store.get_queued(req.id)
    assert record is not None
    assert record['args']['command'] == 'docker rm -f marcel'
    # ...and written to the audit log.
    audit = approval_store.read_audit()
    assert audit[-1]['id'] == req.id
    assert audit[-1]['outcome'] == 'expired'


async def test_resolve_after_expiry_is_noop():
    reg = ApprovalRegistry()
    req = _req(reg)
    await reg.wait(req, timeout=0.05)
    # The window closed; a late button press must not crash or resolve.
    assert reg.resolve(req.id, ApprovalOutcome.ALLOW_ONCE) is False


def test_resolve_unknown_id_is_false():
    reg = ApprovalRegistry()
    assert reg.resolve('does-not-exist', ApprovalOutcome.ALLOW_ONCE) is False


async def test_audit_written_on_allow():
    reg = ApprovalRegistry()
    req = _req(reg)

    async def approve():
        await asyncio.sleep(0.01)
        reg.resolve(req.id, ApprovalOutcome.ALLOW_ALWAYS)

    await asyncio.gather(reg.wait(req, timeout=2), approve())
    audit = approval_store.read_audit()
    assert audit[-1]['outcome'] == 'allow_always'
    assert audit[-1]['user_slug'] == 'shaun'


def test_request_roundtrips_through_dict():
    reg = ApprovalRegistry()
    req = _req(reg)
    restored = ApprovalRequest.from_dict(req.to_dict())
    assert restored == req


def test_process_registry_is_singleton():
    assert approval_registry() is approval_registry()


def test_storage_empty_states():
    # No files yet → empty collections, never a crash.
    assert approval_store.list_queued() == []
    assert approval_store.read_audit() == []
    assert approval_store.get_queued('nope') is None


def test_storage_tolerates_corrupt_files(tmp_path):
    # A corrupt queue file / audit line is skipped, not fatal.
    approval_store.queue_pending({'id': 'good', 'created_at': '2026-01-01', 'args': {}})
    (tmp_path / 'approvals' / 'queue' / 'bad.json').write_text('{not json', encoding='utf-8')
    ids = [r['id'] for r in approval_store.list_queued()]
    assert ids == ['good']
    assert approval_store.get_queued('bad') is None

    approval_store.append_audit({'id': 'a', 'outcome': 'deny'})
    (tmp_path / 'approvals' / 'audit.jsonl').open('a', encoding='utf-8').write('\nnot-json\n')
    outcomes = [e.get('outcome') for e in approval_store.read_audit()]
    assert outcomes == ['deny']


def test_remove_queued(tmp_path):
    reg = ApprovalRegistry()
    req = _req(reg)
    approval_store.queue_pending(req.to_dict())
    assert approval_store.get_queued(req.id) is not None
    approval_store.remove_queued(req.id)
    assert approval_store.get_queued(req.id) is None
    approval_store.remove_queued(req.id)  # idempotent
