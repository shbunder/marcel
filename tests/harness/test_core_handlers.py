"""Tests for the core tool_call handlers: role-gating + self-mod path guard."""

from __future__ import annotations

import pytest

from marcel_core.harness import core_handlers
from marcel_core.harness.core_handlers import (
    _role_gate_handler,
    _self_mod_guard_handler,
    register_core_handlers,
)
from marcel_sdk.events import EventBus, EventContext, ToolCallEvent

USER = EventContext(user_slug='alice', role='user')
ADMIN = EventContext(user_slug='shaun', role='admin')


@pytest.fixture(autouse=True)
def _no_unlock_flag(monkeypatch):
    """Point the unlock flag at a path that does not exist for guard tests.

    So a stray ``.claude/.unlock-safety`` in the working tree cannot make
    the restricted-path tests spuriously pass.
    """
    monkeypatch.setattr(core_handlers, '_UNLOCK_FLAG', '/nonexistent/.unlock-safety')


# --- role gating ----------------------------------------------------------


def test_role_gate_blocks_admin_tool_for_user():
    event = ToolCallEvent(tool_name='run_command', args={'command': 'ls'})
    _role_gate_handler(event, USER)
    assert event.blocked is True
    assert 'admin' in (event.block_reason or '')


def test_role_gate_allows_admin_tool_for_admin():
    event = ToolCallEvent(tool_name='run_command', args={'command': 'ls'})
    _role_gate_handler(event, ADMIN)
    assert event.blocked is False


def test_role_gate_allows_user_tool_for_user():
    event = ToolCallEvent(tool_name='marcel', args={})
    _role_gate_handler(event, USER)
    assert event.blocked is False


def test_role_gate_allows_unknown_tool():
    """A tool not in the admin set is not gated by role (extension tools)."""
    event = ToolCallEvent(tool_name='some_extension_tool', args={})
    _role_gate_handler(event, USER)
    assert event.blocked is False


# --- self-modification path guard -----------------------------------------


@pytest.mark.parametrize(
    'path',
    [
        'CLAUDE.md',
        'docs/CLAUDE.md',
        'src/marcel_core/auth/tokens.py',
        'src/marcel_core/config.py',
        '.env',
        '.env.local',
    ],
)
def test_self_mod_guard_blocks_restricted_write(path):
    event = ToolCallEvent(tool_name='write_file', args={'path': path})
    _self_mod_guard_handler(event, ADMIN)
    assert event.blocked is True
    assert 'restricted' in (event.block_reason or '').lower()


@pytest.mark.parametrize('tool', ['write_file', 'edit_file'])
def test_self_mod_guard_covers_both_writers(tool):
    event = ToolCallEvent(tool_name=tool, args={'path': 'CLAUDE.md'})
    _self_mod_guard_handler(event, ADMIN)
    assert event.blocked is True


def test_self_mod_guard_allows_normal_path():
    event = ToolCallEvent(tool_name='write_file', args={'path': 'src/marcel_core/harness/runner.py'})
    _self_mod_guard_handler(event, ADMIN)
    assert event.blocked is False


def test_self_mod_guard_ignores_non_writing_tools():
    event = ToolCallEvent(tool_name='run_command', args={'command': 'rm CLAUDE.md'})
    _self_mod_guard_handler(event, ADMIN)
    assert event.blocked is False


def test_self_mod_guard_ignores_missing_path():
    event = ToolCallEvent(tool_name='write_file', args={})
    _self_mod_guard_handler(event, ADMIN)
    assert event.blocked is False


def test_self_mod_guard_unlock_flag_lifts_block(tmp_path, monkeypatch):
    flag = tmp_path / '.unlock-safety'
    flag.write_text('')
    monkeypatch.setattr(core_handlers, '_UNLOCK_FLAG', str(flag))
    event = ToolCallEvent(tool_name='write_file', args={'path': 'CLAUDE.md'})
    _self_mod_guard_handler(event, ADMIN)
    assert event.blocked is False


# --- registration ---------------------------------------------------------


def test_register_core_handlers_registers_all():
    bus = EventBus()
    register_core_handlers(bus)
    # self-mod guard + role gate + command policy
    assert bus.handler_count('tool_call') == 3


def test_register_core_handlers_order_is_the_gate_order():
    """The exact registration order IS the gate order (FR2, FEAT-260718-01da2e).

    First-blocker-wins means reordering these silently changes which deny
    message a doubly-blocked call surfaces — pin the identities in order so
    a reorder fails loud instead of passing the counting test above.
    """
    from marcel_core.harness.core_handlers import (
        _command_policy_handler,
        _role_gate_handler,
        _self_mod_guard_handler,
    )

    bus = EventBus()
    register_core_handlers(bus)
    assert bus._handlers['tool_call'] == [
        _self_mod_guard_handler,
        _role_gate_handler,
        _command_policy_handler,
    ]


