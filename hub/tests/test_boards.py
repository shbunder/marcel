"""Project boards (B-35): built by the hub from the project's tasks, approvals and items, with no
brain turn; rebuilt at most once per 10 s per project; a new version only when the content changed.

Every board is checked against contracts/dashboard.schema.json. The real app runs underneath (the
channel gateway, the artifact store, the event log); only the clock is fake, and the keeper's
background loop is held back (conftest), so each test decides when boards are rebuilt.
"""

import asyncio
import json
import logging
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from sqlmodel import Session, select
from test_channel_fakes import OWNER, ChannelHub, channel_hub, permission
from test_tasks_fakes import run, validator

from marcel_hub import boards
from marcel_hub.artifacts import ArtifactNotFound, ArtifactStore
from marcel_hub.boards import DEBOUNCE_S, MAX_NOTES, BoardKeeper
from marcel_hub.models import (
    Artifact,
    ArtifactKind,
    ArtifactVersion,
    TaskEventType,
    TaskState,
)
from marcel_hub.projects import ProjectStore

CONTRACTS = Path(__file__).resolve().parents[2] / 'contracts'
DASHBOARD = Draft202012Validator(json.loads((CONTRACTS / 'dashboard.schema.json').read_text()))
ARTIFACT = validator('app-api.yaml#/components/schemas/Artifact')
UPDATED = validator('events.schema.json#/$defs/ArtifactUpdated')


class Clock:
    """Seconds that pass only when a test says so. It starts at the real clock's time, so what
    the hub marked at start is due now."""

    def __init__(self) -> None:
        self.now = time.monotonic()

    def __call__(self) -> float:
        return self.now


class Boards:
    """The app's keeper, its store and a fake clock, with helpers to read a board."""

    def __init__(self, hub: ChannelHub) -> None:
        self.hub = hub
        self.clock = Clock()
        self.keeper: BoardKeeper = hub.app.state.boards
        self.keeper.clock = self.clock
        self.store: ArtifactStore = hub.app.state.artifacts
        self.projects: ProjectStore = hub.app.state.projects
        self.inbox = self.projects.inbox(hub.agent_id)

    def later(self, seconds: float) -> list[str]:
        """Let `seconds` pass and rebuild what is due."""
        self.clock.now += seconds
        return self.keeper.flush()

    def settle(self) -> None:
        """Rebuild everything that is marked, as if enough time passed."""
        self.later(2 * DEBOUNCE_S)

    def board_id(self, project_id: str | None = None) -> str:
        return self.projects.get(project_id or self.inbox.id).board_artifact_id

    def board(self, project_id: str | None = None, version: int | None = None) -> dict[str, Any]:
        data, content_type, trusted = self.store.content(self.board_id(project_id), version)
        assert content_type == 'application/json' and trusted is False
        doc = json.loads(data)
        DASHBOARD.validate(doc)
        return doc

    def section(self, title: str, project_id: str | None = None) -> list[dict[str, Any]]:
        [found] = [w for w in self.board(project_id)['widgets'] if w.get('title') == title]
        return found['widgets']

    def texts(self, title: str, project_id: str | None = None) -> list[str]:
        return [i['text'] for w in self.section(title, project_id) for i in w['items']]

    def versions(self, project_id: str | None = None) -> int:
        return self.store.get(self.board_id(project_id)).current_version


@pytest.fixture
def hub(tmp_path: Path) -> Iterator[ChannelHub]:
    with channel_hub(tmp_path) as hub:
        yield hub


@pytest.fixture
def b(hub: ChannelHub) -> Boards:
    found = Boards(hub)
    assert found.keeper.build(found.inbox.id) is True  # the inbox's first build
    return found


# Done when: an open approval appears under "waiting on you" and disappears when answered. ---


def test_an_open_approval_waits_on_you_until_it_is_answered(hub: ChannelHub, b: Boards) -> None:
    task_id, _ = hub.task(title='Check disk usage')
    assert b.section('Waiting on you') == []

    run(hub.gateway.permission_request(task_id, permission(description='Run docker df')))
    b.settle()
    [item] = b.section('Waiting on you')[0]['items']
    assert item['text'] == 'Run docker df'
    assert item['detail'].startswith('Check disk usage · since ')
    assert 'Check disk usage' not in b.texts('In flight')  # it needs you: not "in flight"

    [card] = hub.gateway.approvals.open_on(task_id)
    run(hub.gateway.answer(card.id, True, None))
    b.settle()
    assert b.section('Waiting on you') == []
    assert b.texts('In flight') == ['Check disk usage']


