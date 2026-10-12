"""Contract 0.6 in the hub (S-04.14): a worker's `report(note)`, `pr.refreshed_at`, a digest's
`digest_id` and `in_reply_to`, and the schedule error codes the app API now names.

Each control here has a test that fails without it: a note goes only to its own task's project
(the file comes from the project's slug, never from the worker), it is one line, a task sends at
most `MAX_NOTES`, a notes file never passes 1 MB, appends at the same time lose nothing, an
`in_reply_to` the hub never sent is refused, and a version that answers another digest is never
taken for this one."""

# ruff: noqa: F811  (the digest and GitHub fixtures are imported, then named as arguments)

import json
import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml
from sqlalchemy.engine import Engine
from sqlmodel import Session, select
from test_brain_api import Brain
from test_channel_fakes import TO_SESSION, ChannelHub, channel_hub
from test_digest import (  # noqa: F401  (fixtures)
    AGENT,
    EVENING,
    Clock,
    clock,
    digests,
    due,
    frames,
    messages,
    publish,
    pushes,
    sched,
    sent,
    usage,
)
from test_github import FakeGitHub, Rig, gh, rig  # noqa: F401  (fixtures)
from test_tasks_fakes import (  # noqa: F401  (fixtures)
    outbox_fixture,
    publisher_fixture,
    registry_fixture,
    run,
    runner_fixture,
    validator,
)

from marcel_hub.channel.gateway import MAX_NOTES
from marcel_hub.channel.outbox import ChannelOutbox
from marcel_hub.digest import Digests, Pending
from marcel_hub.memory import MAX_FILE_BYTES, MemoryFileTooBig, MemoryRepo, TooManyAppends
from marcel_hub.models import (
    Agent,
    ArtifactVersion,
    MessageKind,
    Project,
    Task,
    TaskState,
    inbox_id,
)
from marcel_hub.schedule import Scheduler

CONTRACTS = Path(__file__).resolve().parents[2] / 'contracts'
SYSTEM_EVENT = TO_SESSION['SystemEvent']
MEMORY_FILE = validator('app-api.yaml#/components/schemas/MemoryFile')
ARTIFACT = validator('app-api.yaml#/components/schemas/Artifact')
UPDATED = validator('events.schema.json#/$defs/ArtifactUpdated')
FACT = 'The Luminus contract ends on 1 March 2027.'


@pytest.fixture
def hub(tmp_path: Path) -> Iterator[ChannelHub]:
    with channel_hub(tmp_path) as hub:
        yield hub


# 1. report(note) ------------------------------------------------------------------------------


def project(hub: ChannelHub, name: str) -> dict[str, Any]:
    res = hub.client.post('/api/projects', json={'agent_id': hub.agent_id, 'name': name})
    assert res.status_code == 201, res.text
    return res.json()


def memory(hub: ChannelHub) -> MemoryRepo:
    return hub.app.state.memory


def notes(hub: ChannelHub, slug: str) -> str:
    path = memory(hub).root / 'projects' / f'{slug}.md'
    return path.read_text() if path.exists() else ''


def subjects(hub: ChannelHub) -> list[str]:
    return memory(hub)._out('log', '--format=%s').splitlines()


def note(hub: ChannelHub, sid: str, *texts: str) -> list[dict[str, Any]]:
    with hub.connect() as ch:
        ch.hello(sid)
        return [ch.call('report', kind='note', text=text) for text in texts]


def test_a_note_lands_in_its_projects_notes_with_a_date_and_the_thread(hub: ChannelHub) -> None:
    energy = project(hub, 'Energy contracts')
    garden = project(hub, 'Garden')
    task_id, sid = hub.task(title='Compare energy offers', project_id=energy['id'])
    before = hub.registry.get(task_id).state
    garden_before = notes(hub, garden['slug'])

    [ack] = note(hub, sid, FACT)

    assert 'error' not in ack
    today = datetime.now(UTC).astimezone().date()
    lines = notes(hub, 'energy-contracts').splitlines()
    assert lines[0] == '# Energy contracts'  # the notes the project was made with stay
    last = lines[-1]
    assert last.endswith(f' {FACT} (from Compare energy offers)')
    stamp = last.removeprefix('- ').split(' ', 1)[0]
    assert abs(datetime.fromisoformat(stamp).date() - today) <= timedelta(days=1)
    assert subjects(hub)[0] == f'memory: projects/energy-contracts.md (worker {task_id})'
    # Never another project's notes.
    assert notes(hub, garden['slug']) == garden_before
    assert FACT not in notes(hub, 'inbox')
    # No milestone, no state change, no progress line.
    assert hub.messages() == []
    assert hub.registry.get(task_id).state == before
    assert hub.task_events(task_id, 'progress') == []
    # The app reads it as the agent's.
    res = hub.client.get('/api/memory/files/projects/energy-contracts.md')
    MEMORY_FILE.validate(res.json())
    assert res.json()['updated_by'] == 'agent'


