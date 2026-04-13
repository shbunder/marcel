"""Tests for agents/loader.py — AgentDoc parsing and discovery."""

from __future__ import annotations

from pathlib import Path

import pytest

from marcel_core.agents.loader import (
    AgentDoc,
    AgentNotFoundError,
    format_agent_index,
    load_agent,
    load_agents,
)
from marcel_core.config import settings


@pytest.fixture
def agents_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point ``settings.data_dir`` at a tmp data root and return the agents subdir."""
    monkeypatch.setattr(settings, 'marcel_data_dir', str(tmp_path))
    agents_dir = tmp_path / 'agents'
    agents_dir.mkdir()
    return agents_dir


def _write_agent(agents_root: Path, name: str, frontmatter: str, body: str = 'system prompt body') -> Path:
    path = agents_root / f'{name}.md'
    path.write_text(f'---\n{frontmatter}\n---\n\n{body}\n', encoding='utf-8')
    return path


class TestLoadAgentsEmpty:
    def test_returns_empty_when_dir_missing(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(settings, 'marcel_data_dir', str(tmp_path))
        # No agents/ subdir at all
        assert load_agents() == []

    def test_returns_empty_when_dir_empty(self, agents_root: Path) -> None:
        assert load_agents() == []


class TestLoadAgentsHappyPath:
    def test_parses_minimal_agent(self, agents_root: Path) -> None:
        _write_agent(
            agents_root,
            'explore',
            'name: explore\ndescription: Read-only explorer',
            body='You are the explore agent.',
        )
        agents = load_agents()
        assert len(agents) == 1
        doc = agents[0]
        assert isinstance(doc, AgentDoc)
        assert doc.name == 'explore'
        assert doc.description == 'Read-only explorer'
        assert doc.system_prompt == 'You are the explore agent.'
        # Defaults
        assert doc.model is None
        assert doc.tools is None
        assert doc.disallowed_tools == []
        assert doc.max_requests is None
        assert doc.timeout_seconds == 300

    def test_parses_all_frontmatter_fields(self, agents_root: Path) -> None:
        fm = (
            'name: plan\n'
            'description: Planner\n'
            'model: anthropic:claude-haiku-4-5-20251001\n'
            'tools: [read_file, web]\n'
            'disallowed_tools: [bash]\n'
            'max_requests: 15\n'
            'timeout_seconds: 120'
        )
        _write_agent(agents_root, 'plan', fm)
        doc = load_agent('plan')
        assert doc.model == 'anthropic:claude-haiku-4-5-20251001'
        assert doc.tools == ['read_file', 'web']
        assert doc.disallowed_tools == ['bash']
        assert doc.max_requests == 15
        assert doc.timeout_seconds == 120

    def test_model_inherit_maps_to_none(self, agents_root: Path) -> None:
        _write_agent(agents_root, 'inh', 'name: inh\ndescription: d\nmodel: inherit')
        assert load_agent('inh').model is None

    def test_camelcase_frontmatter_aliases(self, agents_root: Path) -> None:
        """Clawcode-style ``maxTurns`` / ``disallowedTools`` keys are accepted."""
        fm = 'name: legacy\ndescription: Compat shim\ndisallowedTools: [delete_job]\nmaxTurns: 8'
        _write_agent(agents_root, 'legacy', fm)
        doc = load_agent('legacy')
        assert doc.disallowed_tools == ['delete_job']
        assert doc.max_requests == 8

    def test_sorted_by_name(self, agents_root: Path) -> None:
        _write_agent(agents_root, 'zeta', 'name: zeta\ndescription: z')
        _write_agent(agents_root, 'alpha', 'name: alpha\ndescription: a')
        _write_agent(agents_root, 'mu', 'name: mu\ndescription: m')
        names = [a.name for a in load_agents()]
        assert names == ['alpha', 'mu', 'zeta']

    def test_ignores_hidden_and_underscore_files(self, agents_root: Path) -> None:
        _write_agent(agents_root, 'real', 'name: real\ndescription: d')
        (agents_root / '_draft.md').write_text('---\nname: draft\n---\n', encoding='utf-8')
        (agents_root / '.hidden.md').write_text('---\nname: hidden\n---\n', encoding='utf-8')
        (agents_root / 'notes.txt').write_text('not a markdown file', encoding='utf-8')
        names = [a.name for a in load_agents()]
        assert names == ['real']


class TestLoadAgentLookup:
    def test_raises_for_unknown_name(self, agents_root: Path) -> None:
        _write_agent(agents_root, 'explore', 'name: explore\ndescription: d')
        with pytest.raises(AgentNotFoundError):
            load_agent('nonexistent')

    def test_error_lists_available(self, agents_root: Path) -> None:
        _write_agent(agents_root, 'a', 'name: a\ndescription: d')
        _write_agent(agents_root, 'b', 'name: b\ndescription: d')
        with pytest.raises(AgentNotFoundError) as exc_info:
            load_agent('missing')
        msg = str(exc_info.value)
        assert 'a' in msg and 'b' in msg


class TestFormatAgentIndex:
    def test_empty(self) -> None:
        assert format_agent_index([]) == ''

    def test_renders_one_line_per_agent(self, agents_root: Path) -> None:
        _write_agent(agents_root, 'explore', 'name: explore\ndescription: Read-only explorer')
        _write_agent(agents_root, 'plan', 'name: plan\ndescription: Software architect')
        index = format_agent_index(load_agents())
        lines = index.split('\n')
        assert len(lines) == 2
        assert '**explore**' in lines[0]
        assert 'Read-only explorer' in lines[0]
        assert '**plan**' in lines[1]


class TestDefaultsSeeded:
    """Integration check: the bundled ``explore`` and ``plan`` agents parse cleanly."""

    def test_bundled_defaults_parse(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from marcel_core.defaults import seed_defaults

        seed_defaults(tmp_path)
        monkeypatch.setattr(settings, 'marcel_data_dir', str(tmp_path))
        agents = load_agents()
        names = {a.name for a in agents}
        assert 'explore' in names
        assert 'plan' in names
        for agent in agents:
            assert agent.description  # every default has a description
            assert agent.system_prompt  # body is non-empty
