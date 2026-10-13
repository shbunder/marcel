"""Every request is checked against its person (S-17.2, B-38).

The owner (Shaun) has an agent with a conversation, a message, a project, a schedule, a task with
an item and an approval card, and a phone. Emma, a family member, tries every app route that takes
an id on each of them and gets 404, as if they did not exist. The routes come from the app itself,
not a list: a new route with an id fails `test_every_route_with_an_id_is_classified` until it is
added below, so it cannot ship unchecked.
"""

import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.routing import APIRoute, APIWebSocketRoute, iter_route_contexts
from fastapi.testclient import TestClient
from sqlmodel import Session, col, select

from marcel_hub.app import Settings, create_app
from marcel_hub.auth import tokens
from marcel_hub.models import (
    AppEvent,
    Approval,
    Artifact,
    Device,
    Task,
    TaskLocation,
    TaskOrigin,
    TaskState,
)
from marcel_hub.users.store import ensure_owner_agent

# What Emma's phone gets on each route with an id, used on the owner's object. `None`: the route is
# not reached with a phone token, or its isolation is another story's, with the reason.
NOT_HERE = {
    ('GET', '/api/memory/files/{}'): 'memory is per person from S-17.4 (one repo until then)',
    ('PUT', '/api/memory/files/{}'): 'memory is per person from S-17.4 (one repo until then)',
    ('GET', '/api/ws'): 'the event stream: test_events_reach_only_their_person',
    ('GET', '/channel'): 'sessions, not phones: a per-session token (channel/socket.py)',
    ('GET', '/brain/tasks/{}'): 'the brain token: own agent only (brain_api._own_task)',
    ('POST', '/brain/tasks/{}/messages'): 'the brain token: own agent only (brain_api._own_task)',
    ('POST', '/brain/tasks/{}/stop'): 'the brain token: own agent only (brain_api._own_task)',
    ('POST', '/brain/tasks/{}/move'): 'the brain token: own agent only (brain_api._own_task)',
    ('PATCH', '/brain/projects/{}'): 'the brain token: own agent only (brain_api._own_project)',
    (
        'POST',
        '/brain/projects/{}/notes',
    ): 'the brain token: own agent only (brain_api._own_project)',
    ('DELETE', '/brain/schedules/{}'): 'the brain token: own agent only (brain_api)',
}

# (method, path) → (status Emma gets, the body she sends). A body is valid, so the answer is the
# ownership check's, never a 400 from validation that would hide a missing check.
CHECKED: dict[tuple[str, str], tuple[int, dict[str, Any] | None]] = {
    ('DELETE', '/api/devices/{device_id}'): (404, None),
    ('PUT', '/api/devices/{device_id}/push'): (404, {'kind': 'ntfy', 'ntfy_topic': 'emma'}),
    ('GET', '/api/tasks/{task_id}'): (404, None),
    ('GET', '/api/tasks/{task_id}/events'): (404, None),
    ('POST', '/api/tasks/{task_id}/messages'): (404, {'text': 'Stop that.'}),
    ('POST', '/api/tasks/{task_id}/stop'): (404, None),
    ('POST', '/api/tasks/{task_id}/archive'): (404, None),
    ('POST', '/api/tasks/{task_id}/handback'): (404, None),
    ('POST', '/api/tasks/{task_id}/move'): (404, {'project_id': 'EMMAS_INBOX'}),
    ('GET', '/api/conversations/{conversation_id}/messages'): (404, None),
    ('POST', '/api/conversations/{conversation_id}/messages'): (
        404,
        {'text': 'Hi', 'client_id': 'c'},
    ),
    ('GET', '/api/messages/{message_id}'): (404, None),
    ('POST', '/api/messages/{message_id}/thread'): (404, None),
    ('POST', '/api/conversations/{conversation_id}/send-to-main'): (404, {'message_id': 'MESSAGE'}),
    ('GET', '/api/artifacts/{artifact_id}'): (404, None),
    ('GET', '/api/artifacts/{artifact_id}/versions'): (404, None),
    ('GET', '/api/artifacts/{artifact_id}/content'): (404, None),
    ('POST', '/api/artifacts/{artifact_id}/move'): (404, {'project_id': 'EMMAS_INBOX'}),
    ('GET', '/api/approvals/{approval_id}'): (404, None),
    ('POST', '/api/approvals/{approval_id}'): (404, {'decision': 'deny'}),
    ('GET', '/api/projects/{project_id}'): (404, None),
    ('PATCH', '/api/projects/{project_id}'): (404, {'name': 'Mine now'}),
    ('POST', '/api/projects/{project_id}/archive'): (404, None),
    ('PATCH', '/api/schedules/{schedule_id}'): (404, {'paused': True}),
    ('DELETE', '/api/schedules/{schedule_id}'): (404, None),
    ('POST', '/api/agents/{agent_id}/brain/rollover'): (404, None),
    ('GET', '/api/agents/{agent_id}/brain/rollovers'): (404, None),
    ('GET', '/api/agents/{agent_id}'): (404, None),
    ('PATCH', '/api/agents/{agent_id}'): (404, {'name': 'Pip'}),
    ('GET', '/api/agents/{agent_id}/avatar'): (404, None),
    # Owner-only (S-17.1): a member is refused before the person is even looked up.
    ('DELETE', '/api/users/{user_id}'): (403, None),
    ('POST', '/api/users/{user_id}/pairing-codes'): (403, None),
}