def test_a_task_that_needs_you_without_a_card_waits_on_you_too(hub: ChannelHub, b: Boards) -> None:
    task_id, _ = hub.task()
    run(hub.registry.transition(task_id, TaskState.needs_you, 'asked', waiting_for='Which disk?'))
    b.settle()
    assert b.texts('Waiting on you') == ['Which disk?']


# Done when: an unchanged rebuild does not create a version. --------------------------------


def test_an_unchanged_rebuild_does_not_make_a_version(hub: ChannelHub, b: Boards) -> None:
    hub.task(title='Check disk usage')
    assert b.keeper.build(b.inbox.id) is True
    version = b.versions()
    events = len(hub.events('artifact.updated'))

    assert b.keeper.build(b.inbox.id) is False
    assert b.keeper.build(b.inbox.id) is False
    assert b.versions() == version
    assert len(hub.events('artifact.updated')) == events
    with Session(hub.engine) as s:
        rows = s.exec(select(ArtifactVersion).where(ArtifactVersion.artifact_id == b.board_id()))
        assert len(rows.all()) == version


def test_a_changed_board_is_a_new_version_and_the_old_one_stays(hub: ChannelHub, b: Boards) -> None:
    before = b.versions()
    hub.task(title='Check disk usage')
    b.keeper.mark(b.inbox.id)
    b.settle()
    assert b.versions() == before + 1
    assert b.texts('In flight') == ['Check disk usage']
    assert b.board(version=before)['widgets'][1]['widgets'] == []  # the old one, unchanged
    [event] = hub.events('artifact.updated')[-1:]
    UPDATED.validate(event)
    assert event['data']['artifact']['kind'] == 'board'
    assert event['data']['version'] == {
        **event['data']['version'],
        'version': before + 1,
        'author': 'the hub',
    }
    ARTIFACT.validate(hub.client.get(f'/api/artifacts/{b.board_id()}').json())


# Done when: the debounce holds under a burst. ----------------------------------------------


def test_a_burst_of_changes_rebuilds_the_board_once_per_ten_seconds(
    hub: ChannelHub, b: Boards
) -> None:
    task_id, _ = hub.task(state=TaskState.starting)
    built: list[float] = []
    real_build = b.keeper.build

    def counting(project_id: str) -> bool:
        built.append(b.clock.now)
        return real_build(project_id)

    b.keeper.build = counting  # type: ignore[method-assign]
    start = b.clock.now
    run(hub.registry.report_state(task_id, TaskState.working, 'started'))
    for n in range(40):  # a change every quarter second for ten seconds
        hub.registry.append_event(task_id, TaskEventType.progress, {'text': f'step {n}'})
        hub.registry._publish_task(task_id)
        b.later(0.25)
    assert built == [start + DEBOUNCE_S]  # one rebuild for the whole burst
    for _ in range(40):  # and the next burst waits ten seconds after that rebuild
        hub.registry._publish_task(task_id)
        b.later(0.25)
    assert built == [start + DEBOUNCE_S, start + 2 * DEBOUNCE_S]
    assert b.later(DEBOUNCE_S) == []  # nothing left to do


def test_a_rebuild_does_not_mark_its_own_board_again(hub: ChannelHub, b: Boards) -> None:
    hub.task(title='Check disk usage')
    b.keeper.mark(b.inbox.id)
    b.settle()
    assert hub.events('artifact.updated')  # the rebuild published its new version
    assert b.keeper.due() == {}  # and that did not schedule another one


def test_a_board_never_built_is_built_at_once_and_marks_wait(hub: ChannelHub) -> None:
    b = Boards(hub)
    b.keeper.mark_all()  # what the hub does at start
    assert b.later(0) == [b.inbox.id]
    b.keeper.mark(b.inbox.id)
    assert b.later(DEBOUNCE_S - 1) == []
    assert b.later(1) == [b.inbox.id]


# What a board shows. -----------------------------------------------------------------------


