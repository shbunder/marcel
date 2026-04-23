"""Tests for ISSUE-5f4d34 — ``Habitat`` Protocol + unified orchestrator.

Covers:

- Protocol compliance: every concrete wrapper satisfies the
  :class:`~marcel_core.plugin.habitat.Habitat` runtime-checkable Protocol.
- Per-kind ``discover_all(zoo_dir)`` behaviour on an empty and a populated
  temp zoo, matching the filesystem-walking wrappers and exercising the
  side-effecting ``discover()`` wrappers via stubs.
- Orchestrator ordering + failure isolation: a raise in one kind's
  wrapper must not prevent other kinds from being discovered; the broken
  kind's list becomes empty, everything else is populated.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from marcel_core.plugin.habitat import (
    ChannelHabitat,
    Habitat,
    JobHabitat,
    SkillHabitat,
    SubagentHabitat,
    ToolkitHabitat,
)
from marcel_core.plugin.orchestrator import discover_all_habitats

# ---------------------------------------------------------------------------
# Protocol compliance
# ---------------------------------------------------------------------------


class TestProtocolCompliance:
    def test_all_wrappers_structurally_satisfy_protocol(self):
        samples: list[ToolkitHabitat | ChannelHabitat | SkillHabitat | SubagentHabitat | JobHabitat] = [
            ToolkitHabitat(name='n', source='/p', provides=()),
            ChannelHabitat(name='n', source='/p', has_router=False),
            SkillHabitat(name='n', source='/p'),
            SubagentHabitat(name='n', source='/p'),
            JobHabitat(name='n', source='/p'),
        ]
        for sample in samples:
            assert isinstance(sample, Habitat)
            assert sample.name == 'n'
            assert sample.source == '/p'
            # Every wrapper exposes its kind as a class-level constant.
            assert sample.kind in {'toolkit', 'channel', 'skill', 'subagent', 'job'}

    def test_wrappers_are_frozen(self):
        """Habitats are value objects — no accidental mutation after discovery."""
        h = SkillHabitat(name='n', source='/p')
        with pytest.raises((AttributeError, TypeError)):
            h.name = 'other'  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Filesystem-backed wrappers — SkillHabitat, JobHabitat
# ---------------------------------------------------------------------------


def _seed_zoo(tmp_path: Path, subdir: str, names: list[str], *, with_template: bool = False) -> Path:
    zoo = tmp_path / 'zoo'
    target = zoo / subdir
    target.mkdir(parents=True, exist_ok=True)
    for name in names:
        (target / name).mkdir(parents=True, exist_ok=True)
        if with_template:
            (target / name / 'template.yaml').write_text('description: t\nsystem_prompt: s\nnotify: silent\nmodel: x\n')
    # Decoy dirs that must be excluded.
    (target / '_hidden').mkdir(exist_ok=True)
    (target / '.dotfile').mkdir(exist_ok=True)
    return zoo


class TestSkillHabitatDiscovery:
    def test_returns_empty_when_zoo_dir_is_none(self):
        assert SkillHabitat.discover_all(None) == []

    def test_returns_empty_when_skills_dir_absent(self, tmp_path):
        assert SkillHabitat.discover_all(tmp_path) == []

    def test_enumerates_on_disk_directories(self, tmp_path):
        zoo = _seed_zoo(tmp_path, 'skills', ['alpha', 'beta', 'gamma'])
        result = SkillHabitat.discover_all(zoo)
        names = [h.name for h in result]
        assert names == ['alpha', 'beta', 'gamma']  # sorted
        assert all(h.kind == 'skill' for h in result)
        # Underscore + dot-prefixed excluded.
        assert '_hidden' not in names
        assert '.dotfile' not in names


class TestJobHabitatDiscovery:
    def test_returns_empty_when_zoo_dir_is_none(self):
        assert JobHabitat.discover_all(None) == []

    def test_returns_empty_when_jobs_dir_absent(self, tmp_path):
        assert JobHabitat.discover_all(tmp_path) == []

    def test_only_lists_template_directories(self, tmp_path):
        zoo = _seed_zoo(tmp_path, 'jobs', ['sync', 'digest'], with_template=True)
        # An instance-style directory (no template.yaml) must be skipped.
        (zoo / 'jobs' / 'myslug').mkdir()
        result = JobHabitat.discover_all(zoo)
        names = [h.name for h in result]
        assert names == ['digest', 'sync']  # sorted; myslug skipped
        assert all(h.kind == 'job' for h in result)


# ---------------------------------------------------------------------------
# Side-effecting wrappers — ToolkitHabitat, ChannelHabitat
# ---------------------------------------------------------------------------


class _FakeMeta:
    def __init__(self, provides):
        self.provides = provides


class TestToolkitHabitatDiscovery:
    def test_returns_empty_when_zoo_dir_is_none(self, monkeypatch):
        # Stub discover so we don't touch any real zoo.
        monkeypatch.setattr('marcel_core.toolkit.discover', lambda: None)
        monkeypatch.setattr('marcel_core.toolkit._metadata', {})
        assert ToolkitHabitat.discover_all(None) == []

    def test_wraps_metadata_entries_under_toolkit_path(self, tmp_path, monkeypatch):
        zoo = tmp_path / 'zoo'
        (zoo / 'toolkit' / 'demo').mkdir(parents=True)

        fake_metadata = {'demo': _FakeMeta(['demo.ping', 'demo.pong'])}
        monkeypatch.setattr('marcel_core.toolkit.discover', lambda: None)
        monkeypatch.setattr('marcel_core.toolkit._metadata', fake_metadata)

        result = ToolkitHabitat.discover_all(zoo)
        assert len(result) == 1
        assert result[0].name == 'demo'
        assert result[0].source == str(zoo / 'toolkit' / 'demo')
        assert result[0].provides == ('demo.ping', 'demo.pong')
        assert result[0].kind == 'toolkit'

    def test_falls_back_to_integrations_path_when_toolkit_absent(self, tmp_path, monkeypatch):
        zoo = tmp_path / 'zoo'
        (zoo / 'integrations' / 'legacy').mkdir(parents=True)

        fake_metadata = {'legacy': _FakeMeta(['legacy.op'])}
        monkeypatch.setattr('marcel_core.toolkit.discover', lambda: None)
        monkeypatch.setattr('marcel_core.toolkit._metadata', fake_metadata)

        result = ToolkitHabitat.discover_all(zoo)
        assert len(result) == 1
        assert result[0].source == str(zoo / 'integrations' / 'legacy')


class _FakePlugin:
    def __init__(self, *, router=True):
        self.router = object() if router else None


class TestChannelHabitatDiscovery:
    def test_wraps_registered_channels(self, tmp_path, monkeypatch):
        monkeypatch.setattr('marcel_core.plugin.channels.discover', lambda: None)
        monkeypatch.setattr('marcel_core.plugin.channels.list_channels', lambda: ['telegram', 'signal'])
        plugins = {'telegram': _FakePlugin(router=True), 'signal': _FakePlugin(router=False)}
        monkeypatch.setattr('marcel_core.plugin.channels.get_channel', plugins.get)

        result = ChannelHabitat.discover_all(tmp_path / 'zoo')
        names = [h.name for h in result]
        has_router_by_name = {h.name: h.has_router for h in result}
        assert names == ['telegram', 'signal']
        assert has_router_by_name == {'telegram': True, 'signal': False}
        assert all(h.kind == 'channel' for h in result)

    def test_skips_channels_that_cannot_be_resolved(self, tmp_path, monkeypatch):
        monkeypatch.setattr('marcel_core.plugin.channels.discover', lambda: None)
        monkeypatch.setattr('marcel_core.plugin.channels.list_channels', lambda: ['ghost'])
        monkeypatch.setattr('marcel_core.plugin.channels.get_channel', lambda _name: None)

        assert ChannelHabitat.discover_all(tmp_path / 'zoo') == []


# ---------------------------------------------------------------------------
# SubagentHabitat — passes through load_agents
# ---------------------------------------------------------------------------


class TestSubagentHabitatDiscovery:
    def test_maps_agentdoc_list_to_habitats(self, monkeypatch):
        from types import SimpleNamespace

        fake = [SimpleNamespace(name='plan', source='data'), SimpleNamespace(name='explore', source='zoo')]
        monkeypatch.setattr('marcel_core.agents.loader.load_agents', lambda: fake)

        result = SubagentHabitat.discover_all(None)
        assert [(h.name, h.source, h.kind) for h in result] == [
            ('plan', 'data', 'subagent'),
            ('explore', 'zoo', 'subagent'),
        ]


# ---------------------------------------------------------------------------
# Orchestrator — ordering + failure isolation
# ---------------------------------------------------------------------------


class TestOrchestrator:
    def test_returns_five_kind_keys_even_when_empty(self):
        result = discover_all_habitats(None)
        # None zoo → skill/job/toolkit empty; subagents may have data-root
        # defaults, channels empty (nothing imported).
        assert set(result.keys()) == {'toolkit', 'channel', 'skill', 'subagent', 'job'}

    def test_broken_kind_isolated_from_others(self, tmp_path, monkeypatch, caplog):
        """A wrapper that raises must not prevent other kinds from discovering."""

        def boom(_zoo_dir):
            raise RuntimeError('simulated bad habitat loader')

        # Make the toolkit wrapper blow up mid-discovery.
        monkeypatch.setattr(
            'marcel_core.plugin.orchestrator.ToolkitHabitat.discover_all',
            classmethod(lambda cls, zoo_dir: boom(zoo_dir)),
        )
        # Plus seed a skill dir so we can observe a non-empty neighbour.
        zoo = _seed_zoo(tmp_path, 'skills', ['alpha'])

        with caplog.at_level('ERROR', logger='marcel_core.plugin.orchestrator'):
            result = discover_all_habitats(zoo)

        assert result['toolkit'] == []  # isolated failure
        assert [h.name for h in result['skill']] == ['alpha']  # still populated
        assert any('toolkit discovery failed' in r.message for r in caplog.records)

    def test_dispatch_order_is_fixed(self, monkeypatch):
        """Orchestrator calls toolkit before channel before skill/subagent/job."""
        call_order: list[str] = []

        def record(kind):
            def _stub(_zoo_dir):
                call_order.append(kind)
                return []

            return _stub

        monkeypatch.setattr(
            'marcel_core.plugin.orchestrator.ToolkitHabitat.discover_all',
            classmethod(lambda cls, zoo_dir: record('toolkit')(zoo_dir)),
        )
        monkeypatch.setattr(
            'marcel_core.plugin.orchestrator.ChannelHabitat.discover_all',
            classmethod(lambda cls, zoo_dir: record('channel')(zoo_dir)),
        )
        monkeypatch.setattr(
            'marcel_core.plugin.orchestrator.JobHabitat.discover_all',
            classmethod(lambda cls, zoo_dir: record('job')(zoo_dir)),
        )
        monkeypatch.setattr(
            'marcel_core.plugin.orchestrator.SubagentHabitat.discover_all',
            classmethod(lambda cls, zoo_dir: record('subagent')(zoo_dir)),
        )
        monkeypatch.setattr(
            'marcel_core.plugin.orchestrator.SkillHabitat.discover_all',
            classmethod(lambda cls, zoo_dir: record('skill')(zoo_dir)),
        )

        discover_all_habitats(None)

        # Toolkit MUST come first (metadata populated before scheduler);
        # channel second (router mount ordering).
        assert call_order[:2] == ['toolkit', 'channel']
        # All five kinds called exactly once.
        assert sorted(call_order) == sorted(['toolkit', 'channel', 'job', 'subagent', 'skill'])
