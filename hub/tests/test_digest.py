"""Digests (S-09.2): the facts bundle from a seeded database, the `digest_due` event through the
outbox, the usage report folded into the evening digest, and what happens after the brain
publishes, or does not.

Controls, each with a test here that goes red without it: every list is capped (with `more`);
token-shaped words never reach the bundle; approval details never reach it; the usage report is
not sent twice at the evening digest's time; a digest the brain did not publish in time is said
once, in plain words; the built-in digests cannot be deleted.
"""

import asyncio
import json
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select
from test_tasks_fakes import (  # noqa: F401  (fixtures)
    AGENT,
    FakeRunner,
    outbox_fixture,
    publisher_fixture,
    registry_fixture,
    run,
    runner_fixture,
)

from marcel_hub import digest
from marcel_hub.app import Settings, SettingsError, create_app, load_settings
from marcel_hub.auth.tokens import new_token
from marcel_hub.brain_api import NotSetUp
from marcel_hub.channel.outbox import ChannelOutbox, conversation_target
from marcel_hub.digest import (
    BUILTINS,
    DEADLINE,
    MAX_ITEMS,
    Digests,
    Facts,
    builtin_id,
    headline,
)
from marcel_hub.models import (
    Agent,
    Approval,
    Artifact,
    ArtifactKind,
    ArtifactVersion,
    Message,
    MessageKind,
    MessageRole,
    Project,
    Schedule,
    ScheduleLocation,
    Task,
    TaskLocation,
    TaskOrigin,
    TaskState,
)
from marcel_hub.schedule import ScheduleBuiltin, Scheduler
from marcel_hub.tasks.registry import TaskRegistry
from marcel_hub.usage import UsageGuard

TZ = 'Europe/Brussels'
# Monday 12 October 2026, 21:00 in Brussels (UTC+2): the evening digest's time.
EVENING = datetime(2026, 10, 12, 19, 0, tzinfo=UTC)
TODAY = datetime(2026, 10, 12, 8, 0, tzinfo=UTC)
TOKEN = 'ghp_' + 'a1B2c3D4e5F6g7H8i9J0k1L2m3N4'


class Clock:
    def __init__(self, now: datetime = EVENING) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def go(self, **delta: float) -> None:
        self.now += timedelta(**delta)


class FakeUsage:
    def __init__(self) -> None:
        self.by_task: dict[str, float] = {}
        self.reading: dict[str, Any] | None = {
            'five_hour': {'used_pct': 41.0, 'resets_at': '2026-10-12T20:00:00Z'},
            'weekly': {'used_pct': 62.4, 'resets_at': '2026-10-15T07:00:00Z'},
            'source': 'statusline',
            'stale': False,
        }

    def current(self) -> dict[str, Any]:
        if self.reading is None:
            raise NotSetUp('no reading yet')
        return self.reading

    def usage_by_task(self) -> dict[str, float]:
        return dict(self.by_task)


class Pushes:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str, str | None]] = []

    def digest(self, message_id: str, title: str, body: str, user_id: str | None = None) -> None:
        self.sent.append((message_id, title, body, user_id))


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def usage() -> FakeUsage:
    return FakeUsage()


@pytest.fixture
def pushes() -> Pushes:
    return Pushes()


@pytest.fixture
def frames(engine: Engine) -> ChannelOutbox:
    return ChannelOutbox(engine)


@pytest.fixture
def digests(
    engine: Engine, registry: TaskRegistry, usage: FakeUsage, pushes: Pushes, clock: Clock,
    tmp_path: Path,
) -> Digests:  # fmt: skip
    return Digests(
        engine, Facts(engine, usage), pusher=pushes, clock=clock,
        state_path=tmp_path / digest.STATE_FILE, tz=TZ,
    )  # fmt: skip


@pytest.fixture
def sched(
    engine: Engine, registry: TaskRegistry, frames: ChannelOutbox, clock: Clock, digests: Digests
) -> Scheduler:
    scheduler = Scheduler(engine, registry, frames, clock=clock)
    scheduler.brain_frame = digests.frame
    return scheduler


# Seeding ------------------------------------------------------------------------------------


def task(s: Session, title: str, state: TaskState, at: datetime, **kw: Any) -> Task:
    t = Task(agent_id=kw.pop('agent_id', AGENT), title=title, location=TaskLocation.nuc,
             state=state, model='sonnet', origin=TaskOrigin.marcel, created_at=at,
             updated_at=kw.pop('updated_at', at), **kw)  # fmt: skip
    s.add(t)
    s.commit()
    s.refresh(t)
    return t


