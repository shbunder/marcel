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


class TestBrowserToWebMigration:
    """ISSUE-072: rename `browser` skill to `web`, remove stale browser/ once."""

    def test_stale_browser_dir_removed_when_web_missing(self, tmp_path):
        defaults_dir = tmp_path / 'defaults'
        defaults_dir.mkdir()
        web_src = defaults_dir / 'skills' / 'web'
        web_src.mkdir(parents=True)
        (web_src / 'SKILL.md').write_text('# New Web Skill')

        data_root = tmp_path / 'data'
        stale_browser = data_root / 'skills' / 'browser'
        stale_browser.mkdir(parents=True)
        (stale_browser / 'SKILL.md').write_text('# Old Browser Skill')

        with patch('marcel_core.defaults._DEFAULTS_DIR', defaults_dir):
            seed_defaults(data_root)

        assert not stale_browser.exists(), 'stale browser/ should have been removed'
        assert (data_root / 'skills' / 'web' / 'SKILL.md').exists()
        assert (data_root / 'skills' / 'web' / 'SKILL.md').read_text() == '# New Web Skill'

    def test_migration_noop_when_web_already_exists(self, tmp_path):
        """If web/ is already seeded, the migration should not touch anything."""
        defaults_dir = tmp_path / 'defaults'
        defaults_dir.mkdir()
        web_src = defaults_dir / 'skills' / 'web'
        web_src.mkdir(parents=True)
        (web_src / 'SKILL.md').write_text('# New Web Skill')

        data_root = tmp_path / 'data'
        existing_web = data_root / 'skills' / 'web'
        existing_web.mkdir(parents=True)
        (existing_web / 'SKILL.md').write_text('# Custom Web Skill')
        # Browser is present but migration should NOT delete it (web already exists)
        existing_browser = data_root / 'skills' / 'browser'
        existing_browser.mkdir(parents=True)
        (existing_browser / 'SKILL.md').write_text('# Stale but left alone')

        with patch('marcel_core.defaults._DEFAULTS_DIR', defaults_dir):
            seed_defaults(data_root)

        assert existing_browser.exists(), 'migration is a no-op when web/ already exists'
        assert existing_web.exists()
        assert (existing_web / 'SKILL.md').read_text() == '# Custom Web Skill'

    def test_migration_noop_when_no_browser_dir(self, tmp_path):
        """Fresh installs (no browser/) should seed web/ normally."""
        defaults_dir = tmp_path / 'defaults'
        defaults_dir.mkdir()
        web_src = defaults_dir / 'skills' / 'web'
        web_src.mkdir(parents=True)
        (web_src / 'SKILL.md').write_text('# New Web Skill')

        data_root = tmp_path / 'data'
        data_root.mkdir()

        with patch('marcel_core.defaults._DEFAULTS_DIR', defaults_dir):
            seed_defaults(data_root)

        assert (data_root / 'skills' / 'web' / 'SKILL.md').exists()
        assert not (data_root / 'skills' / 'browser').exists()
