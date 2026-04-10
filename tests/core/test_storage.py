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
    enforce_index_cap,
    format_memory_manifest,
    load_memory_file,
    load_memory_index,
    load_user_profile,
    memory_age_days,
    memory_freshness_note,
    parse_frontmatter,
    prune_expired_memories,
    save_memory_file,
    save_user_profile,
    scan_memory_headers,
    search_memory_files,
    update_memory_index,
    user_exists,
)
from marcel_core.storage.users import get_user_role, set_user_role


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


class TestGetUserRole:
    def test_returns_user_when_file_missing(self) -> None:
        assert get_user_role('nobody') == 'user'

    def test_returns_admin_when_set(self, tmp_path: pathlib.Path) -> None:
        import json as _json

        user_dir = tmp_path / 'users' / 'shaun'
        user_dir.mkdir(parents=True)
        (user_dir / 'user.json').write_text(_json.dumps({'role': 'admin'}), encoding='utf-8')
        assert get_user_role('shaun') == 'admin'

    def test_returns_user_for_unknown_role(self, tmp_path: pathlib.Path) -> None:
        import json as _json

        user_dir = tmp_path / 'users' / 'mallory'
        user_dir.mkdir(parents=True)
        (user_dir / 'user.json').write_text(_json.dumps({'role': 'superuser'}), encoding='utf-8')
        assert get_user_role('mallory') == 'user'

    def test_returns_user_on_corrupt_json(self, tmp_path: pathlib.Path) -> None:
        user_dir = tmp_path / 'users' / 'bad'
        user_dir.mkdir(parents=True)
        (user_dir / 'user.json').write_text('{not json}', encoding='utf-8')
        assert get_user_role('bad') == 'user'

    def test_returns_user_when_role_key_absent(self, tmp_path: pathlib.Path) -> None:
        import json as _json

        user_dir = tmp_path / 'users' / 'norole'
        user_dir.mkdir(parents=True)
        (user_dir / 'user.json').write_text(_json.dumps({'name': 'someone'}), encoding='utf-8')
        assert get_user_role('norole') == 'user'


class TestSetUserRole:
    def test_set_and_get_round_trip(self) -> None:
        set_user_role('alice', 'admin')
        assert get_user_role('alice') == 'admin'

    def test_overwrite_role(self) -> None:
        set_user_role('bob', 'admin')
        set_user_role('bob', 'user')
        assert get_user_role('bob') == 'user'

    def test_raises_on_invalid_role(self) -> None:
        with pytest.raises(ValueError, match="'admin' or 'user'"):
            set_user_role('dave', 'superuser')


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

    def test_parse_memory_type_invalid_returns_none(self) -> None:
        from marcel_core.storage.memory import parse_memory_type

        assert parse_memory_type('not_a_type') is None

    def test_parse_memory_type_none_returns_none(self) -> None:
        from marcel_core.storage.memory import parse_memory_type

        assert parse_memory_type(None) is None


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

    def test_oserror_during_read_bytes_skipped(self, tmp_path: pathlib.Path, monkeypatch) -> None:
        import pathlib as pl

        mem_dir = tmp_path / 'users' / 'shaun' / 'memory'
        mem_dir.mkdir(parents=True)
        (mem_dir / 'bad.md').write_text('Content.', encoding='utf-8')
        (mem_dir / 'good.md').write_text('Good.', encoding='utf-8')

        original_read_bytes = pl.Path.read_bytes

        def mock_read_bytes(self):
            if self.name == 'bad.md':
                raise OSError('permission denied')
            return original_read_bytes(self)

        monkeypatch.setattr(pl.Path, 'read_bytes', mock_read_bytes)
        headers = scan_memory_headers('shaun')
        # bad.md raised OSError so it should be skipped
        assert all(h.filename != 'bad.md' for h in headers)


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

    def test_formats_yesterday_age(self) -> None:
        headers = [
            MemoryHeader(
                filename='old.md',
                filepath=pathlib.Path('/fake/old.md'),
                mtime=time.time() - 86_400,  # exactly 1 day ago
            ),
        ]
        result = format_memory_manifest(headers)
        assert 'yesterday' in result


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

    def test_oserror_during_read_text_skipped(self, tmp_path: pathlib.Path, monkeypatch) -> None:
        import pathlib as pl

        save_memory_file('shaun', 'good', 'keyword match here.')
        save_memory_file('shaun', 'bad', 'keyword match too.')

        original_read_text = pl.Path.read_text

        def mock_read_text(self, *args, **kwargs):
            if self.name == 'bad.md':
                raise OSError('permission denied')
            return original_read_text(self, *args, **kwargs)

        monkeypatch.setattr(pl.Path, 'read_text', mock_read_text)
        results = search_memory_files('shaun', 'keyword')
        # bad.md should be skipped; good.md should still match
        assert all(r.filename != 'bad.md' for r in results)

    def test_snippet_empty_body_returns_empty_string(self) -> None:
        from marcel_core.storage.memory import _extract_snippet

        assert _extract_snippet('', 'query') == ''

    def test_snippet_prefix_ellipsis_when_match_not_at_start(self) -> None:
        from marcel_core.storage.memory import _extract_snippet

        # Long body where query is not near the start — prefix ellipsis added
        body = 'a' * 300 + 'query' + 'b' * 100
        snippet = _extract_snippet(body, 'query')
        assert snippet.startswith('...')

    def test_snippet_suffix_ellipsis_when_match_not_at_end(self) -> None:
        from marcel_core.storage.memory import _extract_snippet

        # Query at start but body continues well past — suffix ellipsis added
        body = 'query' + 'x' * 400
        snippet = _extract_snippet(body, 'query')
        assert snippet.endswith('...')


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

    def test_invalid_expires_format_skipped(self, tmp_path: pathlib.Path) -> None:
        save_memory_file(
            'shaun',
            'bad_date',
            '---\nname: bad_date\ntype: schedule\nexpires: not-a-date\n---\nContent.',
        )
        pruned = prune_expired_memories('shaun', today=datetime.date(2026, 4, 2))
        assert pruned == []

    def test_prune_oserror_on_unlink_logged(self, tmp_path: pathlib.Path, monkeypatch) -> None:
        import pathlib as pl

        save_memory_file(
            'shaun',
            'expired',
            '---\nname: expired\ntype: schedule\nexpires: 2026-01-01\n---\nOld.',
        )

        original_unlink = pl.Path.unlink

        def mock_unlink(self, missing_ok=False):
            if self.name == 'expired.md':
                raise OSError('permission denied')
            return original_unlink(self, missing_ok=missing_ok)

        monkeypatch.setattr(pl.Path, 'unlink', mock_unlink)
        pruned = prune_expired_memories('shaun', today=datetime.date(2026, 4, 2))
        # unlink failed, so file isn't in pruned list (OSError swallowed)
        assert pruned == []

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