def pr(s: Session, title: str, state: str, checks: str, review: str, at: datetime) -> Artifact:
    """A PR item of AGENT's, filed in its inbox (a loose item goes only to the owner, B-38)."""
    inbox = s.exec(select(Project).where(Project.agent_id == AGENT, Project.slug == 'inbox')).one()
    art = Artifact(kind=ArtifactKind.pr, title=title, url='https://github.com/o/r/pull/1',
                   meta_json={'pr': {'state': state, 'checks': checks, 'review': review}},
                   project_id=inbox.id, created_at=at, updated_at=at)  # fmt: skip
    s.add(art)
    s.commit()
    return art


def seed(engine: Engine) -> dict[str, str]:
    """A day of work for AGENT, and some for another agent. Returns the ids by name."""
    ids: dict[str, str] = {}
    day = timedelta(days=1)
    with Session(engine) as s:
        s.add(Agent(id='agt_other', owner_user_id='usr_2', name='Bob', animal='cat',
                    palette='blue', seed='2'))  # fmt: skip
        s.commit()
        garden = Project(agent_id=AGENT, slug='garden', name='Garden')
        s.add(garden)
        s.commit()
        ids['working'] = task(s, 'Docker cleanup', TaskState.working, TODAY, project_id=garden.id,
                              last_activity_at=EVENING - timedelta(minutes=5)).id  # fmt: skip
        asks = task(s, 'Fix flaky login test', TaskState.needs_you, TODAY + timedelta(hours=1))
        ids['asks'] = asks.id
        s.add(Approval(id='apr_1', task_id=asks.id, tool='Bash', summary='Push to main',
                       detail=f'git push https://{TOKEN}@github.com/o/r main',
                       created_at=TODAY + timedelta(hours=2)))  # fmt: skip
        s.add(Approval(id='apr_old', task_id=asks.id, tool='Bash', summary='Answered',
                       state='approved', created_at=TODAY))  # fmt: skip
        ids['question'] = task(s, 'Plan the trip', TaskState.needs_you, TODAY,
                               waiting_for='Which weekend?').id  # fmt: skip
        ids['done'] = task(s, 'Back up the photos', TaskState.done, TODAY,
                           summary=f'Backed up 1,204 photos with token {TOKEN}.').id  # fmt: skip
        ids['done_yesterday'] = task(s, 'Water the garden', TaskState.done, TODAY - day).id
        ids['failed'] = task(s, 'Renew the certificate', TaskState.failed, TODAY,
                             summary='The DNS check timed out.').id  # fmt: skip
        ids['stale'] = task(s, 'Sort the recipes', TaskState.done, TODAY - 5 * day).id
        task(s, 'Archived long ago', TaskState.done, TODAY - 9 * day, archived_at=TODAY - 8 * day)
        task(s, 'Not ours', TaskState.working, TODAY, agent_id='agt_other')

        ids['pr_ready'] = pr(s, 'Fix login', 'open', 'passing', 'approved', TODAY).id
        pr(s, 'Draft idea', 'draft', 'passing', 'none', TODAY)
        pr(s, 'Broken build', 'open', 'failing', 'none', TODAY)
        pr(s, 'Changes asked', 'open', 'passing', 'changes_requested', TODAY)
        ids['pr_merged'] = pr(s, 'Cache fix', 'merged', 'passing', 'approved', TODAY).id
        doc = Artifact(kind=ArtifactKind.doc, title='Photo index', task_id=ids['done'],
                       created_at=TODAY, updated_at=TODAY)  # fmt: skip
        s.add(doc)
        s.add(Schedule(id='sch_garden', agent_id=AGENT, name='Garden check', cron='30 22 * * *',
                       tz=TZ, location=ScheduleLocation.nuc, prompt='Check.'))  # fmt: skip
        s.add(Schedule(id='sch_paused', agent_id=AGENT, name='Paused', cron='45 22 * * *',
                       tz=TZ, location=ScheduleLocation.nuc, prompt='x', paused=True))  # fmt: skip
        s.add(Schedule(id='sch_tomorrow', agent_id=AGENT, name='Tomorrow', cron='0 6 * * *',
                       tz=TZ, location=ScheduleLocation.cloud, prompt='x'))  # fmt: skip
        s.commit()
        ids['doc'] = doc.id
    return ids


def ids_of(section: dict[str, Any], key: str = 'task_id') -> list[str]:
    return [item[key] for item in section['items']]


# The facts bundle ---------------------------------------------------------------------------


