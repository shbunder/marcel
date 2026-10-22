"""App API 0.5.0 through the real app (S-04.13): a message or an approval by id, pinning an item
to another project, "Ask about this" on a message, a worker's next steps, and a PR card's note.

Each control here has a test that fails without it: only the owner's messages, approvals and
items are read or moved; a board never moves; nothing moves into an archived project; an unknown
item on a message is refused; a worker's `next` past the contract's bounds is refused, not cut."""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlmodel import Session
from test_channel_fakes import APPROVAL, MESSAGE, ChannelHub, channel_hub, permission
from test_tasks_fakes import validator

from marcel_hub import db
from marcel_hub.models import Agent, Artifact, ChannelCall, MessageKind, TaskState, inbox_id

ARTIFACT = validator('app-api.yaml#/components/schemas/Artifact')
STRANGER = 'usr_stranger'


@pytest.fixture
def hub(tmp_path: Path) -> Iterator[ChannelHub]:
    with channel_hub(tmp_path) as hub:
        yield hub


def stranger(hub: ChannelHub) -> dict[str, str]:
    return {'Authorization': f'Bearer {hub.phone(STRANGER)}'}


def other_phone(hub: ChannelHub) -> dict[str, str]:
    """Another paired phone of the one person."""
    return {'Authorization': f'Bearer {hub.phone()}'}


def projects(hub: ChannelHub, agent_id: str | None = None) -> list[dict[str, Any]]:
    """The agent's live projects, the inbox last."""
    res = hub.client.get('/api/projects', params={'agent_id': agent_id or hub.agent_id})
    assert res.status_code == 200, res.text
    return res.json()


def new_project(hub: ChannelHub, name: str, agent_id: str | None = None) -> dict[str, Any]:
    res = hub.client.post(
        '/api/projects', json={'agent_id': agent_id or hub.agent_id, 'name': name}
    )
    assert res.status_code == 201, res.text
    return res.json()


def other_agent(hub: ChannelHub, owner: str) -> str:
    with Session(hub.engine) as s:
        agent = Agent(owner_user_id=owner, name='Other', animal='giraffe', palette='pink', seed='2')
        s.add(agent)
        s.commit()
        return agent.id


def item(hub: ChannelHub, project_id: str | None = None) -> str:
    """A link item, filed in the inbox (or in `project_id`)."""
    art = hub.app.state.artifacts.publish_reported(
        {'kind': 'link', 'title': 'Docker docs', 'url': 'https://docs.docker.com/'}
    )
    if project_id is not None:
        with Session(hub.engine) as s:
            row = s.get(Artifact, art.id)
            assert row is not None
            row.project_id = project_id
            s.add(row)
            s.commit()
    return art.id


def last_event(hub: ChannelHub) -> int:
    found = hub.events()
    return found[-1]['id'] if found else 0


def since(hub: ChannelHub, after: int) -> list[dict[str, Any]]:
    return [e for e in hub.events() if e['id'] > after and e['type'] != 'agent.state']


# 1. GET /messages/{id} and GET /approvals/{id} -----------------------------------------------


def test_a_message_is_read_by_id_with_its_thread(hub: ChannelHub) -> None:
    sent = hub.client.post(f'/api/conversations/{hub.main_id}/messages', json={'text': 'Hi'})
    message_id = sent.json()['id']
    thread = hub.client.post(f'/api/messages/{message_id}/thread').json()
    res = hub.client.get(f'/api/messages/{message_id}')
    assert res.status_code == 200, res.text
    MESSAGE.validate(res.json())
    assert res.json()['conversation_id'] == hub.main_id
    assert res.json()['text'] == 'Hi'
    assert res.json()['thread'] == {'conversation_id': thread['id'], 'reply_count': 0}


def test_another_persons_message_is_not_found(hub: ChannelHub) -> None:
    sent = hub.client.post(f'/api/conversations/{hub.main_id}/messages', json={'text': 'Mine'})
    res = hub.client.get(f'/api/messages/{sent.json()["id"]}', headers=stranger(hub))
    assert (res.status_code, res.json()['code']) == (404, 'message_not_found')
    assert 'Mine' not in res.text
    res = hub.client.get('/api/messages/msg_missing')
    assert (res.status_code, res.json()['code']) == (404, 'message_not_found')