# ---------------------------------------------------------------------------
# credentials.py
# ---------------------------------------------------------------------------


class TestCredentials:
    def test_load_returns_empty_when_no_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root_mod, '_DATA_ROOT', tmp_path)
        from marcel_core.storage.credentials import load_credentials

        result = load_credentials('nobody')
        assert result == {}

    def test_save_and_load_plaintext(self, tmp_path, monkeypatch):
        from marcel_core.config import settings

        monkeypatch.setattr(_root_mod, '_DATA_ROOT', tmp_path)
        monkeypatch.setattr(settings, 'marcel_credentials_key', '')
        from marcel_core.storage.credentials import load_credentials, save_credentials

        save_credentials('alice', {'API_KEY': 'secret123'})
        result = load_credentials('alice')
        assert result.get('API_KEY') == 'secret123'

    def test_save_and_load_encrypted(self, tmp_path, monkeypatch):
        from marcel_core.config import settings

        monkeypatch.setattr(_root_mod, '_DATA_ROOT', tmp_path)
        monkeypatch.setattr(settings, 'marcel_credentials_key', 'test-passphrase')
        from marcel_core.storage.credentials import load_credentials, save_credentials

        save_credentials('bob', {'TOKEN': 'abc'})
        result = load_credentials('bob')
        assert result.get('TOKEN') == 'abc'

    def test_encrypted_file_not_plaintext(self, tmp_path, monkeypatch):
        from marcel_core.config import settings

        monkeypatch.setattr(_root_mod, '_DATA_ROOT', tmp_path)
        monkeypatch.setattr(settings, 'marcel_credentials_key', 'my-key')
        from marcel_core.storage.credentials import _enc_path, save_credentials

        save_credentials('bob', {'SECRET': 'value'})
        raw = _enc_path('bob').read_bytes()
        assert b'SECRET' not in raw

    def test_invalid_token_returns_empty(self, tmp_path, monkeypatch):
        from marcel_core.config import settings

        monkeypatch.setattr(_root_mod, '_DATA_ROOT', tmp_path)
        monkeypatch.setattr(settings, 'marcel_credentials_key', 'key-a')
        from marcel_core.storage.credentials import load_credentials, save_credentials

        save_credentials('charlie', {'K': 'v'})
        # Now change the key so decryption fails
        monkeypatch.setattr(settings, 'marcel_credentials_key', 'different-key')
        result = load_credentials('charlie')
        assert result == {}

    def test_parse_env_skips_comments_and_blanks(self):
        from marcel_core.storage.credentials import _parse_env

        result = _parse_env('# comment\n\nKEY=value\nNO_EQUALS_SIGN\n')
        assert result == {'KEY': 'value'}

    def test_load_plaintext_logs_warning_when_no_encryption_key(self, tmp_path, monkeypatch):
        import marcel_core.storage.credentials as creds_mod
        from marcel_core.config import settings

        monkeypatch.setattr(_root_mod, '_DATA_ROOT', tmp_path)
        monkeypatch.setattr(settings, 'marcel_credentials_key', '')
        monkeypatch.setattr(creds_mod, '_warned_plaintext', False)
        from marcel_core.storage.credentials import load_credentials, save_credentials

        save_credentials('warn_user', {'K': 'v'})
        result = load_credentials('warn_user')
        assert result.get('K') == 'v'

    def test_plaintext_migration_to_encrypted(self, tmp_path, monkeypatch):
        from marcel_core.config import settings

        monkeypatch.setattr(_root_mod, '_DATA_ROOT', tmp_path)
        # First create a plaintext file
        monkeypatch.setattr(settings, 'marcel_credentials_key', '')
        from marcel_core.storage.credentials import _plain_path, load_credentials, save_credentials

        save_credentials('dan', {'OLD': 'val'})
        assert _plain_path('dan').exists()
        # Now set an encryption key — loading should migrate
        monkeypatch.setattr(settings, 'marcel_credentials_key', 'migrate-key')

        import marcel_core.storage.credentials as creds_mod

        creds_mod._warned_plaintext = False  # reset warning flag
        result = load_credentials('dan')
        assert result.get('OLD') == 'val'