def normalise(path: str) -> str:
    return re.sub(r'\{[^}]+\}', '{}', path)


def routes_with_ids(data: Path) -> set[tuple[str, str]]:
    app = create_app(Settings(data_dir=data, db_path=data / 'marcel.db'))  # built, never started
    found: set[tuple[str, str]] = set()
    for ctx in iter_route_contexts(app.routes):
        route = ctx.original_route
        if isinstance(route, APIWebSocketRoute):
            found.add(('GET', route.path))
        elif isinstance(route, APIRoute) and '{' in route.path:
            found |= {(m, route.path) for m in route.methods or ()}
    return found


class World:
    def __init__(self, client: TestClient) -> None:
        self.client = client
        self.engine = client.app.state.engine  # type: ignore[attr-defined]
        self.owner, self.owner_device = self.phone(tokens.OWNER_USER_ID)
        with Session(self.engine) as s:
            agent, _ = ensure_owner_agent(s, 'Marcel')
            self.agent_id, self.conversation_id = agent.id, agent.main_conversation_id
        emma = self.call(
            'POST', '/api/users', self.owner, {'name': 'Emma', 'agent': {'name': 'Pip'}}
        ).json()
        self.emma_id, self.emma_agent = emma['id'], emma['agent']['id']
        self.emma, _ = self.phone(self.emma_id)
        self.message_id = self.call(
            'POST',
            f'/api/conversations/{self.conversation_id}/messages',
            self.owner,
            {'text': 'Compare the heat-pump offers', 'client_id': 'c1'},
        ).json()['id']
        self.project_id = self.call(
            'POST', '/api/projects', self.owner, {'agent_id': self.agent_id, 'name': 'House'}
        ).json()['id']
        self.schedule_id = self.call(
            'POST',
            '/api/schedules',
            self.owner,
            {
                'agent_id': self.agent_id,
                'name': 'Monday report',
                'cron': '0 8 * * 1',
                'tz': 'Europe/Brussels',
                'location': 'nuc',
                'prompt': 'Report.',
            },
        ).json()['id']
        with Session(self.engine) as s:
            task = Task(
                agent_id=self.agent_id,
                project_id=self.project_id,
                title='Heat pumps',
                location=TaskLocation.nuc,
                state=TaskState.needs_you,
                model='sonnet',
                origin=TaskOrigin.marcel,
            )
            s.add(task)
            s.commit()
            self.task_id = task.id
        # A doc with content, so `/content` answers 200 to the owner and only the check stops Emma.
        art = client.app.state.artifacts.publish_hub_html(  # type: ignore[attr-defined]
            'Offer', '<p>Daikin is cheapest.</p>', task_id=self.task_id
        )
        self.artifact_id = art.id
        with Session(self.engine) as s:
            card = Approval(
                task_id=self.task_id, request_id='req_1', tool='Bash', summary='Read 3 PDFs'
            )
            s.add(card)
            s.commit()
            self.approval_id = card.id
        inbox = self.call('GET', '/api/projects', self.emma, params={'agent_id': self.emma_agent})
        self.emma_inbox = inbox.json()[0]['id']

    def phone(self, user_id: str) -> tuple[dict[str, str], str]:
        with Session(self.engine) as s:
            code = tokens.create_pairing_code(s, user_id=user_id)
        paired = self.client.post('/api/pair', json={'code': code, 'device_name': user_id}).json()
        return {'Authorization': f'Bearer {paired["token"]}'}, paired['device_id']

    def call(self, method: str, path: str, who: dict[str, str], body: Any = None, **kw: Any) -> Any:
        return self.client.request(method, path, headers=who, json=body, **kw)

    def ids(self) -> dict[str, str]:
        return {
            'device_id': self.owner_device,
            'task_id': self.task_id,
            'conversation_id': self.conversation_id,
            'message_id': self.message_id,
            'artifact_id': self.artifact_id,
            'approval_id': self.approval_id,
            'project_id': self.project_id,
            'schedule_id': self.schedule_id,
            'agent_id': self.agent_id,
            'user_id': tokens.OWNER_USER_ID,
        }