def open_card(hub: ChannelHub) -> tuple[str, str]:
    """A task with an open approval card: (task id, approval id)."""
    task_id, sid = hub.task()
    with hub.connect() as ch:
        ch.hello(sid)
        ch.send(**permission())
        ch.until_pong()
    [card] = hub.approvals()
    return task_id, card['id']


def test_an_approval_is_read_by_id_in_any_state(hub: ChannelHub) -> None:
    task_id, approval_id = open_card(hub)
    res = hub.client.get(f'/api/approvals/{approval_id}')
    assert res.status_code == 200, res.text
    APPROVAL.validate(res.json())
    assert (res.json()['task_id'], res.json()['state']) == (task_id, 'open')
    assert hub.answer(approval_id, 'approve').status_code == 200
    assert hub.approvals(state='open') == []
    res = hub.client.get(f'/api/approvals/{approval_id}')
    assert (res.status_code, res.json()['state']) == (200, 'approved')


def test_any_phone_opens_a_card_and_a_missing_one_is_not_found(hub: ChannelHub) -> None:
    _, approval_id = open_card(hub)
    res = hub.client.get(f'/api/approvals/{approval_id}', headers=other_phone(hub))
    assert res.status_code == 200 and res.json()['id'] == approval_id  # one person (ADR-013)
    res = hub.client.get('/api/approvals/apr_missing')
    assert (res.status_code, res.json()['code']) == (404, 'approval_not_found')


# 2. POST /artifacts/{id}/move -----------------------------------------------------------------


def test_moving_an_item_files_it_and_rebuilds_both_boards(hub: ChannelHub) -> None:
    inbox = projects(hub)[-1]
    energy = new_project(hub, 'Energy')
    art_id = item(hub)
    before = last_event(hub)
    res = hub.client.post(f'/api/artifacts/{art_id}/move', json={'project_id': energy['id']})
    assert res.status_code == 200, res.text
    ARTIFACT.validate(res.json())
    assert res.json()['project_id'] == energy['id']
    sent = since(hub, before)
    assert [e['type'] for e in sent] == [
        'artifact.moved',
        'artifact.updated',
        'project.updated',
        'project.updated',
    ]
    assert sent[0]['data'] == {
        'artifact_id': art_id,
        'from_project_id': inbox['id'],
        'to_project_id': energy['id'],
    }
    assert [sent[2]['data']['id'], sent[3]['data']['id']] == [energy['id'], inbox['id']]
    assert {inbox['id'], energy['id']} <= set(hub.app.state.boards.due())
    listed = hub.client.get('/api/artifacts', params={'project_id': energy['id']}).json()
    assert [a['id'] for a in listed['items']] == [art_id]
    assert hub.client.get('/api/artifacts', params={'project_id': inbox['id']}).json() == {
        'items': []
    }


def test_moving_an_item_to_its_own_project_changes_nothing(hub: ChannelHub) -> None:
    inbox = projects(hub)[-1]
    art_id = item(hub)
    before = last_event(hub)
    res = hub.client.post(f'/api/artifacts/{art_id}/move', json={'project_id': inbox['id']})
    assert res.status_code == 200
    assert res.json()['project_id'] == inbox['id']
    assert since(hub, before) == []


def test_a_board_stays_with_its_project(hub: ChannelHub) -> None:
    inbox = projects(hub)[-1]
    energy = new_project(hub, 'Energy')
    board = inbox['board_artifact_id']
    res = hub.client.post(f'/api/artifacts/{board}/move', json={'project_id': energy['id']})
    assert (res.status_code, res.json()['code']) == (409, 'artifact_is_board')
    with Session(hub.engine) as s:
        row = s.get(Artifact, board)
        assert row is not None and row.project_id == inbox['id']