def test_the_facts_bundle_is_correct_for_a_seeded_db(
    engine: Engine, registry: TaskRegistry, usage: FakeUsage
) -> None:
    ids = seed(engine)
    usage.by_task = {ids['working']: 6.0, 'other': 2.0}
    facts = Facts(engine, usage)
    b = facts.bundle(AGENT, 'evening', TZ, EVENING, include_usage=True)

    assert b['digest'] == 'evening' and b['tz'] == TZ
    assert b['now'] == '2026-10-12T21:00:00+02:00'
    assert b['period'] == {'from': '2026-10-12T00:00:00+02:00', 'to': '2026-10-12T21:00:00+02:00'}
    assert b['approvals'] == {'count': 1, 'more': 0, 'items': [{
        'approval_id': 'apr_1', 'task_id': ids['asks'], 'thread': 'Fix flaky login test',
        'asks': 'Push to main', 'tool': 'Bash', 'since': '2026-10-12T12:00:00+02:00'}]}  # fmt: skip
    assert b['questions']['items'] == [{
        'task_id': ids['question'], 'thread': 'Plan the trip', 'question': 'Which weekend?',
        'since': '2026-10-12T10:00:00+02:00'}]  # fmt: skip
    assert b['in_flight']['items'] == [{
        'task_id': ids['working'], 'thread': 'Docker cleanup', 'state': 'working', 'where': 'nuc',
        'project': 'Garden', 'since': '2026-10-12T10:00:00+02:00',
        'last_activity': '2026-10-12T20:55:00+02:00'}]  # fmt: skip
    assert ids_of(b['done']) == [ids['done']]  # not yesterday's, not the other agent's
    assert b['done']['items'][0]['outcome'] == 'Backed up 1,204 photos with token [hidden].'
    assert b['failures']['items'] == [{
        'task_id': ids['failed'], 'thread': 'Renew the certificate',
        'why': 'The DNS check timed out.', 'at': '2026-10-12T10:00:00+02:00'}]  # fmt: skip
    assert b['prs_ready']['items'] == [{
        'artifact_id': ids['pr_ready'], 'title': 'Fix login',
        'url': 'https://github.com/o/r/pull/1', 'checks': 'passing', 'review': 'approved',
        'thread': None}]  # fmt: skip
    assert ids_of(b['prs_merged'], 'artifact_id') == [ids['pr_merged']]
    assert ids_of(b['artifacts'], 'artifact_id')[-1] == ids['doc']
    assert b['artifacts']['items'][-1]['thread'] == 'Back up the photos'
    assert b['artifacts']['count'] == 6  # five PRs and the doc; no boards
    assert [(i['task_id'], i['idle_days']) for i in b['stale']['items']] == [(ids['stale'], 5)]
    assert b['schedules_today']['items'] == [{
        'schedule_id': 'sch_garden', 'name': 'Garden check', 'at': '2026-10-12T22:30:00+02:00',
        'where': 'nuc'}]  # fmt: skip
    assert b['usage'] == {
        'plan': {
            'five_hour': {'used_pct': 41.0, 'resets_at': '2026-10-12T22:00:00+02:00'},
            'weekly': {'used_pct': 62.4, 'resets_at': '2026-10-15T09:00:00+02:00'},
            'stale': False,
        },
        'by_task': {'count': 2, 'more': 0, 'items': [
            {'task_id': ids['working'], 'thread': 'Docker cleanup', 'points': 6.0,
             'share_pct': 75},
            {'task_id': None, 'thread': 'Other work', 'points': 2.0, 'share_pct': 25},
        ]},
        'since': None,
    }  # fmt: skip

    # Deterministic: the same database and clock give the same bundle.
    assert facts.bundle(AGENT, 'evening', TZ, EVENING, include_usage=True) == b
    # No secrets: not the token in a summary, not the approval's detail.
    text = json.dumps(b)
    assert TOKEN not in text and 'git push' not in text


def test_the_weekly_bundle_covers_seven_days(engine: Engine, registry: TaskRegistry) -> None:
    ids = seed(engine)
    b = Facts(engine).bundle(AGENT, 'weekly', TZ, EVENING)
    assert b['period']['from'] == '2026-10-05T21:00:00+02:00'
    assert set(ids_of(b['done'])) == {ids['done'], ids['done_yesterday'], ids['stale']}
    assert 'usage' not in b


def test_the_morning_bundle_has_no_usage_and_lists_the_rest_of_today(
    engine: Engine, registry: TaskRegistry
) -> None:
    seed(engine)
    morning = datetime(2026, 10, 12, 5, 0, tzinfo=UTC)  # 07:00 in Brussels
    b = Facts(engine).bundle(AGENT, 'morning', TZ, morning)
    assert 'usage' not in b
    assert [i['schedule_id'] for i in b['schedules_today']['items']] == ['sch_garden']