@pytest.fixture
def world(tmp_path: Path) -> Iterator[World]:
    settings = Settings(data_dir=tmp_path, db_path=tmp_path / 'marcel.db', owner_name='Shaun')
    with TestClient(create_app(settings)) as client:
        yield World(client)


def test_every_route_with_an_id_is_classified(tmp_path: Path) -> None:
    known = {(m, normalise(p)) for m, p in CHECKED} | set(NOT_HERE)
    missing = sorted(r for r in routes_with_ids(tmp_path) if (r[0], normalise(r[1])) not in known)
    assert missing == [], (
        f'New routes with an id: {missing}. Add each to CHECKED (with the status a member gets '
        "on the owner's object) or to NOT_HERE (with why), so it never ships unchecked."
    )


@pytest.mark.parametrize(('method', 'path'), sorted(CHECKED))
def test_a_member_never_reaches_the_owners_objects(world: World, method: str, path: str) -> None:
    status, body = CHECKED[(method, path)]
    url = path.format(**world.ids())
    if body is not None:
        body = {
            k: {'EMMAS_INBOX': world.emma_inbox, 'MESSAGE': world.message_id}.get(v, v)
            if isinstance(v, str)
            else v
            for k, v in body.items()
        }
    res = world.call(method, url, world.emma, body)
    assert res.status_code == status, f'{method} {path}: Emma got {res.status_code} {res.text}'
    # and the owner still reaches it: the 404 is about Emma, not a missing object
    assert world.call('GET', f'/api/tasks/{world.task_id}', world.owner).status_code == 200
    assert (
        world.call('GET', f'/api/artifacts/{world.artifact_id}/content', world.owner).status_code
        == 200
    )


def test_lists_show_only_the_callers_own(world: World) -> None:
    tasks = world.call('GET', '/api/tasks', world.emma, params={'agent_id': world.agent_id})
    assert tasks.status_code == 200 and tasks.json()['items'] == []
    items = world.call('GET', '/api/artifacts', world.emma).json()['items']
    assert world.artifact_id not in [a['id'] for a in items]
    mine = world.call('GET', '/api/artifacts', world.owner).json()['items']
    assert world.artifact_id in [a['id'] for a in mine]
    assert world.call('GET', '/api/approvals', world.emma).json() == []


def test_events_reach_only_their_person(world: World) -> None:
    app = world.client.app
    app.state.tasks._publish_task(world.task_id)  # type: ignore[attr-defined]
    app.state.tasks.publisher.publish('usage.updated', {'used_pct': 80})  # type: ignore[attr-defined]
    with Session(world.engine) as s:
        rows = s.exec(
            select(AppEvent).where(col(AppEvent.type).in_(['task.updated', 'usage.updated']))
        ).all()
    people = {(row.type, row.user_id) for row in rows}
    assert ('task.updated', tokens.OWNER_USER_ID) in people
    assert ('usage.updated', tokens.OWNER_USER_ID) in people
    assert not [p for p in people if p[1] == world.emma_id], "Emma got the owner's events"


def test_a_push_for_nobody_in_particular_reaches_only_the_owners_phones(world: World) -> None:
    with Session(world.engine) as s:
        for device in s.exec(select(Device)):
            device.ntfy_topic = f'topic-{device.user_id}'
            s.add(device)
        s.commit()
    pusher = world.client.app.state.pusher  # type: ignore[attr-defined]
    assert {d.user_id for d in pusher._devices(None)} == {tokens.OWNER_USER_ID}
    assert {d.user_id for d in pusher._devices(world.emma_id)} == {world.emma_id}


def test_a_loose_item_is_filed_with_the_owner_never_a_member(world: World) -> None:
    with Session(world.engine) as s:
        loose = Artifact(kind='link', title='Loose', url='https://example.com')  # type: ignore[arg-type]
        s.add(loose)
        s.commit()
        loose_id = loose.id
    assert world.call('GET', f'/api/artifacts/{loose_id}', world.owner).status_code == 200
    assert world.call('GET', f'/api/artifacts/{loose_id}', world.emma).status_code == 404


# --- self-changes (B-24): a family helper's PR on Marcel is the owner's to answer -------------


def self_change_card(world: World) -> str:
    """A self-change card on a task of Emma's helper."""
    with Session(world.engine) as s:
        task = Task(
            agent_id=world.emma_agent,
            title='Tweak Marcel',
            location=TaskLocation.cloud,
            state=TaskState.working,
            model='sonnet',
            origin=TaskOrigin.marcel,
        )
        s.add(task)
        s.commit()
        pr = Artifact(
            kind='pr',
            title='Tweak',
            task_id=task.id,  # type: ignore[arg-type]
            url='https://github.com/shbunder/marcel/pull/99',
            self_change=True,
        )
        s.add(pr)
        s.commit()
        card = Approval(
            task_id=task.id,
            tool='self_change',
            summary='Marcel wants to change itself',
            artifact_id=pr.id,
        )
        s.add(card)
        s.commit()
        return card.id