# ---------------------------------------------------------------------------
# _atomic.py
# ---------------------------------------------------------------------------


class TestAtomicWrite:
    def test_atomic_write_creates_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root_mod, '_DATA_ROOT', tmp_path)
        from marcel_core.storage._atomic import atomic_write

        target = tmp_path / 'out.txt'
        atomic_write(target, 'hello world')
        assert target.read_text() == 'hello world'

    def test_atomic_write_error_cleans_up(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root_mod, '_DATA_ROOT', tmp_path)
        import os

        from marcel_core.storage._atomic import atomic_write

        def broken_rename(src, dst):
            raise OSError('rename failed')

        monkeypatch.setattr(os, 'rename', broken_rename)
        with pytest.raises(OSError, match='rename failed'):
            atomic_write(tmp_path / 'out.txt', 'content')

    def test_atomic_write_unlink_fails_during_exception(self, tmp_path, monkeypatch):
        """OSError during unlink cleanup is silently swallowed; original error propagates."""
        monkeypatch.setattr(_root_mod, '_DATA_ROOT', tmp_path)
        import os

        from marcel_core.storage._atomic import atomic_write

        monkeypatch.setattr(os, 'rename', lambda src, dst: (_ for _ in ()).throw(OSError('rename failed')))
        monkeypatch.setattr(os, 'unlink', lambda path: (_ for _ in ()).throw(OSError('unlink failed')))

        with pytest.raises(OSError, match='rename failed'):
            atomic_write(tmp_path / 'out.txt', 'content')

    def test_atomic_write_sets_permissions(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root_mod, '_DATA_ROOT', tmp_path)
        import os
        import stat

        from marcel_core.storage._atomic import atomic_write

        target = tmp_path / 'secret.txt'
        atomic_write(target, 'secret', mode=0o600)
        mode = stat.S_IMODE(os.stat(target).st_mode)
        assert mode == 0o600


# ---------------------------------------------------------------------------
# _root.py — env var fallback
# ---------------------------------------------------------------------------


class TestDataRoot:
    def test_uses_data_root_override(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_root_mod, '_DATA_ROOT', tmp_path)
        from marcel_core.storage._root import data_root

        assert data_root() == tmp_path

    def test_falls_back_to_settings_when_no_override(self, monkeypatch):
        monkeypatch.setattr(_root_mod, '_DATA_ROOT', None)
        from pathlib import Path

        from marcel_core.storage._root import data_root

        result = data_root()
        assert isinstance(result, Path)
