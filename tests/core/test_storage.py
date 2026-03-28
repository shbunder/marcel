"""Tests for ISSUE-002: flat-file storage layer.

All tests use ``tmp_path`` to point the storage module at an isolated
temporary directory so no real data is read or written.
"""

import pathlib

import pytest

import marcel_core.storage._root as _root_mod
from marcel_core.storage import (
    append_turn,
    load_conversation,
    load_conversation_index,
    load_memory_file,
    load_memory_index,
    load_user_profile,
    new_conversation,
    save_memory_file,
    save_user_profile,
    update_conversation_index,
    update_memory_index,
    user_exists,
)


@pytest.fixture(autouse=True)
def isolated_data_root(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Override the storage module data root for every test in this file."""
    monkeypatch.setattr(_root_mod, '_DATA_ROOT', tmp_path)


# ---------------------------------------------------------------------------
# users
# ---------------------------------------------------------------------------


class TestUserExists:
    def test_returns_false_when_directory_missing(self) -> None:
        assert user_exists('nobody') is False

    def test_returns_true_when_directory_present(self, tmp_path: pathlib.Path) -> None:
        (tmp_path / 'users' / 'shaun').mkdir(parents=True)
        assert user_exists('shaun') is True


class TestLoadUserProfile:
    def test_returns_empty_string_when_missing(self) -> None:
        assert load_user_profile('ghost') == ''

    def test_returns_content_when_present(self, tmp_path: pathlib.Path) -> None:
        profile_path = tmp_path / 'users' / 'alice' / 'profile.md'
        profile_path.parent.mkdir(parents=True)
        profile_path.write_text('# Alice\n', encoding='utf-8')
        assert load_user_profile('alice') == '# Alice\n'


class TestSaveUserProfile:
    def test_round_trip(self) -> None:
        content = '# Bob\nPrefers morning meetings.\n'
        save_user_profile('bob', content)
        assert load_user_profile('bob') == content

    def test_creates_directory_if_missing(self, tmp_path: pathlib.Path) -> None:
        save_user_profile('new_user', '# New\n')
        assert (tmp_path / 'users' / 'new_user' / 'profile.md').exists()

    def test_overwrites_existing(self) -> None:
        save_user_profile('carol', '# Carol v1\n')
        save_user_profile('carol', '# Carol v2\n')
        assert load_user_profile('carol') == '# Carol v2\n'


# ---------------------------------------------------------------------------
# conversations
# ---------------------------------------------------------------------------


class TestNewConversation:
    def test_returns_filename_stem(self) -> None:
        stem = new_conversation('shaun', 'cli')
        # Format: YYYY-MM-DDTHH-MM
        assert len(stem) == len('2026-03-26T14-32')
        assert stem[10] == 'T'
        assert stem[13] == '-'

    def test_creates_file(self, tmp_path: pathlib.Path) -> None:
        stem = new_conversation('shaun', 'cli')
        path = tmp_path / 'users' / 'shaun' / 'conversations' / f'{stem}.md'
        assert path.exists()

    def test_file_contains_header(self, tmp_path: pathlib.Path) -> None:
        stem = new_conversation('shaun', 'telegram')
        path = tmp_path / 'users' / 'shaun' / 'conversations' / f'{stem}.md'
        content = path.read_text(encoding='utf-8')
        assert '# Conversation —' in content
        assert 'channel: telegram' in content


class TestAppendTurn:
    def test_appends_user_turn(self, tmp_path: pathlib.Path) -> None:
        stem = new_conversation('shaun', 'cli')
        append_turn('shaun', stem, 'user', 'Hello!')
        content = load_conversation('shaun', stem)
        assert '**User:** Hello!' in content

    def test_appends_assistant_turn(self, tmp_path: pathlib.Path) -> None:
        stem = new_conversation('shaun', 'cli')
        append_turn('shaun', stem, 'assistant', 'Hi there!')
        content = load_conversation('shaun', stem)
        assert '**Marcel:** Hi there!' in content

    def test_multiple_turns_in_order(self) -> None:
        stem = new_conversation('shaun', 'cli')
        append_turn('shaun', stem, 'user', 'First')
        append_turn('shaun', stem, 'assistant', 'Second')
        content = load_conversation('shaun', stem)
        assert content.index('**User:** First') < content.index('**Marcel:** Second')


class TestLoadConversation:
    def test_returns_empty_string_when_missing(self) -> None:
        assert load_conversation('shaun', '2099-01-01T00-00') == ''

    def test_returns_full_transcript(self) -> None:
        stem = new_conversation('shaun', 'cli')
        append_turn('shaun', stem, 'user', 'Ping')
        append_turn('shaun', stem, 'assistant', 'Pong')
        content = load_conversation('shaun', stem)
        assert '**User:** Ping' in content
        assert '**Marcel:** Pong' in content


class TestLoadConversationIndex:
    def test_returns_empty_string_when_no_index(self) -> None:
        assert load_conversation_index('nobody') == ''

    def test_returns_index_content(self, tmp_path: pathlib.Path) -> None:
        idx = tmp_path / 'users' / 'shaun' / 'conversations' / 'index.md'
        idx.parent.mkdir(parents=True)
        idx.write_text('# Conversations\n', encoding='utf-8')
        assert load_conversation_index('shaun') == '# Conversations\n'


class TestUpdateConversationIndex:
    def test_creates_index_if_missing(self, tmp_path: pathlib.Path) -> None:
        update_conversation_index('shaun', '2026-03-26T14-32', 'calendar check')
        idx = tmp_path / 'users' / 'shaun' / 'conversations' / 'index.md'
        assert idx.exists()

    def test_appends_entry(self) -> None:
        update_conversation_index('shaun', '2026-03-26T14-32', 'calendar check')
        content = load_conversation_index('shaun')
        assert '[2026-03-26T14-32]' in content
        assert 'calendar check' in content

    def test_appends_multiple_entries(self) -> None:
        update_conversation_index('shaun', '2026-03-25T09-11', 'first session')
        update_conversation_index('shaun', '2026-03-26T14-32', 'second session')
        content = load_conversation_index('shaun')
        assert '2026-03-25T09-11' in content
        assert '2026-03-26T14-32' in content


# ---------------------------------------------------------------------------
# memory
# ---------------------------------------------------------------------------


class TestLoadMemoryIndex:
    def test_returns_empty_string_when_missing(self) -> None:
        assert load_memory_index('nobody') == ''

    def test_returns_index_content(self, tmp_path: pathlib.Path) -> None:
        idx = tmp_path / 'users' / 'shaun' / 'memory' / 'index.md'
        idx.parent.mkdir(parents=True)
        idx.write_text('# Memory\n', encoding='utf-8')
        assert load_memory_index('shaun') == '# Memory\n'


class TestSaveAndLoadMemoryFile:
    def test_round_trip(self) -> None:
        content = '# Calendar\nShaun prefers afternoon dentist appointments.\n'
        save_memory_file('shaun', 'calendar', content)
        assert load_memory_file('shaun', 'calendar') == content

    def test_returns_empty_string_when_missing(self) -> None:
        assert load_memory_file('shaun', 'nonexistent') == ''

    def test_creates_directory_if_missing(self, tmp_path: pathlib.Path) -> None:
        save_memory_file('shaun', 'shopping', '# Shopping\n')
        assert (tmp_path / 'users' / 'shaun' / 'memory' / 'shopping.md').exists()

    def test_overwrites_existing(self) -> None:
        save_memory_file('shaun', 'family', '# Family v1\n')
        save_memory_file('shaun', 'family', '# Family v2\n')
        assert load_memory_file('shaun', 'family') == '# Family v2\n'


class TestUpdateMemoryIndex:
    def test_creates_index_if_missing(self, tmp_path: pathlib.Path) -> None:
        update_memory_index('shaun', 'calendar', 'appointment preferences')
        idx = tmp_path / 'users' / 'shaun' / 'memory' / 'index.md'
        assert idx.exists()

    def test_adds_entry(self) -> None:
        update_memory_index('shaun', 'calendar', 'appointment preferences')
        content = load_memory_index('shaun')
        assert 'calendar.md' in content
        assert 'appointment preferences' in content

    def test_does_not_duplicate_existing_entry(self) -> None:
        update_memory_index('shaun', 'calendar', 'first description')
        update_memory_index('shaun', 'calendar', 'second description')
        content = load_memory_index('shaun')
        # Each entry is one line; only one line should be present for 'calendar'.
        calendar_lines = [ln for ln in content.splitlines() if 'calendar.md' in ln]
        assert len(calendar_lines) == 1

    def test_adds_multiple_different_topics(self) -> None:
        update_memory_index('shaun', 'calendar', 'calendar facts')
        update_memory_index('shaun', 'family', 'family members')
        content = load_memory_index('shaun')
        assert 'calendar.md' in content
        assert 'family.md' in content