def test_a_member_cannot_approve_a_change_to_marcel(world: World) -> None:
    card = self_change_card(world)
    res = world.call('POST', f'/api/approvals/{card}', world.emma, {'decision': 'approve'})
    assert res.status_code == 403 and res.json()['code'] == 'owner_only'
    assert 'Only Shaun can approve a change to Marcel' in res.json()['message']
    with Session(world.engine) as s:
        still = s.get(Approval, card)
    assert still is not None and still.state.value == 'open'
    assert world.call('GET', f'/api/approvals/{card}', world.emma).status_code == 200  # she sees it


def test_the_owner_sees_and_answers_a_family_helpers_self_change(world: World) -> None:
    card = self_change_card(world)
    assert card in [a['id'] for a in world.call('GET', '/api/approvals', world.owner).json()]
    assert world.call('GET', f'/api/approvals/{card}', world.owner).status_code == 200


def test_the_owner_id_is_spelled_the_same_in_models_and_auth() -> None:
    from marcel_hub import models

    assert models.OWNER_ID == tokens.OWNER_USER_ID


def events_of(
    world: World, prefix: str, before: set[tuple[str, int]] | None = None
) -> set[tuple[str, str]]:
    """(type, person) of the events of this kind, leaving out those in `before`."""
    with Session(world.engine) as s:
        rows = s.exec(select(AppEvent).where(col(AppEvent.type).startswith(prefix))).all()
    return {(r.type, r.user_id) for r in rows if (r.user_id, r.id) not in (before or set())}


def seen(world: World) -> set[tuple[str, int]]:
    """Every event so far, by its key (person, number): their own counter per person."""
    with Session(world.engine) as s:
        return {(r.user_id, r.id) for r in s.exec(select(AppEvent))}


def test_a_members_task_and_item_events_reach_her_and_never_the_owner(world: World) -> None:
    app = world.client.app
    with Session(world.engine) as s:
        task = Task(
            agent_id=world.emma_agent,
            title='Homework plan',
            location=TaskLocation.nuc,
            state=TaskState.working,
            model='sonnet',
            origin=TaskOrigin.marcel,
        )
        s.add(task)
        s.commit()
        task_id = task.id
    store = app.state.artifacts  # type: ignore[attr-defined]
    start = seen(world)  # only what follows is about Emma's things
    app.state.tasks._publish_task(task_id)  # type: ignore[attr-defined]
    on_task = store.publish_hub_html('Plan', '<p>v1</p>', task_id=task_id)
    store.publish_hub_html('Loose', '<p>x</p>', project_id=world.emma_inbox)  # no task
    school = world.call(
        'POST', '/api/projects', world.emma, {'agent_id': world.emma_agent, 'name': 'School'}
    ).json()['id']
    store.move(on_task.id, school, world.emma_id)  # artifact.moved names projects only
    events = events_of(world, 'task.', start) | events_of(world, 'artifact.', start)
    emmas = {t for t, person in events if person == world.emma_id}
    assert {'task.updated', 'artifact.created', 'artifact.moved', 'artifact.updated'} <= emmas
    assert [e for e in events if e[1] == tokens.OWNER_USER_ID] == [], "the owner got Emma's"


def test_a_family_helpers_self_change_goes_to_the_owner(world: World) -> None:
    app = world.client.app
    card_id = self_change_card(world)
    with Session(world.engine) as s:
        card = s.get(Approval, card_id)
    assert card is not None
    app.state.channel.approvals._publish('approval.created', card)  # type: ignore[attr-defined]
    assert {('approval.created', tokens.OWNER_USER_ID), ('approval.created', world.emma_id)} <= (
        events_of(world, 'approval.')
    )
    pushed: list[str | None] = []
    pusher = app.state.pusher  # type: ignore[attr-defined]
    pusher.push = lambda n, user_id: pushed.append(user_id) or True
    pusher.approval_opened(card_id)
    assert pushed == [None]  # nobody in particular: the owner's phones (`_devices`)
    app.state.selfmod.events = app.state.events  # type: ignore[attr-defined]
    app.state.selfmod._post_card(card)  # type: ignore[attr-defined]
    owners = world.call('GET', f'/api/conversations/{world.conversation_id}/messages', world.owner)
    assert card_id in [m.get('approval_id') for m in owners.json()['items']]


def test_the_owner_answers_a_family_helpers_self_change(world: World) -> None:
    card = self_change_card(world)
    res = world.call('POST', f'/api/approvals/{card}', world.owner, {'decision': 'deny'})
    assert res.status_code == 200, res.text
    assert res.json()['state'] == 'denied'