def test_nothing_moves_into_an_archived_project(hub: ChannelHub) -> None:
    old = new_project(hub, 'Old')
    assert hub.client.post(f'/api/projects/{old["id"]}/archive').status_code == 200
    art_id = item(hub)
    res = hub.client.post(f'/api/artifacts/{art_id}/move', json={'project_id': old['id']})
    assert (res.status_code, res.json()['code']) == (409, 'project_archived')
    assert '“Old” is archived' in res.json()['message']
    with Session(hub.engine) as s:
        row = s.get(Artifact, art_id)
        assert row is not None and row.project_id != old['id']


def test_an_item_moves_only_within_its_agent(hub: ChannelHub) -> None:
    mine = item(hub)
    elsewhere = new_project(hub, 'Elsewhere', other_agent(hub, hub_owner(hub)))
    res = hub.client.post(f'/api/artifacts/{mine}/move', json={'project_id': elsewhere['id']})
    assert (res.status_code, res.json()['code']) == (404, 'project_not_found')
    res = hub.client.post(f'/api/artifacts/{mine}/move', json={'project_id': 'prj_missing'})
    assert (res.status_code, res.json()['code']) == (404, 'project_not_found')
    energy = new_project(hub, 'Energy')
    res = hub.client.post('/api/artifacts/art_missing/move', json={'project_id': energy['id']})
    assert (res.status_code, res.json()['code']) == (404, 'artifact_not_found')
    assert hub.client.post(f'/api/artifacts/{mine}/move', json={}).status_code == 400
    assert hub.client.post(f'/api/artifacts/{mine}/move').status_code == 400


def hub_owner(hub: ChannelHub) -> str:
    with Session(hub.engine) as s:
        agent = s.get(Agent, hub.agent_id)
        assert agent is not None
        return agent.owner_user_id


def board_text(hub: ChannelHub, project: dict[str, Any]) -> str:
    """The project's board as it is rebuilt now, as JSON text."""
    hub.app.state.boards.build(project['id'])
    data, _, _ = hub.app.state.artifacts.content(project['board_artifact_id'])
    return json.dumps(json.loads(data), ensure_ascii=False)


def done_task_with_items(hub: ChannelHub, *titles: str) -> tuple[str, list[str]]:
    """A done task in the inbox with one link item per title: (task id, item ids)."""
    task_id, _ = hub.task(TaskState.done, title='Find the race', summary='Found it.')
    store = hub.app.state.artifacts
    ids = [
        store.publish_reported({'kind': 'link', 'title': t, 'url': 'https://a.b/'}, task_id).id
        for t in titles
    ]
    return task_id, ids


def test_a_pinned_item_shows_on_its_new_board_not_under_its_task_on_the_old(
    hub: ChannelHub,
) -> None:
    inbox = projects(hub)[-1]
    energy = new_project(hub, 'Energy')
    _, (pinned, stays) = done_task_with_items(hub, 'Race fix PR', 'Race notes')
    assert 'Race fix PR' in board_text(hub, inbox)
    hub.client.post(f'/api/artifacts/{pinned}/move', json={'project_id': energy['id']})
    old, new = board_text(hub, inbox), board_text(hub, energy)
    assert 'Race fix PR' not in old and 'Race notes' in old  # the task stays; its pin does not
    assert 'Race fix PR' in new and 'Pinned here' in new
    assert 'Race notes' not in new


def test_a_task_move_takes_its_unpinned_items_and_leaves_pinned_ones(hub: ChannelHub) -> None:
    energy = new_project(hub, 'Energy')
    garden = new_project(hub, 'Garden')
    task_id, (pinned, follows) = done_task_with_items(hub, 'Race fix PR', 'Race notes')
    hub.client.post(f'/api/artifacts/{pinned}/move', json={'project_id': energy['id']})
    before = last_event(hub)
    res = hub.client.post(f'/api/tasks/{task_id}/move', json={'project_id': garden['id']})
    assert res.status_code == 200, res.text
    with Session(hub.engine) as s:
        left, moved = s.get(Artifact, pinned), s.get(Artifact, follows)
        assert left is not None and moved is not None
        assert (left.project_id, left.pinned) == (energy['id'], True)
        assert (moved.project_id, moved.pinned) == (garden['id'], False)
    updated = [e['data']['artifact']['id'] for e in since(hub, before)
               if e['type'] == 'artifact.updated']  # fmt: skip
    assert updated == [follows]  # the app hears only of the item that moved