def test_in_flight_shows_each_task_with_its_last_step(hub: ChannelHub, b: Boards) -> None:
    task_id, _ = hub.task(title='Compare the offers')
    hub.registry.append_event(task_id, TaskEventType.progress, {'text': 'Collected the offers'})
    hub.registry.append_event(task_id, TaskEventType.progress, {'text': 'Comparing totals'})
    hub.task(title='Queued one', state=TaskState.queued)
    b.keeper.mark(b.inbox.id)
    b.settle()
    [items] = [w['items'] for w in b.section('In flight')]
    assert items == [
        {'text': 'Compare the offers', 'detail': 'Working · Comparing totals'},
        {'text': 'Queued one', 'detail': 'Waiting for a free slot'},
    ]


def test_recent_results_show_the_summary_and_the_items_at_their_version(
    hub: ChannelHub, b: Boards
) -> None:
    task_id, _ = hub.task(title='Compare the offers', state=TaskState.done,
                          summary='Luminus is cheapest.')  # fmt: skip
    art = b.store.publish_reported(
        {'kind': 'link', 'title': 'Offers', 'url': 'https://example.com/offers'}, task_id
    )
    b.settle()
    [result] = b.section('Recent results')
    assert result == {
        'type': 'list',
        'title': 'Compare the offers',
        'items': [
            {'text': 'Luminus is cheapest.'},
            {'text': 'Offers · v1', 'detail': 'Link', 'url': 'https://example.com/offers'},
        ],
    }
    b.store.publish_reported(
        {'kind': 'link', 'title': 'Offers', 'url': 'https://example.com/v2', 'artifact_id': art.id},
        task_id,
    )
    b.settle()
    assert b.texts('Recent results') == ['Luminus is cheapest.', 'Offers · v2']


def test_upcoming_is_empty_until_schedules_exist(b: Boards) -> None:
    [upcoming] = [w for w in b.board()['widgets'] if w.get('title') == 'Upcoming']
    assert upcoming == {'type': 'section', 'title': 'Upcoming',
                        'empty_text': 'Nothing scheduled.', 'widgets': []}  # fmt: skip


def test_a_busy_project_still_makes_a_valid_board(hub: ChannelHub, b: Boards) -> None:
    for n in range(30):
        task_id, _ = hub.task(title=f'Done {n}', state=TaskState.done, summary='ok')
        b.store.publish_reported({'kind': 'link', 'title': f'L{n}', 'url': 'https://a.b/'}, task_id)
        hub.task(title=f'Working {n}')
    for n in range(MAX_NOTES + 3):
        b.keeper.board_note(b.inbox.id, f'Note {n}')
    b.settle()
    doc = b.board()  # validated against dashboard.schema.json
    assert len(b.section('Recent results')) == boards.MAX_RESULTS
    assert len(b.texts('In flight')) == boards.MAX_ITEMS
    assert [w['text'] for w in doc['widgets'] if w['type'] == 'note'] == [
        f'Note {n}' for n in range(3, MAX_NOTES + 3)
    ]


def test_marcels_notes_are_added_to_the_board(hub: ChannelHub, b: Boards) -> None:
    b.keeper.board_note(b.inbox.id, '  One approval left; switch before 1 November.  ')
    assert b.inbox.id in b.keeper.due()
    b.settle()
    [note] = [w for w in b.board()['widgets'] if w['type'] == 'note']
    assert note['text'] == 'One approval left; switch before 1 November.'
    assert note['author'] == 'Marcel' and note['at'].endswith('Z')
    b.keeper.board_note(b.inbox.id, 'x' * 1000)
    b.settle()
    assert len(b.board()['widgets'][-1]['text']) == boards.NOTE_MAX
    with pytest.raises(ValueError, match='needs some text'):
        b.keeper.board_note(b.inbox.id, '  ')
    with pytest.raises(ArtifactNotFound):
        b.keeper.board_note('prj_nope', 'x')