async def test_registered_guard_blocks_via_bus():
    """End-to-end through the bus: a restricted write is denied, and the
    self-mod guard (registered first) short-circuits before the role gate."""
    bus = EventBus()
    register_core_handlers(bus)
    event = await bus.emit(ToolCallEvent(tool_name='write_file', args={'path': 'CLAUDE.md'}), ADMIN)
    assert event.blocked is True
    assert 'restricted' in (event.block_reason or '').lower()


# --- command policy + approval handler ------------------------------------

import asyncio

from marcel_core.config import settings
from marcel_core.harness.approval import ApprovalOutcome, approval_registry
from marcel_core.harness.command_policy import Verdict
from marcel_core.harness.core_handlers import _command_policy_handler, command_policy
from marcel_core.plugin.channels import register_channel
from marcel_core.storage import _root

CLI = EventContext(user_slug='shaun', role='admin', channel='cli')


class _FakeApprovalChannel:
    """A channel plugin that delivers approval prompts and auto-resolves them."""

    def __init__(self, name: str, outcome: ApprovalOutcome | None) -> None:
        self.name = name
        self._outcome = outcome
        self.sent: list[dict] = []

    async def send_approval_request(self, request: dict) -> bool:
        self.sent.append(request)
        if self._outcome is None:
            return False  # delivery failure → safe-default deny
        approval_id = request['id']
        outcome = self._outcome

        async def _resolve() -> None:
            await asyncio.sleep(0.01)
            approval_registry().resolve(approval_id, outcome)

        asyncio.create_task(_resolve())
        return True


@pytest.fixture
def _policy_env(tmp_path, monkeypatch):
    """Fresh policy + data root + enabled + short window for each policy test."""
    monkeypatch.setattr(core_handlers, '_POLICY', None)
    monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
    monkeypatch.setattr(settings, 'marcel_command_policy_enabled', True)
    monkeypatch.setattr(settings, 'marcel_approval_timeout_seconds', 2.0)


def _ctx_for(chan: _FakeApprovalChannel) -> EventContext:
    register_channel(chan)  # type: ignore[arg-type]
    return EventContext(user_slug='shaun', role='admin', channel=chan.name)


async def test_policy_allows_benign_command(_policy_env):
    event = ToolCallEvent(tool_name='run_command', args={'command': 'ls -la'})
    await _command_policy_handler(event, CLI)
    assert event.blocked is False


async def test_policy_denies_self_mod_shell(_policy_env):
    event = ToolCallEvent(tool_name='run_command', args={'command': 'rm src/marcel_core/auth/x'})
    await _command_policy_handler(event, CLI)
    assert event.blocked is True
    assert 'command policy' in (event.block_reason or '').lower()


async def test_policy_ask_allow_once_proceeds(_policy_env):
    chan = _FakeApprovalChannel('approve_once', ApprovalOutcome.ALLOW_ONCE)
    ctx = _ctx_for(chan)
    event = ToolCallEvent(tool_name='run_command', args={'command': 'docker rm -f marcel'})
    await _command_policy_handler(event, ctx)
    assert event.blocked is False
    assert chan.sent, 'approval should have been forwarded'


async def test_policy_ask_deny_blocks(_policy_env):
    chan = _FakeApprovalChannel('approve_deny', ApprovalOutcome.DENY)
    ctx = _ctx_for(chan)
    event = ToolCallEvent(tool_name='run_command', args={'command': 'sudo reboot'})
    await _command_policy_handler(event, ctx)
    assert event.blocked is True
    assert 'declined' in (event.block_reason or '').lower()


async def test_policy_ask_allow_always_amends_policy(_policy_env):
    chan = _FakeApprovalChannel('approve_always', ApprovalOutcome.ALLOW_ALWAYS)
    ctx = _ctx_for(chan)
    cmd = 'docker rm -f marcel'

    event1 = ToolCallEvent(tool_name='run_command', args={'command': cmd})
    await _command_policy_handler(event1, ctx)
    assert event1.blocked is False
    assert command_policy().classify('run_command', {'command': cmd}).verdict is Verdict.ALLOW

    # A second identical command auto-allows without re-forwarding.
    chan.sent.clear()
    event2 = ToolCallEvent(tool_name='run_command', args={'command': cmd})
    await _command_policy_handler(event2, ctx)
    assert event2.blocked is False
    assert chan.sent == []


async def test_policy_ask_undeliverable_channel_denies(_policy_env):
    # 'cli' has no registered plugin → no send_approval_request → safe deny.
    event = ToolCallEvent(tool_name='run_command', args={'command': 'sudo reboot'})
    await _command_policy_handler(event, CLI)
    assert event.blocked is True


async def test_policy_disabled_is_noop(_policy_env, monkeypatch):
    monkeypatch.setattr(settings, 'marcel_command_policy_enabled', False)
    event = ToolCallEvent(tool_name='run_command', args={'command': 'rm src/marcel_core/auth/x'})
    await _command_policy_handler(event, CLI)
    assert event.blocked is False


def test_command_policy_singleton(_policy_env):
    assert command_policy() is command_policy()
