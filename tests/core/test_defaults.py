"""Scenario-based tests for defaults/__init__.py — seed_defaults.

Tests that bundled defaults are copied on first startup and that
existing files are not overwritten.
"""

from __future__ import annotations

from unittest.mock import patch

from marcel_core.defaults import seed_defaults


class TestSeedDefaults:
    def test_seeds_marcel_md(self, tmp_path):
        defaults_dir = tmp_path / 'defaults'
        defaults_dir.mkdir()
        (defaults_dir / 'MARCEL.md').write_text('# Default Marcel')

        data_root = tmp_path / 'data'
        data_root.mkdir()

        with patch('marcel_core.defaults._DEFAULTS_DIR', defaults_dir):
            seed_defaults(data_root)

        assert (data_root / 'MARCEL.md').exists()
        assert (data_root / 'MARCEL.md').read_text() == '# Default Marcel'

    def test_does_not_overwrite_existing_marcel_md(self, tmp_path):
        defaults_dir = tmp_path / 'defaults'
        defaults_dir.mkdir()
        (defaults_dir / 'MARCEL.md').write_text('# Default')

        data_root = tmp_path / 'data'
        data_root.mkdir()
        (data_root / 'MARCEL.md').write_text('# Custom')

        with patch('marcel_core.defaults._DEFAULTS_DIR', defaults_dir):
            seed_defaults(data_root)

        assert (data_root / 'MARCEL.md').read_text() == '# Custom'

    def test_seeds_skills(self, tmp_path):
        defaults_dir = tmp_path / 'defaults'
        defaults_dir.mkdir()
        skill_dir = defaults_dir / 'skills' / 'banking'
        skill_dir.mkdir(parents=True)
        (skill_dir / 'SKILL.md').write_text('# Banking Skill')

        data_root = tmp_path / 'data'
        data_root.mkdir()

        with patch('marcel_core.defaults._DEFAULTS_DIR', defaults_dir):
            seed_defaults(data_root)

        assert (data_root / 'skills' / 'banking' / 'SKILL.md').exists()

    def test_does_not_overwrite_existing_skills(self, tmp_path):
        defaults_dir = tmp_path / 'defaults'
        defaults_dir.mkdir()
        skill_dir = defaults_dir / 'skills' / 'banking'
        skill_dir.mkdir(parents=True)
        (skill_dir / 'SKILL.md').write_text('# Default')

        data_root = tmp_path / 'data'
        (data_root / 'skills' / 'banking').mkdir(parents=True)
        (data_root / 'skills' / 'banking' / 'SKILL.md').write_text('# Custom')

        with patch('marcel_core.defaults._DEFAULTS_DIR', defaults_dir):
            seed_defaults(data_root)

        assert (data_root / 'skills' / 'banking' / 'SKILL.md').read_text() == '# Custom'

    def test_skips_hidden_and_underscore_dirs(self, tmp_path):
        defaults_dir = tmp_path / 'defaults'
        defaults_dir.mkdir()
        (defaults_dir / 'skills').mkdir()
        (defaults_dir / 'skills' / '__pycache__').mkdir()
        (defaults_dir / 'skills' / '.hidden').mkdir()
        (defaults_dir / 'skills' / '_internal').mkdir()

        data_root = tmp_path / 'data'
        data_root.mkdir()

        with patch('marcel_core.defaults._DEFAULTS_DIR', defaults_dir):
            seed_defaults(data_root)

        assert not (data_root / 'skills' / '__pycache__').exists()
        assert not (data_root / 'skills' / '.hidden').exists()
        assert not (data_root / 'skills' / '_internal').exists()

    def test_seeds_channel_prompts(self, tmp_path):
        defaults_dir = tmp_path / 'defaults'
        defaults_dir.mkdir()
        # Need a skills dir (even empty) so seed_defaults doesn't return early
        (defaults_dir / 'skills').mkdir()
        channels_dir = defaults_dir / 'channels'
        channels_dir.mkdir()
        (channels_dir / 'telegram.md').write_text('# Telegram prompt')

        data_root = tmp_path / 'data'
        data_root.mkdir()

        with patch('marcel_core.defaults._DEFAULTS_DIR', defaults_dir):
            seed_defaults(data_root)

        assert (data_root / 'channels' / 'telegram.md').exists()

    def test_does_not_overwrite_existing_channel_prompts(self, tmp_path):
        defaults_dir = tmp_path / 'defaults'
        defaults_dir.mkdir()
        (defaults_dir / 'skills').mkdir()  # needed so we reach the channels section
        channels_dir = defaults_dir / 'channels'
        channels_dir.mkdir()
        (channels_dir / 'telegram.md').write_text('# Default')

        data_root = tmp_path / 'data'
        (data_root / 'channels').mkdir(parents=True)
        (data_root / 'channels' / 'telegram.md').write_text('# Custom')

        with patch('marcel_core.defaults._DEFAULTS_DIR', defaults_dir):
            seed_defaults(data_root)

        assert (data_root / 'channels' / 'telegram.md').read_text() == '# Custom'

    def test_no_skills_dir_in_defaults(self, tmp_path):
        """If defaults has no skills dir, seed_defaults does not crash."""
        defaults_dir = tmp_path / 'defaults'
        defaults_dir.mkdir()
        # No skills directory, no MARCEL.md, no channels

        data_root = tmp_path / 'data'
        data_root.mkdir()

        with patch('marcel_core.defaults._DEFAULTS_DIR', defaults_dir):
            seed_defaults(data_root)  # should not raise

    def test_skills_but_no_channels_dir(self, tmp_path):
        """If defaults has skills but no channels dir, seed_defaults does not crash."""
        defaults_dir = tmp_path / 'defaults'
        defaults_dir.mkdir()
        (defaults_dir / 'skills').mkdir()
        # No channels directory

        data_root = tmp_path / 'data'
        data_root.mkdir()

        with patch('marcel_core.defaults._DEFAULTS_DIR', defaults_dir):
            seed_defaults(data_root)  # should not raise