def test_every_list_is_capped_and_says_how_many_more(
    engine: Engine, registry: TaskRegistry
) -> None:
    with Session(engine) as s:
        for n in range(MAX_ITEMS + 25):
            task(s, f'Job {n:02d} ' + 'x' * 400, TaskState.working, TODAY + timedelta(seconds=n))
    b = Facts(engine).bundle(AGENT, 'morning', TZ, EVENING)
    flight = b['in_flight']
    assert flight['count'] == MAX_ITEMS + 25 and flight['more'] == 25
    assert len(flight['items']) == MAX_ITEMS
    assert (
        flight['items'][0]['thread'].startswith('Job 00')
        and len(flight['items'][0]['thread']) <= 160
    )
    assert len(json.dumps(b)) < 16_000


def test_secrets_of_every_common_shape_are_hidden() -> None:
    hub_secret = secrets.token_hex(32)  # the channel's hub secret: 64 hex
    for secret in (TOKEN, 'github_pat_' + 'A' * 30, 'sk-ant-' + 'b' * 30, 'xoxb-' + '1' * 20,
                   'AKIA' + 'Z' * 16, 'Bearer ' + 'q' * 30, new_token(), hub_secret,
                   hub_secret.upper()[:32], 'c0ffee' * 6):  # fmt: skip
        assert secret not in digest.safe_text(f'it used {secret} here'), secret
    for line, kept in (
        (f'MARCEL_HUB_SECRET={hub_secret[:12]}', 'MARCEL_HUB_SECRET=[hidden]'),
        ('export API_KEY=Zm9vYmFyYmF6', 'export API_KEY=[hidden]'),
        ('db_password = "hunter2 two"', 'db_password=[hidden]'),
        ("my-token='abc'", 'my-token=[hidden]'),
        ('SECRET=x and more', 'SECRET=[hidden] and more'),
    ):
        shown = digest.safe_text(line)
        assert kept in shown, (line, shown)
        for value in (hub_secret[:12], 'Zm9v', 'hunter2', 'abc', '=x'):
            assert value not in shown, (line, shown)
    for fine in (
        'A normal title, sk-ip it',
        'Fix the key handling',
        'monkey=banana',
        'Thread tsk_019a2b3c4d5e1f2a3b4c done',
        'Token ring = old',
        'v2.3.1 at 21:00',
    ):
        assert digest.safe_text(fine) == fine


def test_usage_by_task_counts_from_the_last_report(
    engine: Engine, registry: TaskRegistry, usage: FakeUsage
) -> None:
    usage.by_task = {'tsk_a': 10.0, 'tsk_b': 3.0, 'tsk_gone': 1.0}
    b = Facts(engine, usage).bundle(
        AGENT, 'evening', 'Not/AZone', EVENING, include_usage=True,
        usage_baseline={'tsk_a': 4.0, 'tsk_b': 3.0, 'tsk_gone': 5.0}, usage_since=TODAY,
    )  # fmt: skip
    assert b['tz'] == 'UTC'
    items = b['usage']['by_task']['items']
    assert [(i['points'], i['share_pct']) for i in items] == [(6.0, 86), (1.0, 14)]
    assert items[0]['thread'] == 'Another agent’s thread'
    assert b['usage']['since'] == '2026-10-12T08:00:00+00:00'
    usage.reading = None
    assert Facts(engine, usage).bundle(AGENT, 'evening', TZ, EVENING,
                                       include_usage=True)['usage']['plan'] is None  # fmt: skip


def test_the_headline_is_plain_numbers() -> None:
    def section(n: int) -> dict[str, int]:
        return {'count': n}

    assert headline({'digest': 'morning'}) == 'Nothing needs you.'
    assert headline({
        'digest': 'weekly', 'approvals': section(1), 'questions': section(1),
        'failures': section(1), 'in_flight': section(3), 'done': section(1),
        'prs_ready': section(2), 'stale': section(4),
        'usage': {'plan': {'weekly': {'used_pct': 62.4}}},
    }) == ('2 things wait for you; 1 thread failed; 3 threads are working; 1 finished; '
           '2 pull requests are ready to merge; 4 threads look stale; the plan is at 62 % of '
           'the week.')  # fmt: skip


# Built-in schedules -------------------------------------------------------------------------


def schedules(engine: Engine) -> dict[str, Schedule]:
    with Session(engine) as s:
        return {r.id: r for r in s.exec(select(Schedule))}


