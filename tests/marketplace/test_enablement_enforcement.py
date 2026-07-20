"""Enablement enforcement (FEAT-260707-acb2b6) — the 'enabled' state, enforced.

Availability = role ∧ enablement ∧ configuration, filtered at the two loader
choke points. Scenarios run against real zoo fixtures and real capability
builds — structural invisibility is asserted on what the model would see.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from marcel_core.marketplace.enablement import disable, enable, enabled_for
from marcel_core.storage import _root


@pytest.fixture(autouse=True)
def _fake_api_keys(monkeypatch):
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'sk-ant-test-fake')


@pytest.fixture
def household(tmp_path, monkeypatch):
    """A zoo with a paired skill+connector, two users, isolated data root."""
    from marcel_core.config import settings

    zoo = tmp_path / 'zoo'
    skill = zoo / 'skills' / 'banking'
    skill.mkdir(parents=True)
    (skill / 'SKILL.md').write_text(
        '---\nname: banking\ndescription: Family finances.\n'
        'metadata:\n  marcel-connectors: banking\n---\n\nCall `transactions()`.\n'
    )
    park = zoo / 'connectors' / 'banking'
    park.mkdir(parents=True)
    (park / 'connector.yaml').write_text(
        'name: banking\ndescription: bank tools\n'
        'server: {transport: inprocess, module: server.py}\n'
        'auth: {mode: none, per_user: false}\n'
    )
    (park / 'server.py').write_text(
        'from fastmcp import FastMCP\nmcp = FastMCP("srv")\n\n'
        '@mcp.tool\ndef transactions() -> str:\n    """List transactions."""\n    return "[]"\n'
    )

    data = tmp_path / 'data'
    for slug, role in (('shaun', 'admin'), ('kiddo', 'user')):
        d = data / 'users' / slug
        d.mkdir(parents=True)
        (d / 'profile.md').write_text(f'---\nrole: {role}\n---\n')

    import marcel_core.connectors.toolset as toolset_mod

    monkeypatch.setattr(toolset_mod, '_REGISTRY', None)
    monkeypatch.setattr(settings, 'marcel_zoo_dir', str(zoo))
    monkeypatch.setattr(settings, 'marcel_data_dir', str(data))
    monkeypatch.setattr(_root, '_DATA_ROOT', data)
    return zoo, data


def _manifest(data: Path, content: dict) -> None:
    (data / 'enablement.yaml').write_text(yaml.safe_dump(content))


class TestEnabledFor:
    def test_absent_entry_and_literal_all_mean_everyone(self, household):
        _, data = household
        assert enabled_for('skills', 'banking', 'kiddo') is True
        _manifest(data, {'skills': {'banking': 'all'}})
        assert enabled_for('skills', 'banking', 'kiddo') is True

    def test_named_list_enforced(self, household):
        _, data = household
        _manifest(data, {'skills': {'banking': ['shaun']}})
        assert enabled_for('skills', 'banking', 'shaun') is True
        assert enabled_for('skills', 'banking', 'kiddo') is False

    def test_global_view_never_filtered(self, household):
        _, data = household
        _manifest(data, {'skills': {'banking': []}})
        assert enabled_for('skills', 'banking', None) is True

    def test_malformed_entry_fails_closed_admin_only(self, household, caplog):
        """NFR1: garbage entry → admin-only, loud log."""
        import logging

        _, data = household
        _manifest(data, {'skills': {'banking': {'weird': 'shape'}}})
        caplog.set_level(logging.WARNING, logger='marcel_core.marketplace.enablement')
        assert enabled_for('skills', 'banking', 'kiddo', 'user') is False
        assert enabled_for('skills', 'banking', 'shaun', 'admin') is True
        assert any('failing closed' in r.message for r in caplog.records)


class TestStructuralInvisibility:
    def test_scoped_out_user_sees_nothing_enabled_user_everything(self, household):
        """Feature scenario 1: bob (kiddo) sees no banking skill, connector or
        bundled tools; the enabled user's build is unchanged."""
        from marcel_core.composition import build_capabilities

        _, data = household
        _manifest(data, {'skills': {'banking': ['shaun']}, 'connectors': {'banking': ['shaun']}})

        kiddo_ids = {getattr(c, 'id', None) for c in build_capabilities(role='user', user_slug='kiddo')}
        shaun_ids = {getattr(c, 'id', None) for c in build_capabilities(role='admin', user_slug='shaun')}
        assert 'banking' not in kiddo_ids
        assert 'banking' in shaun_ids

    def test_unscoped_habitat_reaches_everyone(self, household):
        """Feature scenario 2: no manifest entry ⇒ all users (role permitting)."""
        from marcel_core.composition import build_capabilities

        kiddo_ids = {getattr(c, 'id', None) for c in build_capabilities(role='user', user_slug='kiddo')}
        assert 'banking' in kiddo_ids

    def test_composes_with_role_gating_as_intersection(self, household):
        """An admin-scoped connector enabled for a non-admin stays invisible
        to them — enablement never widens what role forbids."""
        from marcel_core.connectors.loader import load_connectors

        zoo, data = household
        park_yaml = zoo / 'connectors' / 'banking' / 'connector.yaml'
        park_yaml.write_text(park_yaml.read_text() + 'scope: admin\n')
        _manifest(data, {'connectors': {'banking': ['shaun', 'kiddo']}})

        assert 'banking' not in {d.name for d in load_connectors('kiddo', 'user')}
        assert 'banking' in {d.name for d in load_connectors('shaun', 'admin')}

    def test_effective_without_restart(self, household):
        """FR6: manifest edits apply on the next load — no restart, no cache."""
        from marcel_core.skills.loader import load_skills

        _, data = household
        assert 'banking' in {d.name for d in load_skills('kiddo', 'user')}
        _manifest(data, {'skills': {'banking': ['shaun']}})
        assert 'banking' not in {d.name for d in load_skills('kiddo', 'user')}


