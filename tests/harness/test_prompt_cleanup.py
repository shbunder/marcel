"""Tests for prompt-content stripping helpers and memory index formatting."""

from __future__ import annotations

import time

from marcel_core.harness.marcelmd import (
    _strip_channel_preamble,
    _strip_leading_h1,
    _strip_self_ref_blockquote,
    format_marcelmd_for_prompt,
)
from marcel_core.storage.memory import MemoryHeader, MemoryType, format_memory_index


class TestStripLeadingH1:
    def test_strips_simple_h1(self):
        assert _strip_leading_h1('# Title\n\nBody text.') == 'Body text.'

    def test_strips_h1_with_em_dash(self):
        assert _strip_leading_h1('# Marcel — Personal Assistant\n\nRest.') == 'Rest.'

    def test_keeps_body_if_no_h1(self):
        assert _strip_leading_h1('Already body.') == 'Already body.'

    def test_does_not_strip_h2(self):
        assert _strip_leading_h1('## Not a top header\n\nBody.') == '## Not a top header\n\nBody.'

    def test_only_strips_first(self):
        out = _strip_leading_h1('# First\n\n# Second\n\nBody.')
        assert out.startswith('# Second')

    def test_handles_leading_whitespace(self):
        assert _strip_leading_h1('\n\n# Title\n\nBody.') == 'Body.'


class TestStripSelfRefBlockquote:
    def test_strips_per_user_blockquote(self):
        body = (
            '> This file provides global rules. Per-user instructions live at users/<slug>/MARCEL.md.\n\nReal content.'
        )
        result = _strip_self_ref_blockquote(body)
        assert 'per-user instructions' not in result.lower()
        assert 'Real content.' in result

    def test_strips_multiline_blockquote(self):
        body = '> This file is for global config.\n> It is loaded first.\n\nReal content.'
        result = _strip_self_ref_blockquote(body)
        assert 'loaded first' not in result
        assert 'Real content.' in result

    def test_strips_blockquote_after_intro_paragraph(self):
        """Blockquote does not need to be at the very start of the body."""
        body = (
            'You are Marcel, a warm assistant.\n\n'
            '> This file provides global rules. Per-user instructions live at users/<slug>.\n\n'
            '## Role\nBe kind.'
        )
        result = _strip_self_ref_blockquote(body)
        assert 'You are Marcel' in result
        assert 'global rules' not in result
        assert '## Role' in result

    def test_keeps_unrelated_blockquote(self):
        body = '> A famous quote by someone.\n\nContent.'
        # Does not mention "this file" or "per-user instructions" — keep
        assert _strip_self_ref_blockquote(body) == body

    def test_no_blockquote_is_noop(self):
        assert _strip_self_ref_blockquote('Plain text.') == 'Plain text.'


class TestStripChannelPreamble:
    def test_strips_standard_form(self):
        body = 'You are responding via the telegram channel.\n\n## Formatting\nRules.'
        assert _strip_channel_preamble(body) == '## Formatting\nRules.'

    def test_strips_short_form(self):
        body = 'You are responding via Telegram.\n\nBody.'
        assert _strip_channel_preamble(body) == 'Body.'

    def test_case_insensitive(self):
        body = 'YOU ARE RESPONDING VIA TELEGRAM.\n\nBody.'
        assert _strip_channel_preamble(body) == 'Body.'

    def test_noop_when_absent(self):
        body = '## Direct start\nRules here.'
        assert _strip_channel_preamble(body) == body


class TestFormatMarcelmdForPrompt:
    def test_strips_h1_and_blockquote_at_load_time(self):
        raw = '# Marcel — Global\n\n> This file provides rules. Per-user instructions live at users/<slug>.\n\n## Role\nBe kind.'
        result = format_marcelmd_for_prompt([('global', raw)])
        assert '# Marcel — Global' not in result
        assert 'per-user instructions' not in result.lower()
        assert '## Role' in result
        assert 'Be kind.' in result

    def test_returns_empty_for_no_files(self):
        assert format_marcelmd_for_prompt([]) == ''

    def test_joins_multiple_files_with_hr(self):
        raw1 = '# Global\n\nRule 1.'
        raw2 = '# User\n\nRule 2.'
        result = format_marcelmd_for_prompt([('global', raw1), ('user', raw2)])
        assert 'Rule 1.' in result
        assert 'Rule 2.' in result
        assert '---' in result


class TestFormatMemoryIndex:
    def _make_header(
        self, name: str, description: str, age_days: int = 0, mem_type: MemoryType | None = None
    ) -> MemoryHeader:
        from pathlib import Path

        mtime = time.time() - age_days * 86400
        return MemoryHeader(
            filename=f'{name}.md',
            filepath=Path(f'/tmp/{name}.md'),
            mtime=mtime,
            name=name,
            description=description,
            type=mem_type,
        )

    def test_empty_headers_returns_empty_string(self):
        assert format_memory_index([]) == ''

    def test_basic_format(self):
        headers = [self._make_header('family', 'Family members')]
        result = format_memory_index(headers)
        assert result == '- **family** — Family members'

    def test_multiple_entries(self):
        headers = [
            self._make_header('family', 'Family members'),
            self._make_header('work', 'Work schedule'),
        ]
        result = format_memory_index(headers)
        assert '- **family** — Family members' in result
        assert '- **work** — Work schedule' in result

    def test_stale_marker_for_old_memories(self):
        headers = [self._make_header('old_stuff', 'Outdated info', age_days=8)]
        result = format_memory_index(headers)
        assert '_(stale: 8d)_' in result

    def test_no_stale_marker_for_fresh_memories(self):
        headers = [self._make_header('recent', 'Fresh info', age_days=1)]
        result = format_memory_index(headers)
        assert 'stale' not in result

    def test_falls_back_to_filename_when_no_name(self):
        from pathlib import Path

        h = MemoryHeader(
            filename='fallback.md',
            filepath=Path('/tmp/fallback.md'),
            mtime=time.time(),
            name=None,
            description=None,
        )
        result = format_memory_index([h])
        assert 'fallback' in result