def test_four_builtin_digests_are_made_once_and_cannot_be_deleted(
    engine: Engine, digests: Digests, sched: Scheduler
) -> None:
    assert digests.ensure_builtins() == 4
    assert digests.ensure_builtins() == 0
    rows = schedules(engine)
    morning = rows[builtin_id('morning', AGENT)]
    assert (morning.name, morning.cron, morning.tz, morning.builtin) == (
        'Morning digest', '0 7 * * *', TZ, True)  # fmt: skip
    assert {r.cron for r in rows.values()} == {'0 7 * * *', '0 21 * * *', '0 18 * * 0'}
    assert all(r.location == ScheduleLocation.brain for r in rows.values())
    assert [b.digest for b in BUILTINS] == ['morning', 'evening', 'weekly', 'usage']
    with pytest.raises(ScheduleBuiltin):
        sched.delete(AGENT, builtin_id('weekly', AGENT))
    assert sched.update(builtin_id('weekly', AGENT), {'paused': True})['paused'] is True


# Firing -------------------------------------------------------------------------------------


def due(engine: Engine, *digest_names: str) -> None:
    """Make these digests due at EVENING (they then fire on the next look)."""
    with Session(engine) as s:
        for name in digest_names:
            row = s.get(Schedule, builtin_id(name, AGENT))
            assert row is not None
            row.next_run_at = EVENING
            s.add(row)
        s.commit()


def sent(frames: ChannelOutbox) -> list[dict[str, Any]]:
    return frames.pending(f'conversation:{frames.main_conversation(AGENT)}')


def test_the_evening_digest_carries_usage_and_the_usage_report_is_not_sent_twice(
    engine: Engine, digests: Digests, sched: Scheduler, frames: ChannelOutbox, usage: FakeUsage
) -> None:
    seed(engine)
    usage.by_task = {'other': 2.0}
    digests.ensure_builtins()
    due(engine, 'evening', 'usage')
    run(sched.tick())
    (frame,) = sent(frames)
    assert frame['type'] == 'system_event' and frame['kind'] == 'digest_due'
    assert frame['text'].startswith('The evening digest is due.')
    assert frame['data']['digest'] == 'evening' and 'artifact_id' not in frame['data']
    assert frame['data']['facts']['usage']['by_task']['count'] == 1
    assert [p.digest for p in digests.pending] == ['evening']
    # The next report counts from here.
    assert digests.baselines['day'].by_task == {'other': 2.0}


def test_a_usage_report_moved_away_from_the_evening_is_sent_on_its_own(
    engine: Engine, digests: Digests, sched: Scheduler, frames: ChannelOutbox
) -> None:
    digests.ensure_builtins()
    sched.update(builtin_id('usage', AGENT), {'cron': '0 20 * * *'})
    due(engine, 'evening', 'usage')
    run(sched.tick())
    by_digest = {f['data']['digest']: f['data']['facts'] for f in sent(frames)}
    assert set(by_digest) == {'evening', 'usage'}
    assert 'usage' not in by_digest['evening'] and 'usage' in by_digest['usage']


def test_a_paused_usage_report_leaves_usage_out_of_the_evening(
    engine: Engine, digests: Digests, sched: Scheduler, frames: ChannelOutbox
) -> None:
    digests.ensure_builtins()
    sched.update(builtin_id('usage', AGENT), {'paused': True})
    due(engine, 'evening')
    run(sched.tick())
    (frame,) = sent(frames)
    assert 'usage' not in frame['data']['facts']


def test_other_brain_schedules_keep_their_own_event(
    engine: Engine, digests: Digests, sched: Scheduler, frames: ChannelOutbox, clock: Clock
) -> None:
    made = sched.create(AGENT, {'name': 'Tea', 'cron': '1 21 * * *', 'tz': TZ,
                                'location': 'brain', 'prompt': 'Remind me.'})  # fmt: skip
    clock.go(minutes=1)
    run(sched.tick())
    (frame,) = sent(frames)
    assert frame['kind'] == 'schedule_fired' and frame['data']['schedule_id'] == made['id']
    assert digests.pending == []


# After the event: published, or not ---------------------------------------------------------