def test_the_pinned_column_comes_and_goes_with_its_migration(tmp_path: Path) -> None:
    engine = db.make_engine(tmp_path / 'old.db')
    db.upgrade(engine, '0006')
    db.upgrade(engine)
    with engine.connect() as conn:
        columns = {r[1]: r for r in conn.execute(text('PRAGMA table_info(artifact)'))}
    assert columns['pinned'][3] == 1 and columns['pinned'][4] == '0'  # NOT NULL DEFAULT 0
    db.downgrade(engine, '0006')
    with engine.connect() as conn:
        names = {r[1] for r in conn.execute(text('PRAGMA table_info(artifact)'))}
        assert conn.execute(text('PRAGMA foreign_keys')).scalar_one() == 1
    assert 'pinned' not in names and 'current_version' in names


# 3. NewMessage.artifact_id --------------------------------------------------------------------


def test_ask_about_this_stores_the_item_and_tells_the_brain(hub: ChannelHub) -> None:
    brain = hub.brain()
    art_id = item(hub)
    res = hub.client.post(
        f'/api/conversations/{hub.main_id}/messages',
        json={'text': 'What does this say about volumes?', 'artifact_id': art_id},
    )
    assert res.status_code == 201, res.text
    MESSAGE.validate(res.json())
    assert res.json()['artifact_id'] == art_id
    assert hub.client.get(f'/api/messages/{res.json()["id"]}').json()['artifact_id'] == art_id
    [created] = [e for e in hub.events('message.created') if e['data']['id'] == res.json()['id']]
    assert created['data']['artifact_id'] == art_id
    hub.client.post(f'/api/conversations/{hub.main_id}/messages', json={'text': 'Thanks.'})
    with hub.connect() as ch:
        ch.hello(brain)
        about, plain = ch.deliver(), ch.deliver()
    assert about['artifact_id'] == art_id
    assert 'artifact_id' not in plain


def test_ask_about_this_in_a_task_thread_stores_the_item(hub: ChannelHub) -> None:
    task_id, _ = hub.task()
    thread_id, _ = hub.side(task_id)
    art_id = item(hub)
    res = hub.client.post(
        f'/api/conversations/{thread_id}/messages',
        json={'text': 'Use this.', 'artifact_id': art_id},
    )
    assert res.status_code == 201, res.text
    assert res.json()['artifact_id'] == art_id


def test_an_unknown_item_on_a_message_is_refused_and_nothing_is_stored(hub: ChannelHub) -> None:
    theirs = other_agent(hub, STRANGER)
    with Session(hub.engine) as s:
        their_inbox = inbox_id(s.connection(), theirs)
        s.commit()
    foreign = item(hub, their_inbox)
    for art_id in ('art_missing', foreign):
        res = hub.client.post(
            f'/api/conversations/{hub.main_id}/messages',
            json={'text': 'About this?', 'artifact_id': art_id},
        )
        assert (res.status_code, res.json()['code']) == (404, 'artifact_not_found'), art_id
    assert hub.messages() == []


# 4. report(progress).next ---------------------------------------------------------------------


def test_a_workers_next_steps_reach_the_progress_event(hub: ChannelHub) -> None:
    task_id, sid = hub.task()
    steps = ['Run the login tests ten times', 'Open the PR']
    with hub.connect() as ch:
        ch.hello(sid)
        ack = ch.call('report', kind='progress', text='Found the race.', next=steps)
        assert 'error' not in ack
        ch.call('report', kind='progress', text='Tests pass.')
    [first, second] = [e.data_json for e in hub.task_events(task_id, 'progress')]
    assert first == {'text': 'Found the race.', 'next': steps}
    assert second == {'text': 'Tests pass.'}  # absent: the worker gave none
    sent = [e['data'] for e in hub.events('task.event') if e['data']['type'] == 'progress']
    assert sent[0]['data'] == first  # checked against events.schema.json by `hub.events`


