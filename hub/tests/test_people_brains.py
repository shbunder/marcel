"""S-17.7: a brain per person. Each agent's brain runs in its own folder, which the runner makes
ready first (`POST /brains`); the owner's first agent keeps the brain workspace from the settings.
An archived agent has no brain, no schedules and no digests.

Nothing here reaches a real `claude` or runner: `FakeBrains` and `FakeRunner` answer from memory."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy.engine import Engine
from sqlmodel import Session, select
from test_brain import CWD, FakeBrains
from test_schedule import DAILY
from test_schedule import Clock as ScheduleClock
from test_schedule import tasks as schedule_tasks
from test_tasks_fakes import (  # noqa: F401  (fixtures)
    AGENT,
    FakeRunner,
    outbox_fixture,
    publisher_fixture,
    registry_fixture,
    run,
    runner_fixture,
)

from marcel_hub.auth.tokens import OWNER_USER_ID
from marcel_hub.brain import BrainKeeper, brain_name
from marcel_hub.channel.outbox import ChannelOutbox
from marcel_hub.digest import Digests, Facts
from marcel_hub.models import Agent, Schedule, User, UserRole
from marcel_hub.runner_client import RunnerClient
from marcel_hub.schedule import Scheduler
from marcel_hub.tasks.ports import RunnerUnavailable, StartFailed
from marcel_hub.tasks.registry import TaskRegistry


class Brains(FakeBrains):
    """`FakeBrains` that also makes brain folders, as the runner's `POST /brains` does."""

    def __init__(self) -> None:
        super().__init__()
        self.prepared: list[dict[str, Any]] = []
        self.prepare_fails: Exception | None = None

    async def prepare_brain(
        self, *, agent_slug: str, name: str, person_name: str, personality: str | None,
        animal: str | None = None,
    ) -> str:  # fmt: skip
        self.prepared.append(
            {'slug': agent_slug, 'name': name, 'person': person_name, 'personality': personality}
            | ({'animal': animal} if animal else {})
        )
        if self.prepare_fails is not None:
            raise self.prepare_fails
        return f'/home/owner/marcel/brains/{agent_slug}'


def add(engine: Engine, *, slug: str | None, user: str, person: str, name: str,
        personality: str | None = None, animal: str | None = None,
        archived: bool = False) -> str:  # fmt: skip
    with Session(engine) as s:
        if s.get(User, user) is None:
            role = UserRole.owner if user == OWNER_USER_ID else UserRole.member
            s.add(User(id=user, name=person, role=role))
        agent = Agent(owner_user_id=user, name=name, animal='fox', palette='tangerine', seed=name,
                      slug=slug, look_personality=personality, look_animal=animal,
                      archived_at=datetime.now(UTC) if archived else None)  # fmt: skip
        s.add(agent)
        s.commit()
        return agent.id


@pytest.fixture
def brains() -> Brains:
    return Brains()


def keeper(engine: Engine, brains: Brains) -> BrainKeeper:
    return BrainKeeper(engine, brains, cwd=CWD)


def spawned_in(brains: Brains, agent_id: str) -> list[str]:
    return [b['cwd'] for b in brains.spawned if b['name'] == brain_name(agent_id)]


# The keeper ---------------------------------------------------------------------------------


def test_each_person_gets_a_brain_in_their_own_folder(engine: Engine, brains: Brains) -> None:
    marcel = add(engine, slug='marcel', user=OWNER_USER_ID, person='Shaun', name='Marcel')
    pip = add(
        engine, slug='pip', user='usr_emma', person='Emma', name='Pip', personality='cheerful'
    )
    run(keeper(engine, brains).check())
    assert spawned_in(brains, marcel) == [CWD], "the owner's brain keeps its workspace"
    assert spawned_in(brains, pip) == ['/home/owner/marcel/brains/pip']
    assert brains.prepared == [
        {'slug': 'pip', 'name': 'Pip', 'person': 'Emma', 'personality': 'cheerful'}
    ], 'only a family member needs a folder made; the person and the look reach the persona'