def publish(
    engine: Engine,
    title: str = 'Evening digest — Monday 12 October',
    at: datetime = EVENING,
    artifact_id: str | None = None,
    summary: str = '2 things wait for you.',
) -> str:
    """What the brain's `artifacts_publish` stores: a doc by Marcel, in the inbox."""
    with Session(engine) as s:
        inbox = s.exec(select(Project).where(Project.agent_id == AGENT)
                       .where(Project.slug == 'inbox')).one()  # fmt: skip
        if artifact_id is None:
            art = Artifact(kind=ArtifactKind.doc, title=title, project_id=inbox.id,
                           updated_by='Marcel', created_at=at, updated_at=at)  # fmt: skip
            s.add(art)
            s.commit()
            version = s.get(ArtifactVersion, (art.id, 1))
        else:
            art = s.get(Artifact, artifact_id)
            assert art is not None
            art.current_version += 1
            art.updated_at = at
            s.add(art)
            version = ArtifactVersion(artifact_id=art.id, version=art.current_version)
        assert version is not None
        version.summary = summary
        s.add(version)
        s.commit()
        return art.id


def messages(engine: Engine) -> list[Message]:
    with Session(engine) as s:
        return list(s.exec(select(Message).order_by(col(Message.created_at), col(Message.id))))


def test_a_published_digest_becomes_the_board_with_a_card_and_a_push(
    engine: Engine, digests: Digests, sched: Scheduler, frames: ChannelOutbox, clock: Clock,
    pushes: Pushes,
) -> None:  # fmt: skip
    digests.ensure_builtins()
    due(engine, 'evening')
    run(sched.tick())
    clock.go(minutes=2)
    digests.check()
    assert digests.pending and messages(engine) == []  # not published yet: nothing said

    board = publish(engine, at=clock.now)
    digests.check()
    assert digests.pending == [] and digests.boards == {AGENT: board}
    (card,) = messages(engine)
    assert (card.kind, card.role, card.artifact_id) == (MessageKind.artifact, MessageRole.agent,
                                                        board)  # fmt: skip
    assert card.project_id is not None
    ((message_id, title, body, user),) = pushes.sent
    assert (message_id, title, body, user) == (card.id, 'Evening digest', '2 things wait for you.',
                                               'usr_1')  # fmt: skip

    # The next digest names the board, so the brain adds a version; a new doc does not replace it.
    clock.now = EVENING + timedelta(days=1)  # the evening digest's next time
    run(sched.tick())
    assert sent(frames)[-1]['data']['artifact_id'] == board
    other = publish(engine, 'Shopping list', at=clock.now)
    publish(engine, at=clock.now, artifact_id=board, summary='Quiet day.')
    digests.check()
    assert digests.boards == {AGENT: board} and other != board
    assert pushes.sent[-1][2] == 'Quiet day.'


def test_a_digest_not_published_in_time_is_said_once_in_plain_words(
    engine: Engine, digests: Digests, sched: Scheduler, clock: Clock, pushes: Pushes
) -> None:
    seed(engine)
    digests.ensure_builtins()
    due(engine, 'evening', 'usage')
    run(sched.tick())
    clock.now = EVENING + DEADLINE - timedelta(seconds=1)
    digests.check()
    assert messages(engine) == []
    clock.go(seconds=1)
    digests.check()
    digests.check()
    (line,) = messages(engine)
    assert line.role == MessageRole.system
    assert line.body_json['text'] == (
        'The evening digest was not written: Marcel did not publish it within 30 minutes. '
        'In short: 2 things wait for you; 1 thread failed; 1 thread is working; 1 finished; '
        '1 pull request is ready to merge; the plan is at 62 % of the week.'
    )
    assert [p[1] for p in pushes.sent] == ['Evening digest']
    # A digest published after that is still recognised: it becomes the board and its card is
    # posted, without a second push.
    clock.go(hours=2)
    late = publish(engine, at=clock.now)
    digests.check()
    assert digests.boards == {AGENT: late} and digests.pending == []
    assert [m.kind for m in messages(engine)] == [MessageKind.text, MessageKind.artifact]
    assert len(pushes.sent) == 1


def test_a_late_digest_is_recognised_until_the_next_one_of_its_kind(
    engine: Engine, digests: Digests, sched: Scheduler, clock: Clock
) -> None:
    digests.ensure_builtins()
    due(engine, 'evening')
    run(sched.tick())
    clock.go(minutes=31)
    digests.check()
    assert [p.late for p in digests.pending] == [True]
    clock.now = EVENING + timedelta(days=1)  # the next evening digest replaces it
    run(sched.tick())
    assert [(p.late, p.title) for p in digests.pending] == [
        (False, 'Evening digest — Tuesday 13 October')
    ]
    # With no next digest (paused), a late one is forgotten after LATE_FOR.
    digests.pending[0].late = True
    clock.now += digest.LATE_FOR
    digests.check()
    assert digests.pending == []