def test_an_unfiled_tasks_note_goes_to_the_inbox_notes(hub: ChannelHub) -> None:
    _task_id, sid = hub.task(title='Check disk usage')
    [ack] = note(hub, sid, 'The NUC disk is 1 TB.')
    assert 'error' not in ack
    assert notes(hub, 'inbox').endswith('The NUC disk is 1 TB. (from Check disk usage)\n')


def test_a_note_is_one_line_whatever_the_worker_sends(hub: ChannelHub) -> None:
    _task_id, sid = hub.task(title='Line\nbreaks\tin a title')
    [ack] = note(hub, sid, 'First line.\n\n# Not a heading\r\n- not an item')
    assert 'error' not in ack
    assert (
        notes(hub, 'inbox')
        .splitlines()[-1]
        .endswith('First line. # Not a heading - not an item (from Line breaks in a title)')
    )


def test_the_eleventh_note_of_a_task_is_refused_in_plain_words(hub: ChannelHub) -> None:
    task_id, sid = hub.task()
    other_id, other_sid = hub.task(title='Another thread')
    acks = note(hub, sid, *[f'Fact {n}.' for n in range(1, MAX_NOTES + 2)])

    assert all('error' not in ack for ack in acks[:MAX_NOTES])
    refused = acks[MAX_NOTES]['error']
    assert refused['code'] == 'too_many_notes'
    assert refused['message'] == (
        'Marcel did not keep this note: this thread already sent 10 notes, the most one thread '
        'may. Put the fact in your done summary instead.'
    )
    kept = [line for line in notes(hub, 'inbox').splitlines() if line.startswith('- ')]
    assert len(kept) == MAX_NOTES and 'Fact 11.' not in notes(hub, 'inbox')
    assert sum(s.endswith(f'(worker {task_id})') for s in subjects(hub)) == MAX_NOTES
    # The cap is per task: another thread still may.
    [ack] = note(hub, other_sid, 'Fact from elsewhere.')
    assert 'error' not in ack and other_id != task_id


def test_a_note_never_follows_a_slug_out_of_the_notes_folder(hub: ChannelHub) -> None:
    """The file comes from the project's slug; one that is not a slug (written past the API) is
    refused before any path is made from it."""
    task_id, sid = hub.task()
    with Session(hub.engine) as s:
        evil = Project(agent_id=hub.agent_id, slug='../../hub-secret', name='Evil')
        s.add(evil)
        s.commit()
        row = s.get(Task, task_id)
        assert row is not None
        row.project_id = evil.id
        s.add(row)
        s.commit()
    secret_before = (hub.tmp_path / 'hub-secret').read_text()

    [ack] = note(hub, sid, FACT)

    assert ack['error']['code'] == 'memory_unavailable'
    assert ack['error']['message'] == (
        'Marcel did not keep this note: its project has no notes file. '
        'Put the fact in your done summary instead.'
    )
    assert (hub.tmp_path / 'hub-secret').read_text() == secret_before
    assert not any(FACT in p.read_text() for p in hub.tmp_path.rglob('*.md'))


def test_a_full_notes_file_refuses_the_note(hub: ChannelHub) -> None:
    _task_id, sid = hub.task()
    memory(hub).write('projects/inbox.md', 'x' * (MAX_FILE_BYTES - 10) + '\n', None, 'app')
    [ack] = note(hub, sid, FACT)
    assert ack['error']['code'] == 'notes_full'
    assert FACT not in notes(hub, 'inbox')


