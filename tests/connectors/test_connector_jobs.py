"""Connector scheduled jobs: materialization (D1) + tool-dispatch fallback (D2).

FEAT-260718-c232d9: job templates keep their `family.tool` refs across the
toolkit→connector migration — the scheduler materializes connector.yaml
scheduled_jobs under the same stable habitat job id, and the executor resolves
the ref against connectors when no toolkit claims it.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from marcel_core.connectors.models import ConnectorScheduledJob
from marcel_core.storage import _root


@pytest.fixture
def zoo_with_clockpark(tmp_path, monkeypatch):
    """A zoo containing one inprocess connector with a park-relative server.py."""
    from marcel_core.config import settings

    monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path / 'data')
    zoo = tmp_path / 'zoo'
    park = zoo / 'connectors' / 'clockpark'
    park.mkdir(parents=True)
    (park / 'connector.yaml').write_text(
        'name: clockpark\ndescription: A clock\n'
        'server: {transport: inprocess, module: server.py}\n'
        'auth: {mode: none, per_user: false}\n'
        'scheduled_jobs:\n'
        '  - name: "Clock tick"\n'
        '    handler: clockpark.whoami\n'
        '    cron: "0 6,18 * * *"\n'
        '    notify: on_failure\n'
    )
    (park / 'server.py').write_text(
        'from fastmcp import FastMCP\n\n'
        'def build(user_slug):\n'
        "    mcp = FastMCP('clockpark')\n"
        '    @mcp.tool\n'
        '    def whoami() -> str:\n'
        '        """Who is this server serving?"""\n'
        "        return f'serving:{user_slug}'\n"
        '    return mcp\n'
    )
    monkeypatch.setattr(settings, 'marcel_zoo_dir', str(zoo))
    return zoo


class TestScheduledJobSpec:
    def test_exactly_one_trigger_required(self):
        with pytest.raises(ValidationError, match='exactly one'):
            ConnectorScheduledJob.model_validate({'name': 'x', 'handler': 'a.b'})
        with pytest.raises(ValidationError, match='exactly one'):
            ConnectorScheduledJob.model_validate(
                {'name': 'x', 'handler': 'a.b', 'cron': '* * * * *', 'interval_seconds': 60}
            )

    def test_parses_from_connector_yaml(self, zoo_with_clockpark):
        from marcel_core.connectors.loader import get_connector

        doc = get_connector('clockpark', None, role='admin')
        assert doc is not None
        assert [j.name for j in doc.config.scheduled_jobs] == ['Clock tick']
        assert doc.config.scheduled_jobs[0].cron == '0 6,18 * * *'


class TestMaterialization:
    def test_connector_job_materialized_with_stable_id_and_tool_dispatch(self, zoo_with_clockpark):
        from marcel_core.jobs import list_all_jobs
        from marcel_core.jobs.models import JobDispatchType
        from marcel_core.jobs.scheduler import _ensure_habitat_jobs, _habitat_job_id

        _ensure_habitat_jobs()
        job = next(j for j in list_all_jobs() if j.id == _habitat_job_id('clockpark', 'Clock tick'))
        assert job.template == 'habitat:clockpark'
        assert job.trigger.cron == '0 6,18 * * *'  # cadence preserved (AC3 analogue)
        # No task/system_prompt override → deterministic tool dispatch, no LLM.
        assert job.dispatch_type is JobDispatchType.TOOL
        assert job.tool == 'clockpark.whoami'

    def test_materialization_is_idempotent(self, zoo_with_clockpark):
        from marcel_core.jobs import list_all_jobs
        from marcel_core.jobs.scheduler import _ensure_habitat_jobs

        _ensure_habitat_jobs()
        _ensure_habitat_jobs()
        assert sum(1 for j in list_all_jobs() if j.template == 'habitat:clockpark') == 1


class TestConnectorToolDispatch:
    @pytest.mark.asyncio
    async def test_tool_job_falls_back_to_the_connector(self, zoo_with_clockpark):
        """The same `family.tool` ref keeps working once the toolkit is gone —
        resolved against the connector, called over the real MCP protocol,
        with the run slug delivered to the park's per-user factory."""
        from marcel_core.jobs.executor import _fire_tool_job
        from marcel_core.jobs.models import JobDefinition, JobDispatchType, RunStatus, TriggerSpec, TriggerType

        job = JobDefinition(
            id='t-clock',
            name='tick',
            description='',
            users=['shaun'],
            trigger=TriggerSpec(type=TriggerType.INTERVAL, interval_seconds=3600),
            dispatch_type=JobDispatchType.TOOL,
            tool='clockpark.whoami',
            task='',
            system_prompt='',
        )
        run = await _fire_tool_job(job, 'test')
        assert run.status is RunStatus.COMPLETED
        assert 'serving:shaun' in (run.output or '')

    @pytest.mark.asyncio
    async def test_unresolvable_ref_fails_with_config_error(self, zoo_with_clockpark):
        from marcel_core.jobs.executor import _fire_tool_job
        from marcel_core.jobs.models import JobDefinition, JobDispatchType, RunStatus, TriggerSpec, TriggerType

        job = JobDefinition(
            id='t-ghost',
            name='ghost',
            description='',
            users=['shaun'],
            trigger=TriggerSpec(type=TriggerType.INTERVAL, interval_seconds=3600),
            dispatch_type=JobDispatchType.TOOL,
            tool='ghost.nothing',
            task='',
            system_prompt='',
        )
        run = await _fire_tool_job(job, 'test')
        assert run.status is RunStatus.FAILED
        assert run.error_category == 'config'


class TestDispatchShapeRoundTrip:
    """Regression: save_job dropped dispatch_type/tool, so a TOOL job read back
    as AGENT. Connector scheduled jobs were the first to hit it."""

    def test_tool_dispatch_survives_disk(self, tmp_path, monkeypatch):
        from marcel_core.jobs import list_all_jobs, save_job
        from marcel_core.jobs.models import JobDefinition, JobDispatchType, TriggerSpec, TriggerType

        monkeypatch.setattr(_root, '_DATA_ROOT', tmp_path)
        save_job(
            JobDefinition(
                id='rt-1',
                name='rt',
                description='',
                users=['shaun'],
                trigger=TriggerSpec(type=TriggerType.INTERVAL, interval_seconds=60),
                dispatch_type=JobDispatchType.TOOL,
                tool='clockpark.whoami',
                tool_params={'a': 'b'},
                task='',
                system_prompt='',
            )
        )
        loaded = next(j for j in list_all_jobs() if j.id == 'rt-1')
        assert loaded.dispatch_type is JobDispatchType.TOOL
        assert loaded.tool == 'clockpark.whoami'
        assert loaded.tool_params == {'a': 'b'}