class TestJobsRespectEnablement:
    def test_scoped_job_names_disabled_habitat_distinctly(self, household):
        """Feature AC3 (reworked): the run user's resolution fails with the
        'installed but not enabled' message, not a generic unknown-name."""
        from marcel_core.jobs.scoping import JobScopingError, resolve_job_scoping

        _, data = household
        _manifest(data, {'connectors': {'banking': ['shaun']}})

        with pytest.raises(JobScopingError, match="installed but not enabled for 'kiddo'"):
            resolve_job_scoping('kiddo', [], ['banking'])
        assert resolve_job_scoping('shaun', [], ['banking']).connector_docs


class TestAdminSurface:
    def test_enable_disable_round_trip(self, household):
        _, data = household
        msg = disable('skills', 'banking', 'kiddo')
        assert 'disabled for kiddo' in msg
        manifest = yaml.safe_load((data / 'enablement.yaml').read_text())
        assert manifest['skills']['banking'] == ['shaun']  # explicit everyone-but list

        msg = enable('skills', 'banking', 'kiddo')
        assert 'enabled for kiddo' in msg
        manifest = yaml.safe_load((data / 'enablement.yaml').read_text())
        assert manifest['skills']['banking'] == ['kiddo', 'shaun']

    def test_enable_on_absent_entry_is_a_readable_noop(self, household):
        assert 'already enabled for everyone' in enable('skills', 'banking', 'kiddo')

    def test_cli_enable_disable(self, household, capsys):
        from marcel_core.marketplace.cli import main

        assert main(['disable', '--kind', 'skill', '--name', 'banking', '--user', 'kiddo']) == 0
        assert 'disabled for kiddo' in capsys.readouterr().out
        assert main(['enable', '--kind', 'skill', '--name', 'banking', '--user', 'kiddo']) == 0
        assert 'enabled for kiddo' in capsys.readouterr().out


class TestActivationGuidanceParity:
    """STORY-260707-986c30 (reworked): the parity is delivered by the Zoo v2
    architecture — prove it per surface, no silence, no stack traces."""

    def test_skill_with_unmet_credentials_serves_setup(self, household):
        """A skill whose per-user credentials are missing serves SETUP.md."""
        from marcel_core.skills.loader import load_skills

        zoo, _ = household
        skill = zoo / 'skills' / 'icloudish'
        skill.mkdir()
        (skill / 'SKILL.md').write_text(
            '---\nname: icloudish\ndescription: Calendar.\n'
            'metadata:\n  marcel-requires-credentials: ICLOUD_APP_PASSWORD\n---\n\nUse the tools.\n'
        )
        (skill / 'SETUP.md').write_text('To activate, tell Marcel your app password.\n')

        (doc,) = [d for d in load_skills('kiddo', 'user') if d.name == 'icloudish']
        assert doc.is_setup is True
        assert 'To activate' in doc.content  # guidance, not silence

    def test_enabled_but_unlinked_connector_serves_readable_guidance(self, household):
        """An enabled connector the user has not linked degrades to a
        deferred needs-setup capability — tools are never advertised."""
        from marcel_core.connectors.loader import load_connectors
        from marcel_core.connectors.toolset import build_connector_capabilities

        zoo, _ = household
        park = zoo / 'connectors' / 'maily'
        park.mkdir()
        (park / 'connector.yaml').write_text(
            'name: maily\ndescription: Mail.\n'
            'server: {transport: stdio, command: [server.py]}\n'
            'auth: {mode: api_key, credential_keys: [MAILY_KEY]}\n'
        )
        docs = [d for d in load_connectors('kiddo', 'user') if d.name == 'maily']
        (cap,) = build_connector_capabilities('kiddo', 'user', docs=docs)
        assert 'needs setup' in (cap.description or '')
        assert not getattr(cap, 'toolsets', None)  # no tools advertised