def test_an_unrelated_doc_published_meanwhile_is_not_taken_for_the_digest(
    engine: Engine, digests: Digests, sched: Scheduler, frames: ChannelOutbox, clock: Clock,
    pushes: Pushes,
) -> None:  # fmt: skip
    digests.ensure_builtins()
    due(engine, 'evening')
    run(sched.tick())
    assert sent(frames)[-1]['data']['title'] == 'Evening digest — Monday 12 October'
    publish(engine, 'Shopping list', at=clock.now + timedelta(minutes=1))
    digests.check()
    assert digests.boards == {} and messages(engine) == [] and len(digests.pending) == 1
    real = publish(engine, at=clock.now + timedelta(minutes=2))
    digests.check()
    assert digests.boards == {AGENT: real}


def test_waiting_digests_and_the_board_survive_a_restart(
    engine: Engine, registry: TaskRegistry, digests: Digests, sched: Scheduler, clock: Clock,
    tmp_path: Path,
) -> None:  # fmt: skip
    digests.ensure_builtins()
    digests.boards[AGENT] = 'art_gone'
    due(engine, 'evening', 'usage')
    run(sched.tick())
    again = Digests(engine, Facts(engine), clock=clock, state_path=tmp_path / digest.STATE_FILE)
    assert [p.digest for p in again.pending] == ['evening']
    assert again.boards == {}  # the board it knew is gone, so it was forgotten
    (tmp_path / digest.STATE_FILE).write_text('{not json')
    assert Digests(engine, Facts(engine), state_path=tmp_path / digest.STATE_FILE).pending == []