def test_a_folder_that_cannot_be_made_backs_off_and_starts_nothing(
    engine: Engine, brains: Brains
) -> None:
    """The runner refuses (or is down): no brain is started in the wrong folder; the keeper's
    usual backoff and alert take over."""
    pip = add(engine, slug='pip', user='usr_emma', person='Emma', name='Pip')
    for failure in (StartFailed('render_failed: a real file sits where MEMORY.md goes'),
                    RunnerUnavailable('The runner is not answering.')):  # fmt: skip
        brains.prepare_fails = failure
        k = keeper(engine, brains)
        run(k.check())
        assert spawned_in(brains, pip) == []
        assert k._state(pip).failures == 1


def test_an_archived_agent_gets_no_brain_and_its_running_one_is_stopped(
    engine: Engine, brains: Brains
) -> None:
    """Delete the archived filter in `_agent_ids` and the keeper starts Emma's brain again."""
    gone = add(engine, slug='pip', user='usr_emma', person='Emma', name='Pip', archived=True)
    left_running = brains.add(name=brain_name(gone))
    run(keeper(engine, brains).check())
    assert spawned_in(brains, gone) == []
    assert brains.prepared == []
    assert brains.stopped == [left_running]


# The scheduler and the digests ----------------------------------------------------------------


def archive(engine: Engine, agent_id: str) -> None:
    with Session(engine) as s:
        agent = s.get(Agent, agent_id)
        assert agent is not None
        agent.archived_at = datetime.now(UTC)
        s.add(agent)
        s.commit()


def test_an_archived_agents_schedules_do_not_fire(
    engine: Engine, registry: TaskRegistry, tmp_path: Path
) -> None:
    clock = ScheduleClock()
    sched = Scheduler(engine, registry, ChannelOutbox(engine), clock=clock,
                      state_path=tmp_path / 'sched.json')  # fmt: skip
    sched.create(AGENT, DAILY)
    archive(engine, AGENT)
    clock.go(minutes=1, seconds=5)  # 08:00:05 Brussels: due
    run(sched.tick())
    assert schedule_tasks(engine) == []


def test_an_archived_agent_gets_no_digests(engine: Engine, tmp_path: Path) -> None:
    live = add(engine, slug='marcel', user=OWNER_USER_ID, person='Shaun', name='Marcel')
    gone = add(engine, slug='pip', user='usr_emma', person='Emma', name='Pip', archived=True)
    digests = Digests(engine, Facts(engine), state_path=tmp_path / 'digest.json',
                      clock=lambda: datetime(2026, 10, 12, 9, tzinfo=UTC))  # fmt: skip
    assert digests.ensure_builtins() == 4  # four built-ins, all for the live agent
    with Session(engine) as s:
        owners = {r.agent_id for r in s.exec(select(Schedule))}
    assert owners == {live} and gone not in owners


# The runner client -------------------------------------------------------------------------


def client(status: int, body: dict[str, Any], seen: list[dict[str, Any]]) -> RunnerClient:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append({'path': request.url.path, 'body': json.loads(request.content)})
        return httpx.Response(status, json=body)

    return RunnerClient('/x.sock', transport=httpx.MockTransport(handler))


def test_preparing_pips_brain_sends_its_animal(engine: Engine, brains: Brains) -> None:
    """S-17.8: Pip's look is a rabbit, so its brain is told it is a rabbit, not a giraffe."""
    add(engine, slug='marcel', user=OWNER_USER_ID, person='Shaun', name='Marcel')
    add(engine, slug='pip', user='usr_emma', person='Emma', name='Pip', personality='cheerful',
        animal='rabbit')  # fmt: skip
    run(keeper(engine, brains).check())
    assert brains.prepared == [
        {
            'slug': 'pip',
            'name': 'Pip',
            'person': 'Emma',
            'personality': 'cheerful',
            'animal': 'rabbit',
        }
    ]