@pytest.mark.parametrize(
    ('steps', 'says'),
    [
        ([f'Step {n}' for n in range(21)], 'next has 21 steps; send at most 20'),
        (['x' * 201], 'step 1 of next is longer than 200 characters'),
        (['ok', 'y' * 201, 'z' * 300], 'steps 2, 3 of next are longer than 200 characters'),
        (['Open the PR', ''], 'a step in next is empty'),
        ('Open the PR', 'next must be a list of steps'),
        ([1, 2], 'next must be a list of steps'),
    ],
)
def test_next_steps_past_the_contract_are_refused_not_cut(
    hub: ChannelHub, steps: Any, says: str
) -> None:
    task_id, sid = hub.task(TaskState.starting)
    with hub.connect() as ch:
        ch.hello(sid)
        ack = ch.call('report', call_id='c_next', kind='progress', text='Busy.', next=steps)
    assert ack['error']['code'] == 'bad_next'
    assert says in ack['error']['message']
    assert ack['error']['message'].startswith('Marcel did not take this report')
    assert hub.task_events(task_id, 'progress') == []  # nothing half-lands
    assert hub.registry.get(task_id).state == TaskState.starting
    with Session(hub.engine) as s:
        assert s.get(ChannelCall, 'c_next') is None


def test_next_on_a_report_other_than_progress_is_ignored_unread(hub: ChannelHub) -> None:
    """channel.schema.json allows `next` on any report but gives it meaning only for progress."""
    task_id, sid = hub.task(TaskState.starting)
    with hub.connect() as ch:
        ch.hello(sid)
        ack = ch.call('report', kind='started', text='Starting.', next=['x' * 300] * 30)
    assert 'error' not in ack
    [event] = hub.task_events(task_id, 'progress')
    assert event.data_json == {'text': 'Starting.'}
    assert hub.registry.get(task_id).state == TaskState.working


def test_twenty_steps_of_two_hundred_characters_are_taken(hub: ChannelHub) -> None:
    task_id, sid = hub.task()
    steps = ['s' * 200] * 20
    with hub.connect() as ch:
        ch.hello(sid)
        assert 'error' not in ch.call('report', kind='progress', text='Busy.', next=steps)
    [event] = hub.task_events(task_id, 'progress')
    assert event.data_json['next'] == steps


# 5. pr.note -----------------------------------------------------------------------------------


def test_a_pr_card_carries_its_note_and_null_once_refreshed(hub: ChannelHub) -> None:
    store = hub.app.state.artifacts
    art = store.publish_reported(
        {'kind': 'pr', 'title': 'Fix', 'url': 'https://github.com/shbunder/marcel/pull/14'}
    )

    def card() -> dict[str, Any]:
        res = hub.client.get(f'/api/artifacts/{art.id}')
        ARTIFACT.validate(res.json())
        return res.json()['pr']

    assert card()['note'] is None
    store.set_meta(art.id, pr_note='GitHub is having trouble (it answered 502).')
    assert card()['note'] == 'GitHub is having trouble (it answered 502).'
    store.set_meta(art.id, pr_note='n' * 250)
    assert card()['note'] == 'n' * 200  # the contract's limit
    store.set_meta(art.id, pr_note=None)
    assert card() == {'number': 14, 'state': 'open', 'note': None}


# 6. Approval cards in task threads ------------------------------------------------------------


def test_an_approval_card_is_mirrored_into_its_task_thread_and_answers_both(
    hub: ChannelHub,
) -> None:
    task_id, sid = hub.task()
    thread_id, _ = hub.side(task_id)
    with hub.connect() as ch:
        ch.hello(sid)
        ch.send(**permission())
        ch.until_pong()
    [card] = hub.approvals()
    [main_copy] = [m for m in hub.messages() if m.kind == MessageKind.approval]
    [thread_copy] = [m for m in hub.messages(thread_id) if m.kind == MessageKind.approval]
    for msg in (main_copy, thread_copy):
        body = hub.client.get(f'/api/messages/{msg.id}').json()
        MESSAGE.validate(body)
        assert (body['approval_id'], body['task_id']) == (card['id'], task_id)
    # Answering either answers both: they are one card.
    assert hub.answer(card['id'], 'approve').status_code == 200
    assert hub.client.get(f'/api/approvals/{card["id"]}').json()['state'] == 'approved'
    assert hub.answer(card['id'], 'deny').status_code == 409
