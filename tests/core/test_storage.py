"""Tests for ISSUE-002: flat-file storage layer.

All tests use ``tmp_path`` to point the storage module at an isolated
temporary directory so no real data is read or written.
"""

import datetime
import pathlib
import time

import pytest

import marcel_core.storage._root as _root_mod
from marcel_core.storage import (
    MemoryHeader,
    MemoryType,
    append_turn,
    enforce_index_cap,
    format_memory_manifest,
    load_conversation,
    load_conversation_index,
    load_memory_file,
    load_memory_index,
    load_user_profile,
    memory_age_days,
    memory_freshness_note,
    new_conversation,
    parse_frontmatter,
    prune_expired_memories,
    save_memory_file,
    save_user_profile,
    scan_memory_headers,
    search_memory_files,
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


# ---------------------------------------------------------------------------
# frontmatter parsing
# ---------------------------------------------------------------------------


class TestParseFrontmatter:
    def test_parses_basic_frontmatter(self) -> None:
        text = '---\nname: dentist\ntype: schedule\n---\nBody text.'
        meta, body = parse_frontmatter(text)
        assert meta['name'] == 'dentist'
        assert meta['type'] == 'schedule'
        assert body.strip() == 'Body text.'

    def test_no_frontmatter_returns_empty_dict(self) -> None:
        text = 'Just some text without frontmatter.'
        meta, body = parse_frontmatter(text)
        assert meta == {}
        assert body == text

    def test_frontmatter_with_all_fields(self) -> None:
        text = (
            '---\n'
            'name: morning_routine\n'
            'description: Prefers mornings for meetings\n'
            'type: preference\n'
            'expires: 2026-12-31\n'
            'confidence: told\n'
            '---\n'
            'Content here.'
        )
        meta, body = parse_frontmatter(text)
        assert meta['name'] == 'morning_routine'
        assert meta['description'] == 'Prefers mornings for meetings'
        assert meta['type'] == 'preference'
        assert meta['expires'] == '2026-12-31'
        assert meta['confidence'] == 'told'
        assert body.strip() == 'Content here.'

    def test_body_preserved_after_frontmatter(self) -> None:
        text = '---\nname: test\n---\nLine 1\nLine 2\n'
        _, body = parse_frontmatter(text)
        assert 'Line 1' in body
        assert 'Line 2' in body


class TestMemoryType:
    def test_valid_types(self) -> None:
        assert MemoryType('schedule') == MemoryType.SCHEDULE
        assert MemoryType('preference') == MemoryType.PREFERENCE
        assert MemoryType('person') == MemoryType.PERSON
        assert MemoryType('reference') == MemoryType.REFERENCE
        assert MemoryType('household') == MemoryType.HOUSEHOLD

    def test_invalid_type_raises(self) -> None:
        with pytest.raises(ValueError):
            MemoryType('invalid_type')


# ---------------------------------------------------------------------------
# memory scanning
# ---------------------------------------------------------------------------


class TestScanMemoryHeaders:
    def test_empty_when_no_memory_dir(self) -> None:
        assert scan_memory_headers('nobody') == []

    def test_scans_files_with_frontmatter(self, tmp_path: pathlib.Path) -> None:
        mem_dir = tmp_path / 'users' / 'shaun' / 'memory'
        mem_dir.mkdir(parents=True)
        (mem_dir / 'calendar.md').write_text(
            '---\nname: calendar\ndescription: Calendar facts\ntype: schedule\n---\nContent.',
            encoding='utf-8',
        )
        headers = scan_memory_headers('shaun')
        assert len(headers) == 1
        assert headers[0].filename == 'calendar.md'
        assert headers[0].name == 'calendar'
        assert headers[0].description == 'Calendar facts'
        assert headers[0].type == MemoryType.SCHEDULE

    def test_scans_files_without_frontmatter(self, tmp_path: pathlib.Path) -> None:
        mem_dir = tmp_path / 'users' / 'shaun' / 'memory'
        mem_dir.mkdir(parents=True)
        (mem_dir / 'notes.md').write_text('Plain text notes.', encoding='utf-8')
        headers = scan_memory_headers('shaun')
        assert len(headers) == 1
        assert headers[0].name is None
        assert headers[0].type is None

    def test_excludes_index_md(self, tmp_path: pathlib.Path) -> None:
        mem_dir = tmp_path / 'users' / 'shaun' / 'memory'
        mem_dir.mkdir(parents=True)
        (mem_dir / 'index.md').write_text('Index content.', encoding='utf-8')
        (mem_dir / 'real.md').write_text('Real content.', encoding='utf-8')
        headers = scan_memory_headers('shaun')
        assert len(headers) == 1
        assert headers[0].filename == 'real.md'

    def test_sorted_newest_first(self, tmp_path: pathlib.Path) -> None:
        import os

        mem_dir = tmp_path / 'users' / 'shaun' / 'memory'
        mem_dir.mkdir(parents=True)
        (mem_dir / 'old.md').write_text('Old.', encoding='utf-8')
        os.utime(mem_dir / 'old.md', (1000, 1000))
        (mem_dir / 'new.md').write_text('New.', encoding='utf-8')
        headers = scan_memory_headers('shaun')
        assert headers[0].filename == 'new.md'
        assert headers[1].filename == 'old.md'


class TestFormatMemoryManifest:
    def test_formats_with_type_and_description(self) -> None:
        headers = [
            MemoryHeader(
                filename='calendar.md',
                filepath=pathlib.Path('/fake/calendar.md'),
                mtime=time.time(),
                name='calendar',
                description='Calendar facts',
                type=MemoryType.SCHEDULE,
            ),
        ]
        result = format_memory_manifest(headers)
        assert '[schedule]' in result
        assert 'calendar.md' in result
        assert 'Calendar facts' in result
        assert 'today' in result

    def test_formats_without_type(self) -> None:
        headers = [
            MemoryHeader(
                filename='notes.md',
                filepath=pathlib.Path('/fake/notes.md'),
                mtime=time.time(),
            ),
        ]
        result = format_memory_manifest(headers)
        assert 'notes.md' in result
        assert '[' not in result.split('notes.md')[0]  # no type tag


# ---------------------------------------------------------------------------
# staleness helpers
# ---------------------------------------------------------------------------


class TestMemoryStaleness:
    def test_age_days_today(self) -> None:
        assert memory_age_days(time.time()) == 0

    def test_age_days_yesterday(self) -> None:
        assert memory_age_days(time.time() - 86_400) == 1

    def test_age_days_old(self) -> None:
        assert memory_age_days(time.time() - 86_400 * 30) == 30

    def test_freshness_note_empty_for_today(self) -> None:
        assert memory_freshness_note(time.time()) == ''

    def test_freshness_note_empty_for_yesterday(self) -> None:
        assert memory_freshness_note(time.time() - 86_400) == ''

    def test_freshness_note_for_old(self) -> None:
        note = memory_freshness_note(time.time() - 86_400 * 10)
        assert '10 days old' in note

    def test_freshness_note_very_old(self) -> None:
        note = memory_freshness_note(time.time() - 86_400 * 100)
        assert 'very outdated' in note


# ---------------------------------------------------------------------------
# memory search
# ---------------------------------------------------------------------------


class TestSearchMemoryFiles:
    def test_empty_when_no_memory_dir(self) -> None:
        assert search_memory_files('nobody', 'anything') == []

    def test_finds_match_in_body(self, tmp_path: pathlib.Path) -> None:
        save_memory_file('shaun', 'dentist', '---\nname: dentist\ntype: schedule\n---\nDentist on Friday at 3pm.')
        results = search_memory_files('shaun', 'dentist')
        assert len(results) == 1
        assert results[0].filename == 'dentist.md'
        assert results[0].type == MemoryType.SCHEDULE
        assert 'Friday' in results[0].snippet

    def test_finds_match_in_description(self, tmp_path: pathlib.Path) -> None:
        save_memory_file(
            'shaun',
            'prefs',
            '---\nname: prefs\ndescription: Morning coffee routine\ntype: preference\n---\nDrinks espresso.',
        )
        results = search_memory_files('shaun', 'coffee')
        assert len(results) == 1
        assert results[0].filename == 'prefs.md'

    def test_case_insensitive(self, tmp_path: pathlib.Path) -> None:
        save_memory_file('shaun', 'note', 'Remember the WiFi password.')
        results = search_memory_files('shaun', 'wifi')
        assert len(results) == 1

    def test_type_filter(self, tmp_path: pathlib.Path) -> None:
        save_memory_file('shaun', 'cal', '---\ntype: schedule\n---\nMeeting Monday.')
        save_memory_file('shaun', 'pref', '---\ntype: preference\n---\nLikes Monday mornings.')
        results = search_memory_files('shaun', 'monday', type_filter=MemoryType.SCHEDULE)
        assert len(results) == 1
        assert results[0].filename == 'cal.md'

    def test_includes_household(self, tmp_path: pathlib.Path) -> None:
        save_memory_file('shaun', 'personal', 'My wifi is fast.')
        save_memory_file('_household', 'wifi', '---\ntype: household\n---\nPassword: secret123.')
        results = search_memory_files('shaun', 'wifi')
        assert len(results) == 2

    def test_excludes_household_when_disabled(self, tmp_path: pathlib.Path) -> None:
        save_memory_file('shaun', 'personal', 'My wifi is fast.')
        save_memory_file('_household', 'wifi', 'Password: secret123.')
        results = search_memory_files('shaun', 'wifi', include_household=False)
        assert len(results) == 1
        assert results[0].filename == 'personal.md'

    def test_max_results(self, tmp_path: pathlib.Path) -> None:
        for i in range(5):
            save_memory_file('shaun', f'note{i}', f'Keyword match {i}.')
        results = search_memory_files('shaun', 'keyword', max_results=3)
        assert len(results) == 3

    def test_no_match_returns_empty(self, tmp_path: pathlib.Path) -> None:
        save_memory_file('shaun', 'note', 'Nothing relevant here.')
        results = search_memory_files('shaun', 'xyz123nonexistent')
        assert results == []

    def test_meta_matches_ranked_before_body(self, tmp_path: pathlib.Path) -> None:
        save_memory_file('shaun', 'body_match', 'The dentist appointment is on Friday.')
        save_memory_file('shaun', 'meta_match', '---\nname: dentist\ndescription: Dentist visit\n---\nSome body.')
        results = search_memory_files('shaun', 'dentist')
        assert len(results) == 2
        # Meta match should come first.
        assert results[0].filename == 'meta_match.md'
        assert results[1].filename == 'body_match.md'

    def test_excludes_index_md(self, tmp_path: pathlib.Path) -> None:
        mem_dir = tmp_path / 'users' / 'shaun' / 'memory'
        mem_dir.mkdir(parents=True, exist_ok=True)
        (mem_dir / 'index.md').write_text('keyword in index', encoding='utf-8')
        save_memory_file('shaun', 'real', 'keyword in real file.')
        results = search_memory_files('shaun', 'keyword')
        assert len(results) == 1
        assert results[0].filename == 'real.md'


# ---------------------------------------------------------------------------
# memory lifecycle — expiry and index cap
# ---------------------------------------------------------------------------


class TestPruneExpiredMemories:
    def test_prunes_expired_schedule(self, tmp_path: pathlib.Path) -> None:
        save_memory_file(
            'shaun',
            'old_dentist',
            '---\nname: old_dentist\ntype: schedule\nexpires: 2026-03-01\n---\nDentist March 1.',
        )
        pruned = prune_expired_memories('shaun', today=datetime.date(2026, 4, 2))
        assert 'old_dentist.md' in pruned
        assert load_memory_file('shaun', 'old_dentist') == ''

    def test_keeps_future_schedule(self, tmp_path: pathlib.Path) -> None:
        save_memory_file(
            'shaun',
            'future_dentist',
            '---\nname: future_dentist\ntype: schedule\nexpires: 2026-12-31\n---\nDentist December.',
        )
        pruned = prune_expired_memories('shaun', today=datetime.date(2026, 4, 2))
        assert pruned == []
        assert 'Dentist December.' in load_memory_file('shaun', 'future_dentist')

    def test_ignores_non_schedule_with_expires(self, tmp_path: pathlib.Path) -> None:
        save_memory_file(
            'shaun',
            'pref',
            '---\nname: pref\ntype: preference\nexpires: 2020-01-01\n---\nOld pref.',
        )
        pruned = prune_expired_memories('shaun', today=datetime.date(2026, 4, 2))
        assert pruned == []

    def test_ignores_schedule_without_expires(self, tmp_path: pathlib.Path) -> None:
        save_memory_file(
            'shaun',
            'undated',
            '---\nname: undated\ntype: schedule\n---\nNo expiry.',
        )
        pruned = prune_expired_memories('shaun', today=datetime.date(2026, 4, 2))
        assert pruned == []

    def test_returns_empty_for_no_memory_dir(self) -> None:
        assert prune_expired_memories('nobody') == []

    def test_prunes_multiple(self, tmp_path: pathlib.Path) -> None:
        save_memory_file('shaun', 'a', '---\ntype: schedule\nexpires: 2026-01-01\n---\nOld A.')
        save_memory_file('shaun', 'b', '---\ntype: schedule\nexpires: 2026-02-01\n---\nOld B.')
        save_memory_file('shaun', 'c', '---\ntype: schedule\nexpires: 2026-12-01\n---\nFuture C.')
        pruned = prune_expired_memories('shaun', today=datetime.date(2026, 4, 2))
        assert len(pruned) == 2
        assert 'a.md' in pruned
        assert 'b.md' in pruned
        assert load_memory_file('shaun', 'c') != ''


class TestEnforceIndexCap:
    def test_no_truncation_under_cap(self, tmp_path: pathlib.Path) -> None:
        update_memory_index('shaun', 'topic1', 'description 1')
        update_memory_index('shaun', 'topic2', 'description 2')
        assert enforce_index_cap('shaun', max_lines=10) is False

    def test_truncates_at_cap(self, tmp_path: pathlib.Path) -> None:
        # Write 20 lines to the index.
        mem_dir = tmp_path / 'users' / 'shaun' / 'memory'
        mem_dir.mkdir(parents=True, exist_ok=True)
        lines = [f'- [topic{i}.md](topic{i}.md) — description {i}\n' for i in range(20)]
        (mem_dir / 'index.md').write_text(''.join(lines), encoding='utf-8')

        assert enforce_index_cap('shaun', max_lines=10) is True
        content = load_memory_index('shaun')
        # Should have 10 original lines + truncation warning.
        assert 'topic0.md' in content
        assert 'topic9.md' in content
        assert 'topic10.md' not in content
        assert 'truncated' in content.lower()

    def test_returns_false_when_no_index(self) -> None:
        assert enforce_index_cap('nobody') is False

    def test_exactly_at_cap_no_truncation(self, tmp_path: pathlib.Path) -> None:
        mem_dir = tmp_path / 'users' / 'shaun' / 'memory'
        mem_dir.mkdir(parents=True, exist_ok=True)
        lines = [f'- [topic{i}.md](topic{i}.md) — desc {i}\n' for i in range(5)]
        (mem_dir / 'index.md').write_text(''.join(lines), encoding='utf-8')
        assert enforce_index_cap('shaun', max_lines=5) is False