def test_the_runner_client_sends_the_animal_in_the_persona() -> None:
    seen: list[dict[str, Any]] = []
    run(client(200, {'cwd': '/b/pip'}, seen).prepare_brain(
        agent_slug='pip', name='Pip', person_name='Emma', personality='cheerful',
        animal='rabbit'))  # fmt: skip
    assert seen[0]['body']['persona'] == {
        'person_name': 'Emma',
        'personality': 'cheerful',
        'animal': 'rabbit',
    }


def test_the_runner_client_asks_for_the_folder_and_returns_it() -> None:
    seen: list[dict[str, Any]] = []
    rc = client(200, {'agent_slug': 'pip', 'cwd': '/b/pip', 'memory_dir': '/m/agents/pip'}, seen)
    cwd = run(rc.prepare_brain(agent_slug='pip', name='Pip', person_name='Emma',
                               personality='cheerful'))  # fmt: skip
    assert cwd == '/b/pip'
    persona = {'person_name': 'Emma', 'personality': 'cheerful'}
    assert seen == [
        {'path': '/brains', 'body': {'agent_slug': 'pip', 'name': 'Pip', 'persona': persona}}
    ]
    seen.clear()
    run(client(200, {'cwd': '/b/x'}, seen).prepare_brain(
        agent_slug='x', name='X', person_name='Y', personality=None))  # fmt: skip
    assert seen[0]['body']['persona'] == {'person_name': 'Y'}  # no personality or animal: left out


@pytest.mark.parametrize(('status', 'error'), [(503, RunnerUnavailable), (422, StartFailed)])
def test_the_runner_client_maps_refusals(status: int, error: type[Exception]) -> None:
    rc = client(status, {'code': 'x', 'message': 'no'}, [])
    with pytest.raises(error):
        run(rc.prepare_brain(agent_slug='pip', name='Pip', person_name='Emma', personality=None))


def test_two_brains_never_share_a_folder(engine: Engine, brains: Brains) -> None:
    add(engine, slug='marcel', user=OWNER_USER_ID, person='Shaun', name='Marcel')
    add(engine, slug='pip', user='usr_emma', person='Emma', name='Pip')
    add(engine, slug='rex', user='usr_tom', person='Tom', name='Rex')
    run(keeper(engine, brains).check())
    folders = [b['cwd'] for b in brains.spawned]
    assert len(folders) == len(set(folders)) == 3


def test_only_the_owners_own_marcel_keeps_the_owners_folder(engine: Engine, brains: Brains) -> None:
    """A member's agent called `marcel` gets its own folder: the owner's workspace and memory are
    the owner's. Drop the owner check in `_cwd_for` and this goes red."""
    other = add(engine, slug='marcel', user='usr_emma', person='Emma', name='Marcel')
    run(keeper(engine, brains).check())
    assert spawned_in(brains, other) == ['/home/owner/marcel/brains/marcel']


def test_one_brain_failing_never_stops_or_clears_another(engine: Engine, brains: Brains) -> None:
    """Pip's folder cannot be made: Marcel's brain still starts, Pip's alert names Pip, and Pip
    starting later clears only Pip's alert."""
    from test_runner_client_fakes import RecordingAlerts

    from marcel_hub import brain as brain_mod

    marcel = add(engine, slug='marcel', user=OWNER_USER_ID, person='Shaun', name='Marcel')
    pip = add(engine, slug='pip', user='usr_emma', person='Emma', name='Pip')
    alerts = RecordingAlerts()
    clock = {'mono': 1000.0}
    k = BrainKeeper(engine, brains, cwd=CWD, alerts=alerts, monotonic=lambda: clock['mono'])
    brains.prepare_fails = StartFailed('render_failed')
    for _ in range(brain_mod.ALERT_AFTER):
        run(k.check())
        clock['mono'] += 3600
    assert spawned_in(brains, marcel) == [CWD]
    assert spawned_in(brains, pip) == []
    assert [(c, t) for c, t, _ in alerts.raised] == [('brain_down:pip', 'Pip cannot start')]
    brains.prepare_fails = None
    run(k.check())
    assert spawned_in(brains, pip) == ['/home/owner/marcel/brains/pip']
    assert alerts.cleared == ['brain_down:pip']