def test_a_note_without_a_memory_folder_is_refused(hub: ChannelHub) -> None:
    _task_id, sid = hub.task()
    hub.gateway.memory = None
    [ack] = note(hub, sid, FACT)
    assert ack['error']['code'] == 'memory_unavailable'


def test_a_note_git_cannot_save_is_refused_and_logged(
    hub: ChannelHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    _task_id, sid = hub.task()
    monkeypatch.setattr('marcel_hub.memory.GIT', '/nonexistent/git')
    [ack] = note(hub, sid, FACT)
    assert ack['error']['code'] == 'memory_unavailable'
    assert 'could not be saved just now' in ack['error']['message']


def test_a_note_carries_artifacts_but_no_milestone(hub: ChannelHub) -> None:
    task_id, sid = hub.task()
    with hub.connect() as ch:
        ch.hello(sid)
        link = {'kind': 'link', 'title': 'Offer', 'url': 'https://example.com/offer'}
        ack = ch.call('report', kind='note', text=FACT, artifact=link)
    assert 'error' not in ack
    assert [m.kind for m in hub.messages()] == [MessageKind.artifact]  # its card, no milestone
    assert hub.registry.get(task_id).state == TaskState.working


# The memory side: `MemoryRepo.append` ---------------------------------------------------------


@pytest.fixture
def repo(tmp_path: Path) -> MemoryRepo:
    return MemoryRepo(tmp_path / 'memory')


def test_appends_at_the_same_time_lose_nothing_and_keep_the_cap(repo: MemoryRepo) -> None:
    errors: list[Exception] = []

    def add(n: int) -> None:
        try:
            repo.append('projects/inbox.md', f'- fact {n}', 'worker tsk_1', max_appends=10)
        except TooManyAppends as exc:
            errors.append(exc)

    threads = [threading.Thread(target=add, args=(n,)) for n in range(14)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    lines = (repo.root / 'projects' / 'inbox.md').read_text().splitlines()
    assert len(lines) == 10 and len(set(lines)) == 10
    assert len(errors) == 4


def test_append_adds_one_line_after_the_last(repo: MemoryRepo) -> None:
    repo.write('projects/garden.md', '# Garden', None, 'app')  # no newline at the end
    repo.append('projects/garden.md', '- 2026-10-13 Water on Fridays.', 'worker tsk_2')
    text = (repo.root / 'projects' / 'garden.md').read_text()
    assert text == '# Garden\n- 2026-10-13 Water on Fridays.\n'


@pytest.mark.parametrize(
    ('line', 'by'),
    [('two\nlines', 'worker tsk_1'), ('cr\rline', 'worker tsk_1'), ('   ', 'worker tsk_1'),
     ('fine', 'worker tsk 1'), ('fine', 'worker tsk_1)\n\nInjected'), ('fine', 'app')],
)  # fmt: skip
def test_append_refuses_more_than_one_line_or_a_strange_writer(
    repo: MemoryRepo, line: str, by: str
) -> None:
    with pytest.raises(ValueError, match='append'):
        repo.append('projects/inbox.md', line, by)
    assert not (repo.root / 'projects' / 'inbox.md').exists()


def test_append_never_passes_the_file_cap(repo: MemoryRepo) -> None:
    repo.write('projects/inbox.md', 'x' * (MAX_FILE_BYTES - 5), None, 'app')
    with pytest.raises(MemoryFileTooBig):
        repo.append('projects/inbox.md', '- too much', 'worker tsk_1')
    assert (repo.root / 'projects' / 'inbox.md').stat().st_size == MAX_FILE_BYTES - 5


def test_append_refuses_a_path_outside_the_memory_folder(repo: MemoryRepo) -> None:
    from marcel_hub.memory import InvalidMemoryPath

    with pytest.raises(InvalidMemoryPath):
        repo.append('projects/../../escape.md', '- x', 'worker tsk_1')


# 2. pr.refreshed_at ---------------------------------------------------------------------------


class Ticks:
    """`utcnow` for the store: one second later on every call."""

    def __init__(self) -> None:
        self.now = datetime(2026, 10, 13, 8, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        self.now += timedelta(seconds=1)
        return self.now


def test_every_good_refresh_is_stamped_and_rides_on_the_next_update(
    rig: Rig, gh: FakeGitHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr('marcel_hub.artifacts.store.utcnow', Ticks())
    art = rig.add_pr()
    assert 'refreshed_at' not in rig.pr(art)  # absent before the first good refresh

    rig.later(0)
    first = rig.pr(art)['refreshed_at']
    [update] = rig.events.updates()
    assert update['artifact']['pr']['refreshed_at'] == first

    rig.later()  # nothing changed on GitHub: a newer time, no event of its own
    second = rig.pr(art)['refreshed_at']
    assert second > first and len(rig.events.updates()) == 1
    ARTIFACT.validate(rig.store.to_api(rig.store.get(art)))

    gh.answer = 502  # a failed refresh keeps the last good time
    rig.later()
    assert rig.pr(art)['refreshed_at'] == second
    assert rig.events.updates()[-1]['artifact']['pr']['refreshed_at'] == second  # the note's

    gh.answer = 200
    gh.reviews = [{'state': 'APPROVED', 'user': {'login': 'shaun'}}]
    rig.later()
    third = rig.pr(art)['refreshed_at']
    assert third > second
    assert rig.events.updates()[-1]['artifact']['pr']['refreshed_at'] == third


# 3. digest_id and in_reply_to -----------------------------------------------------------------


def test_the_digest_event_carries_its_id(
    engine: Engine, digests: Digests, sched: Scheduler, frames: ChannelOutbox
) -> None:
    digests.ensure_builtins()
    due(engine, 'evening')
    run(sched.tick())
    frame = sent(frames)[-1]
    SYSTEM_EVENT.validate(frame)
    digest_id = frame['data']['digest_id']
    assert digest_id.startswith('dig_') and len(digest_id) <= 80
    assert [p.digest_id for p in digests.pending] == [digest_id]
    assert digests.knows(AGENT, digest_id) and not digests.knows('agt_other', digest_id)
    assert not digests.knows(AGENT, 'dig_never_sent')


def answer(engine: Engine, artifact_id: str, digest_id: str) -> None:
    with Session(engine) as s:
        rows = s.exec(select(ArtifactVersion).where(ArtifactVersion.artifact_id == artifact_id))
        version = max(rows, key=lambda v: v.version)
        version.in_reply_to = digest_id
        s.add(version)
        s.commit()


def test_a_digest_is_known_by_in_reply_to_whatever_its_title(
    engine: Engine, digests: Digests, sched: Scheduler, frames: ChannelOutbox, clock: Clock,
    tmp_path: Path,
) -> None:  # fmt: skip
    digests.ensure_builtins()
    due(engine, 'evening')
    run(sched.tick())
    digest_id = sent(frames)[-1]['data']['digest_id']

    doc = publish(engine, 'Tonight, in short', at=clock.now)  # not the asked title
    digests.check()
    assert digests.boards == {}  # without in_reply_to it is not the digest
    answer(engine, doc, digest_id)
    digests.check()
    assert digests.boards == {AGENT: doc} and digests.pending == []
    assert [m.kind for m in messages(engine)] == [MessageKind.artifact]
    # Answered ids stay known (a corrected version may name one), also after a restart.
    again = Digests(engine, digests.facts, state_path=tmp_path / 'digests.json', clock=clock)
    assert again.knows(AGENT, digest_id)


def test_a_version_answering_another_digest_is_not_taken_by_its_title(
    engine: Engine, digests: Digests, sched: Scheduler, frames: ChannelOutbox, clock: Clock
) -> None:
    digests.ensure_builtins()
    due(engine, 'evening')
    run(sched.tick())
    stray = publish(engine, at=clock.now)  # the asked title, but it answers another digest
    answer(engine, stray, 'dig_another')
    digests.check()
    assert digests.boards == {} and len(digests.pending) == 1
    real = publish(engine, at=clock.now)  # the title alone still works (the fallback)
    digests.check()
    assert digests.boards == {AGENT: real}


def test_only_the_last_answered_digests_stay_known(
    engine: Engine, digests: Digests, sched: Scheduler, frames: ChannelOutbox, clock: Clock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    monkeypatch.setattr('marcel_hub.digest.KNOWN_ANSWERED', 1)
    digests.ensure_builtins()
    ids = []
    for day in range(2):
        clock.now = EVENING + timedelta(days=day)
        if day == 0:
            due(engine, 'evening')
        run(sched.tick())
        ids.append(sent(frames)[-1]['data']['digest_id'])
        answer(engine, publish(engine, 'Tonight', at=clock.now), ids[-1])
        digests.check()
    assert ids[0] != ids[1]
    assert not digests.knows(AGENT, ids[0]) and digests.knows(AGENT, ids[1])


def brain_digest(hub: ChannelHub, digest_id: str, agent_id: str | None = None) -> None:
    hub.app.state.digests.pending.append(
        Pending(agent_id or hub.agent_id, 'evening', datetime.now(UTC).isoformat(),
                (datetime.now(UTC) + timedelta(minutes=30)).isoformat(), 'Quiet.',
                'Evening digest — Tuesday 13 October', digest_id=digest_id)
    )  # fmt: skip


def test_the_brain_publishes_a_digest_with_in_reply_to(hub: ChannelHub) -> None:
    brain = Brain(hub, hub.brain())
    brain_digest(hub, 'dig_evening_1')
    body = {'kind': 'link', 'title': 'Tonight', 'in_reply_to': 'dig_evening_1',
            'url': 'https://example.com/digest'}  # fmt: skip
    res = brain.post('/artifacts', body)
    assert res.status_code == 201, res.text
    ARTIFACT.validate(res.json())
    with Session(hub.engine) as s:
        version = s.get(ArtifactVersion, (res.json()['id'], 1))
        assert version is not None and version.in_reply_to == 'dig_evening_1'


@pytest.mark.parametrize('digest_id', ['dig_never_sent', 'dig_other_agents'])
def test_an_in_reply_to_the_hub_never_sent_this_brain_is_refused(
    hub: ChannelHub, digest_id: str
) -> None:
    with Session(hub.engine) as s:
        other = Agent(owner_user_id='usr_other', name='Odile', animal='owl', palette='blue',
                      seed='2')  # fmt: skip
        s.add(other)
        s.commit()
        inbox_id(s.connection(), other.id)
        s.commit()
        other_id = other.id
    brain_digest(hub, 'dig_other_agents', other_id)
    brain = Brain(hub, hub.brain())
    count = len(hub.events('artifact.created'))
    res = brain.post('/artifacts', {'kind': 'link', 'title': 'Tonight', 'in_reply_to': digest_id,
                                    'url': 'https://example.com/digest'})  # fmt: skip
    assert res.status_code == 404
    assert res.json()['code'] == 'digest_not_found'
    assert digest_id in res.json()['message']
    assert len(hub.events('artifact.created')) == count  # nothing stored


def test_without_digests_every_in_reply_to_is_unknown(hub: ChannelHub) -> None:
    from marcel_hub.brain_api import NoDigests

    hub.app.state.brain_ports.digests = NoDigests()
    res = Brain(hub, hub.brain()).post(
        '/artifacts', {'kind': 'link', 'title': 'T', 'in_reply_to': 'dig_x', 'url': 'https://e.x'}
    )
    assert res.json()['code'] == 'digest_not_found'


# 4. Schedule error codes ----------------------------------------------------------------------


def test_the_schedule_codes_the_hub_sends_are_the_ones_the_contract_names() -> None:
    api = yaml.safe_load((CONTRACTS / 'app-api.yaml').read_text())
    delete = api['paths']['/schedules/{schedule_id}']['delete']['responses']
    assert '`schedule_not_found`' in delete['404']['description']
    assert '`schedule_builtin`' in delete['409']['description']
    codes = api['components']['schemas']['Error']['properties']['code']['description']
    assert 'schedule_not_found' in codes and 'schedule_builtin' in codes


def test_deleting_a_schedule_answers_the_contract_codes(hub: ChannelHub) -> None:
    gone = hub.client.delete('/api/schedules/sch_nope')
    assert (gone.status_code, gone.json()['code']) == (404, 'schedule_not_found')
    hub.app.state.digests.ensure_builtins()
    builtin = f'sch_digest_morning_{hub.agent_id}'
    kept = hub.client.delete(f'/api/schedules/{builtin}')
    assert (kept.status_code, kept.json()['code']) == (409, 'schedule_builtin')
    assert json.loads(kept.text)['message']
