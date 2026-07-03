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
    event = ToolCallEvent(tool_name='bash', args={'command': 'ls'})
    _role_gate_handler(event, USER)
    assert event.blocked is True
    assert 'admin' in (event.block_reason or '')


def test_role_gate_allows_admin_tool_for_admin():
    event = ToolCallEvent(tool_name='bash', args={'command': 'ls'})
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
    event = ToolCallEvent(tool_name='bash', args={'command': 'rm CLAUDE.md'})
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


def test_register_core_handlers_registers_both():
    bus = EventBus()
    register_core_handlers(bus)
    assert bus.handler_count('tool_call') == 2


async def test_registered_guard_blocks_via_bus():
    """End-to-end through the bus: a restricted write is denied, and the
    self-mod guard (registered first) short-circuits before the role gate."""
    bus = EventBus()
    register_core_handlers(bus)
    event = await bus.emit(ToolCallEvent(tool_name='write_file', args={'path': 'CLAUDE.md'}), ADMIN)
    assert event.blocked is True
    assert 'restricted' in (event.block_reason or '').lower()