def test_the_loop_looks_and_stops(
    engine: Engine, digests: Digests, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(digest, 'TICK_S', 0.01)
    calls: list[str] = []

    def check() -> None:
        calls.append('check')
        if len(calls) == 1:
            raise RuntimeError('boom')  # a failed look does not end the loop

    monkeypatch.setattr(digests, 'check', check)

    async def go() -> None:
        digests.start()
        while len(calls) < 2:
            await asyncio.sleep(0.01)
        await digests.stop()
        await digests.stop()  # stopping twice is fine

    run(go())
    assert len(calls) >= 2 and len(schedules(engine)) == 4


# The app ------------------------------------------------------------------------------------


def test_the_app_sends_digest_events_and_makes_builtins_at_start(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, db_path=tmp_path / 'm.db', tz='Europe/London')
    app = create_app(settings)
    with TestClient(app):
        pass  # the first start makes the database
    with Session(app.state.engine) as s:
        s.add(Agent(id=AGENT, owner_user_id='usr_1', name='Marcel', animal='giraffe',
                    palette='pink', seed='1'))  # fmt: skip
        s.commit()
    app = create_app(settings)
    with TestClient(app):
        assert app.state.scheduler.brain_frame == app.state.digests.frame
        rows = schedules(app.state.engine)
        assert len(rows) == 4 and {r.tz for r in rows.values()} == {'Europe/London'}


def test_the_time_zone_setting(tmp_path: Path) -> None:
    assert load_settings({'MARCEL_CONFIG': str(tmp_path / 'none.toml')}).tz == 'Europe/Brussels'
    env = {'MARCEL_CONFIG': str(tmp_path / 'none.toml'), 'MARCEL_TZ': 'America/New_York'}
    assert load_settings(env).tz == 'America/New_York'
    with pytest.raises(SettingsError, match='Europe/Brussels'):
        load_settings({**env, 'MARCEL_TZ': 'Mars/Base'})


# One time zone: the `tz` setting --------------------------------------------------------------


def test_the_usage_guard_says_times_in_the_tz_setting_not_the_process_zone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv('TZ', 'Asia/Tokyo')  # the process's own zone must not matter
    settings = Settings(data_dir=tmp_path, db_path=tmp_path / 'm.db', tz='America/New_York')
    guard = create_app(settings).state.usage_guard
    assert guard.tz == ZoneInfo('America/New_York')
    assert guard._clock(datetime(2026, 10, 12, 19, 0, tzinfo=UTC)).endswith('15:00')
    assert UsageGuard().tz is UTC  # never "whatever the process is set to"


def test_builtin_digests_follow_a_changed_tz_unless_the_owner_edited_them(
    engine: Engine, registry: TaskRegistry, digests: Digests, sched: Scheduler, clock: Clock,
    tmp_path: Path,
) -> None:  # fmt: skip
    digests.ensure_builtins()
    sched.update(builtin_id('weekly', AGENT), {'cron': '0 9 * * 6'})  # the owner moved it
    sched.update(builtin_id('usage', AGENT), {'tz': 'Asia/Tokyo'})  # and this one
    sched.update(builtin_id('morning', AGENT), {'paused': True})  # pausing is not an edit
    with Session(engine) as s:  # an evening row from before the hub kept records of its own
        evening = s.get(Schedule, builtin_id('evening', AGENT))
        assert evening is not None
        evening.tz = 'Europe/Paris'
        s.add(evening)
        s.commit()
    digests.made.pop(builtin_id('evening', AGENT))
    digests.save()

    london = Digests(engine, Facts(engine), clock=clock, tz='Europe/London',
                     state_path=tmp_path / digest.STATE_FILE)  # fmt: skip
    assert london.ensure_builtins() == 0
    rows = schedules(engine)
    tz = {d: rows[builtin_id(d, AGENT)].tz for d in ('morning', 'evening', 'weekly', 'usage')}
    assert tz == {'morning': 'Europe/London', 'evening': 'Europe/London',
                  'weekly': TZ, 'usage': 'Asia/Tokyo'}  # fmt: skip
    assert rows[builtin_id('morning', AGENT)].next_run_at is None  # still paused
    evening_at = rows[builtin_id('evening', AGENT)].next_run_at
    assert evening_at is not None and evening_at.replace(tzinfo=UTC) == datetime(
        2026, 10, 12, 20, 0, tzinfo=UTC
    )  # 21:00 in London
    # Started again with the same setting: nothing moves.
    again = Digests(engine, Facts(engine), clock=clock, tz='Europe/London',
                    state_path=tmp_path / digest.STATE_FILE)  # fmt: skip
    again.ensure_builtins()
    assert schedules(engine)[builtin_id('evening', AGENT)].next_run_at == evening_at


# The brain down or restarting when a digest is due (NUC, 2026-10-14) -------------------------


@pytest.fixture
def waiting_digests(digests: Digests, frames: ChannelOutbox) -> Digests:
    digests.waiting = digest.undelivered(frames)  # as `install` wires it
    return digests


def brain_receives_everything(engine: Engine, frames: ChannelOutbox) -> None:
    """The brain comes back and its session acks every frame waiting for it."""
    target = conversation_target(frames.main_conversation(AGENT))
    for f in frames.pending(target):
        frames.ack(target, f['seq'])


def test_a_digest_waits_in_the_outbox_while_the_brain_is_away(
    engine: Engine, waiting_digests: Digests, sched: Scheduler, frames: ChannelOutbox,
    clock: Clock,
) -> None:  # fmt: skip
    seed(engine)
    waiting_digests.ensure_builtins()
    due(engine, 'evening', 'usage')
    run(sched.tick())
    (p,) = waiting_digests.pending
    target = conversation_target(frames.main_conversation(AGENT))
    queued = [f for f in frames.pending(target) if f.get('kind') == 'digest_due']
    assert [f['data']['digest_id'] for f in queued] == [p.digest_id]  # delivered when it is back


def test_the_deadline_counts_from_when_the_brain_gets_the_digest(
    engine: Engine, waiting_digests: Digests, sched: Scheduler, frames: ChannelOutbox,
    clock: Clock, pushes: Pushes,
) -> None:  # fmt: skip
    seed(engine)
    waiting_digests.ensure_builtins()
    due(engine, 'evening', 'usage')
    run(sched.tick())
    clock.go(hours=2)  # the brain is away: no "not written" line it could not help
    waiting_digests.check()
    assert messages(engine) == [] and pushes.sent == []
    brain_receives_everything(engine, frames)
    clock.go(minutes=29)
    waiting_digests.check()
    assert messages(engine) == []  # its full 30 minutes, counted from its return
    clock.go(minutes=2)
    waiting_digests.check()
    (line,) = messages(engine)
    assert 'Marcel did not publish it within 30 minutes' in line.body_json['text']


def test_after_three_hours_away_the_hub_says_so_once(
    engine: Engine, waiting_digests: Digests, sched: Scheduler, clock: Clock, pushes: Pushes
) -> None:
    seed(engine)
    waiting_digests.ensure_builtins()
    due(engine, 'evening', 'usage')
    run(sched.tick())
    clock.go(hours=3, minutes=1)
    waiting_digests.check()
    waiting_digests.check()
    (line,) = messages(engine)
    assert line.body_json['text'].startswith(
        'The evening digest was not written: Marcel has not been reachable for 3 hours; it gets '
        'the evening digest when it is back. In short: '
    )
    assert len(pushes.sent) == 1