class TestExtensionRegisteredConnectors:
    """register() extensions get the full flow via api.connector() — now
    actually wired into the loader (pre-close finding: was record-only)."""

    @pytest.fixture
    def ext_park(self, household, tmp_path):
        from marcel_core.plugin.extension import extension_registry

        park = tmp_path / 'ext-park' / 'weatherext'
        park.mkdir(parents=True)
        (park / 'connector.yaml').write_text(
            'name: weatherext\ndescription: Extension weather.\n'
            'server: {transport: stdio, command: [server.py]}\n'
            'auth: {mode: api_key, credential_keys: [WEATHER_KEY]}\n'
        )
        extension_registry().connectors.append(str(park))
        yield park
        extension_registry().connectors.clear()

    def test_discovered_with_source_extension(self, ext_park):
        from marcel_core.connectors.loader import load_connectors

        (doc,) = [d for d in load_connectors('kiddo', 'user') if d.name == 'weatherext']
        assert doc.source == 'extension'

    def test_enablement_and_guidance_apply_like_any_habitat(self, ext_park, household):
        from marcel_core.connectors.loader import load_connectors
        from marcel_core.connectors.toolset import build_connector_capabilities

        _, data = household
        # Unlinked per-user api_key → the needs-setup guidance flow.
        docs = [d for d in load_connectors('kiddo', 'user') if d.name == 'weatherext']
        (cap,) = build_connector_capabilities('kiddo', 'user', docs=docs)
        assert 'needs setup' in (cap.description or '')

        # And household policy filters it exactly like a zoo habitat.
        _manifest(data, {'connectors': {'weatherext': ['shaun']}})
        assert 'weatherext' not in {d.name for d in load_connectors('kiddo', 'user')}

    def test_zoo_habitat_overrides_extension_on_collision(self, ext_park, household):
        from marcel_core.connectors.loader import load_connectors

        zoo, _ = household
        shadow = zoo / 'connectors' / 'weatherext'
        shadow.mkdir()
        (shadow / 'connector.yaml').write_text(
            'name: weatherext\ndescription: Zoo override.\n'
            'server: {transport: inprocess, module: server.py}\n'
            'auth: {mode: none, per_user: false}\n'
        )
        (shadow / 'server.py').write_text('from fastmcp import FastMCP\nmcp = FastMCP("s")\n')
        (doc,) = [d for d in load_connectors('kiddo', 'user') if d.name == 'weatherext']
        assert doc.source == 'zoo-global'


class TestToolDispatchRespectsEnablement:
    @pytest.mark.asyncio
    async def test_tool_job_for_disabled_user_fails_loud(self, household):
        """An admin-authored TOOL job targeting a user the connector is
        disabled for fails that run loudly (verifier finding — the AGENT
        path's honest collision surface, extended to TOOL dispatch)."""
        from marcel_core.jobs.executor import _fire_tool_job
        from marcel_core.jobs.models import JobDefinition, JobDispatchType, RunStatus, TriggerSpec, TriggerType

        _, data = household
        _manifest(data, {'connectors': {'banking': ['shaun']}})

        job = JobDefinition(
            name='Sync',
            users=['kiddo'],
            trigger=TriggerSpec(type=TriggerType.INTERVAL, interval_seconds=3600),
            dispatch_type=JobDispatchType.TOOL,
            tool='banking.transactions',
            system_prompt='',
            task='sync',
        )
        run = await _fire_tool_job(job, 'test', user_slug='kiddo')
        assert run.status is RunStatus.FAILED
        assert run.error_category == 'config'
        assert 'installed but not enabled' in (run.error or '')

    @pytest.mark.asyncio
    async def test_system_runs_not_gated(self, household):
        """System-scope TOOL jobs have no user to gate — they proceed to
        dispatch (and here succeed against the inprocess park)."""
        from marcel_core.jobs.executor import _fire_tool_job
        from marcel_core.jobs.models import JobDefinition, JobDispatchType, RunStatus, TriggerSpec, TriggerType

        _, data = household
        _manifest(data, {'connectors': {'banking': []}})

        job = JobDefinition(
            name='Sync',
            users=[],
            trigger=TriggerSpec(type=TriggerType.INTERVAL, interval_seconds=3600),
            dispatch_type=JobDispatchType.TOOL,
            tool='banking.transactions',
            system_prompt='',
            task='sync',
        )
        run = await _fire_tool_job(job, 'test')
        assert run.status is RunStatus.COMPLETED
