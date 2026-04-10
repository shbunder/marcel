"""Tests for the MARCEL.md loader — multi-location discovery and concatenation."""

from __future__ import annotations

from marcel_core.agent.marcelmd import (
    format_marcelmd_for_prompt,
    load_marcelmd_files,
)


class TestLoadMarcelmdFiles:
    def _patch(self, monkeypatch, tmp_path):
        """Helper: patch data root to use tmp dir."""
        import marcel_core.storage._root as root_mod

        monkeypatch.setattr(root_mod, '_DATA_ROOT', tmp_path)

    def test_empty_when_no_files_exist(self, tmp_path, monkeypatch):
        self._patch(monkeypatch, tmp_path)
        assert load_marcelmd_files('alice') == []

    def test_global_file_loaded(self, tmp_path, monkeypatch):
        (tmp_path / 'MARCEL.md').write_text('Global instructions.')
        self._patch(monkeypatch, tmp_path)
        files = load_marcelmd_files('alice')
        assert len(files) == 1
        assert files[0] == ('global', 'Global instructions.')

    def test_user_file_loaded(self, tmp_path, monkeypatch):
        user_dir = tmp_path / 'users' / 'alice'
        user_dir.mkdir(parents=True)
        (user_dir / 'MARCEL.md').write_text('Alice instructions.')
        self._patch(monkeypatch, tmp_path)
        files = load_marcelmd_files('alice')
        assert len(files) == 1
        assert files[0] == ('user', 'Alice instructions.')

    def test_loading_order_global_then_user(self, tmp_path, monkeypatch):
        """Global < user, in that loading order."""
        (tmp_path / 'MARCEL.md').write_text('Global content.')
        user_dir = tmp_path / 'users' / 'alice'
        user_dir.mkdir(parents=True)
        (user_dir / 'MARCEL.md').write_text('User content.')
        self._patch(monkeypatch, tmp_path)
        files = load_marcelmd_files('alice')
        assert len(files) == 2
        assert files[0] == ('global', 'Global content.')
        assert files[1] == ('user', 'User content.')

    def test_empty_files_skipped(self, tmp_path, monkeypatch):
        user_dir = tmp_path / 'users' / 'alice'
        user_dir.mkdir(parents=True)
        (user_dir / 'MARCEL.md').write_text('   \n  ')  # whitespace only
        self._patch(monkeypatch, tmp_path)
        assert load_marcelmd_files('alice') == []

    def test_different_users_get_different_instructions(self, tmp_path, monkeypatch):
        for name in ('alice', 'bob'):
            d = tmp_path / 'users' / name
            d.mkdir(parents=True)
            (d / 'MARCEL.md').write_text(f'{name.capitalize()} instructions.')
        self._patch(monkeypatch, tmp_path)
        alice_files = load_marcelmd_files('alice')
        bob_files = load_marcelmd_files('bob')
        assert alice_files[0][1] == 'Alice instructions.'
        assert bob_files[0][1] == 'Bob instructions.'

    def test_missing_user_dir_still_loads_global(self, tmp_path, monkeypatch):
        (tmp_path / 'MARCEL.md').write_text('Global rules.')
        self._patch(monkeypatch, tmp_path)
        files = load_marcelmd_files('nonexistent-user')
        assert len(files) == 1
        assert files[0][1] == 'Global rules.'


class TestFormatMarcelmdForPrompt:
    def test_empty_list_returns_empty_string(self):
        assert format_marcelmd_for_prompt([]) == ''

    def test_single_file(self):
        result = format_marcelmd_for_prompt([('user', 'Instructions here.')])
        assert result == 'Instructions here.'

    def test_multiple_files_separated_by_hr(self):
        files = [
            ('global', 'Global rules.'),
            ('user', 'User override.'),
        ]
        result = format_marcelmd_for_prompt(files)
        assert 'Global rules.' in result
        assert 'User override.' in result
        assert '---' in result