def test_every_project_has_a_board_and_it_follows_a_rename(hub: ChannelHub, b: Boards) -> None:
    project = b.projects.create(OWNER, hub.agent_id, 'Energy contracts', goal='Cheaper gas.')
    assert project.id in b.keeper.due()  # project.created: built at once
    b.later(0)
    board = hub.client.get(f'/api/artifacts/{project.board_artifact_id}').json()
    ARTIFACT.validate(board)
    assert board['kind'] == 'board' and board['title'] == 'Energy contracts — board'
    assert board['project_id'] == project.id and board['updated_by'] == 'the hub'
    doc = b.board(project.id)
    assert doc['title'] == 'Energy contracts' and doc['subtitle'] == 'Cheaper gas.'
    assert [w.get('title') for w in doc['widgets']] == [
        'Waiting on you', 'In flight', 'Recent results', 'Upcoming',
    ]  # fmt: skip
    created = [e for e in hub.events('artifact.created') if e['data']['kind'] == 'board']
    assert [e['data']['id'] for e in created][-1] == project.board_artifact_id

    b.projects.update(project.id, OWNER, {'name': 'Energy'})
    b.settle()
    assert b.store.get(project.board_artifact_id).title == 'Energy — board'
    assert b.board(project.id)['title'] == 'Energy'
    assert b.versions(project.id) == 2


def test_a_board_read_before_its_first_build_is_built_on_the_spot(hub: ChannelHub) -> None:
    b = Boards(hub)
    res = hub.client.get(f'/api/artifacts/{b.board_id()}/content')
    assert res.status_code == 200
    DASHBOARD.validate(res.json())
    versions = hub.client.get(f'/api/artifacts/{b.board_id()}/versions').json()
    assert [v['version'] for v in versions] == [1]


def test_a_missing_board_row_is_made_again(hub: ChannelHub, b: Boards) -> None:
    with Session(hub.engine) as s:
        for row in s.exec(
            select(ArtifactVersion).where(ArtifactVersion.artifact_id == b.board_id())
        ):
            s.delete(row)
        s.flush()
        art = s.get(Artifact, b.board_id())
        s.delete(art)
        s.commit()
    b.keeper.build(b.inbox.id)
    assert b.store.get(b.board_id()).kind is ArtifactKind.board
    assert b.board()['title'] == 'Inbox'


def test_a_broken_board_does_not_stop_the_others(
    hub: ChannelHub, b: Boards, caplog: pytest.LogCaptureFixture
) -> None:
    other = b.projects.create(OWNER, hub.agent_id, 'Garden')
    real_build = b.keeper.build

    def build(project_id: str) -> bool:
        if project_id == b.inbox.id:
            raise RuntimeError('disk full')
        return real_build(project_id)

    b.keeper.build = build  # type: ignore[method-assign]
    b.keeper.mark(b.inbox.id)
    with caplog.at_level(logging.ERROR):
        b.settle()
    assert b.keeper.due() == {}
    assert 'Could not rebuild the board' in caplog.text
    assert b.board(other.id)['title'] == 'Garden'


def test_events_that_touch_no_board_are_ignored(hub: ChannelHub, b: Boards) -> None:
    b.keeper.observe(OWNER, 'approval.created', {'task_id': 'tsk_gone'})
    b.keeper.observe(OWNER, 'message.created', {'id': 'msg_1'})
    b.keeper.observe(OWNER, 'task.updated', {'id': 'tsk_1'})
    b.keeper.observe(OWNER, 'artifact.created', {'id': 'art_1', 'kind': 'link'})
    assert b.keeper.due() == {}


def test_a_broken_event_never_fails_the_event_log(
    hub: ChannelHub, b: Boards, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.ERROR):
        b.keeper.observe(OWNER, 'task.moved', {})  # no project ids at all
    assert 'Could not follow a task.moved event' in caplog.text


def test_an_unknown_project_has_nothing_to_build(b: Boards) -> None:
    assert b.keeper.build('prj_nope') is False


def test_the_keeper_runs_on_the_event_loop(hub: ChannelHub) -> None:
    b = Boards(hub)
    b.keeper.mark(b.inbox.id, at_once=True)

    async def go() -> None:
        b.keeper.start(tick=0.01)
        for _ in range(200):
            if not b.keeper.due():
                break
            await asyncio.sleep(0.01)
        await b.keeper.stop()
        await b.keeper.stop()  # stopping twice is fine

    asyncio.run(go())
    assert b.keeper.due() == {}
    assert b.store.stored_bytes(b.store.get(b.board_id())) is not None


def test_the_hub_has_no_brain_turn_for_a_board(hub: ChannelHub, b: Boards) -> None:
    hub.brain()
    hub.task(title='Check disk usage')
    b.keeper.mark(b.inbox.id)
    b.settle()
    assert b.texts('In flight') == ['Check disk usage']  # the board was rebuilt …
    assert hub.gateway.outbox.pending(f'conversation:{hub.main_id}') == []  # … and nothing woke
