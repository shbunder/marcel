"""Tests for the MARCEL.md loader — multi-location discovery and concatenation."""

from __future__ import annotations

from marcel_core.agent.marcelmd import (
    format_marcelmd_for_prompt,
    load_marcelmd_files,
)


class TestLoadMarcelmdFiles:
    def test_empty_when_no_files_exist(self, tmp_path, monkeypatch):
        import marcel_core.agent.marcelmd as mod

        monkeypatch.setattr(mod, '_home_marcelmd', lambda: tmp_path / 'nonexistent.md')
        monkeypatch.setattr(mod, '_dirs_from_root_to_cwd', lambda: [tmp_path / 'no' / 'such' / 'dir'])
        result = load_marcelmd_files()
        assert result == []

    def test_home_file_loaded(self, tmp_path, monkeypatch):
        import marcel_core.agent.marcelmd as mod

        home_file = tmp_path / 'MARCEL.md'
        home_file.write_text('Home instructions.')
        monkeypatch.setattr(mod, '_home_marcelmd', lambda: home_file)
        monkeypatch.setattr(mod, '_dirs_from_root_to_cwd', lambda: [])
        files = load_marcelmd_files()
        assert len(files) == 1
        assert files[0] == ('user', 'Home instructions.')

    def test_project_file_loaded(self, tmp_path, monkeypatch):
        import marcel_core.agent.marcelmd as mod

        project_dir = tmp_path / 'project'
        project_dir.mkdir()
        (project_dir / 'MARCEL.md').write_text('Project instructions.')
        monkeypatch.setattr(mod, '_home_marcelmd', lambda: tmp_path / 'nonexistent.md')
        monkeypatch.setattr(mod, '_dirs_from_root_to_cwd', lambda: [project_dir])
        files = load_marcelmd_files()
        assert len(files) == 1
        assert files[0] == ('project', 'Project instructions.')

    def test_home_loaded_before_project(self, tmp_path, monkeypatch):
        """Home-level file comes first; project file comes after (higher priority)."""
        import marcel_core.agent.marcelmd as mod

        home_file = tmp_path / 'home.md'
        home_file.write_text('Home content.')
        project_dir = tmp_path / 'project'
        project_dir.mkdir()
        (project_dir / 'MARCEL.md').write_text('Project content.')
        monkeypatch.setattr(mod, '_home_marcelmd', lambda: home_file)
        monkeypatch.setattr(mod, '_dirs_from_root_to_cwd', lambda: [project_dir])
        files = load_marcelmd_files()
        assert len(files) == 2
        assert files[0][1] == 'Home content.'
        assert files[1][1] == 'Project content.'

    def test_dot_marcel_dir_also_scanned(self, tmp_path, monkeypatch):
        """Both MARCEL.md and .marcel/MARCEL.md at each directory level are loaded."""
        import marcel_core.agent.marcelmd as mod

        project_dir = tmp_path / 'project'
        project_dir.mkdir()
        (project_dir / 'MARCEL.md').write_text('Root level.')
        dotmarcel = project_dir / '.marcel'
        dotmarcel.mkdir()
        (dotmarcel / 'MARCEL.md').write_text('Dot-marcel level.')
        monkeypatch.setattr(mod, '_home_marcelmd', lambda: tmp_path / 'nonexistent.md')
        monkeypatch.setattr(mod, '_dirs_from_root_to_cwd', lambda: [project_dir])
        files = load_marcelmd_files()
        assert len(files) == 2
        contents = [c for _, c in files]
        assert 'Root level.' in contents
        assert 'Dot-marcel level.' in contents

    def test_empty_files_skipped(self, tmp_path, monkeypatch):
        import marcel_core.agent.marcelmd as mod

        home_file = tmp_path / 'MARCEL.md'
        home_file.write_text('   \n  ')  # whitespace only
        monkeypatch.setattr(mod, '_home_marcelmd', lambda: home_file)
        monkeypatch.setattr(mod, '_dirs_from_root_to_cwd', lambda: [])
        files = load_marcelmd_files()
        assert files == []

    def test_duplicate_files_deduplicated(self, tmp_path, monkeypatch):
        """The same resolved path is never loaded twice."""
        import marcel_core.agent.marcelmd as mod

        md_file = tmp_path / 'MARCEL.md'
        md_file.write_text('Content.')
        # Both home and project point to the same file
        monkeypatch.setattr(mod, '_home_marcelmd', lambda: md_file)
        monkeypatch.setattr(mod, '_dirs_from_root_to_cwd', lambda: [tmp_path])
        files = load_marcelmd_files()
        assert len(files) == 1  # loaded once despite being found via both paths

    def test_multiple_dirs_in_order(self, tmp_path, monkeypatch):
        """Files from multiple dirs are returned in dir order (root → CWD)."""
        import marcel_core.agent.marcelmd as mod

        dir_a = tmp_path / 'a'
        dir_b = tmp_path / 'b'
        dir_a.mkdir()
        dir_b.mkdir()
        (dir_a / 'MARCEL.md').write_text('From a.')
        (dir_b / 'MARCEL.md').write_text('From b.')
        monkeypatch.setattr(mod, '_home_marcelmd', lambda: tmp_path / 'nonexistent.md')
        monkeypatch.setattr(mod, '_dirs_from_root_to_cwd', lambda: [dir_a, dir_b])
        files = load_marcelmd_files()
        assert len(files) == 2
        assert files[0][1] == 'From a.'
        assert files[1][1] == 'From b.'


class TestFormatMarcelmdForPrompt:
    def test_empty_list_returns_empty_string(self):
        assert format_marcelmd_for_prompt([]) == ''

    def test_single_file(self):
        result = format_marcelmd_for_prompt([('user', 'Instructions here.')])
        assert result == 'Instructions here.'

    def test_multiple_files_separated_by_hr(self):
        files = [
            ('user', 'Base instructions.'),
            ('project', 'Project override.'),
        ]
        result = format_marcelmd_for_prompt(files)
        assert 'Base instructions.' in result
        assert 'Project override.' in result
        assert '---' in result
