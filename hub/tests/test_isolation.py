"""One person, many buddies (S-22.11, ADR-013): no route tells two people apart any more, so the
device token is what keeps a stranger out of every one.

The one person has two buddies, Marcel and Pip, each with a conversation, a message, a project, a
schedule, a task with an item and an approval card, and a phone that shows it. Every app route
with an id in its path is called on those real objects without a token, and must answer 401. The
routes come from the app itself, not a list: a new route with an id fails
`test_every_route_with_an_id_is_classified` until it is added below. Removing the token check from
any one router turns the loop red (`test_the_loop_catches_a_router_without_its_token_check`).

A phone that shows Pip reaches everything of Marcel's too: the conversation, the tasks, the items
and the approval cards, a self-change card included.
"""

import re
import tempfile
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.routing import APIRoute, iter_route_contexts
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from marcel_hub.app import Settings, create_app
from marcel_hub.auth import tokens
from marcel_hub.auth.deps import require_device
from marcel_hub.brain_api import Brain, require_brain
from marcel_hub.conversations import ensure_main_conversation
from marcel_hub.models import (
    Agent,
    AppEvent,
    Approval,
    Artifact,
    Device,
    Message,
    MessageKind,
    MessageRole,
    Task,
    TaskLocation,
    TaskOrigin,
    TaskState,
)
from marcel_hub.users.store import ensure_owner_agent

HUB_SECRET = 'hs_' + 'a1b2c3d4' * 6

# Every route with an id in its path, by who may call it. `phone`: the device token
# (`require_device` on its router); `brain`: the hub secret and the brain's session id
# (`brain_api`). Both answer 401 without them.
CLASSIFIED: dict[tuple[str, str], str] = {
    ('DELETE', '/api/devices/{}'): 'phone',
    ('PUT', '/api/devices/{}/push'): 'phone',
    ('PATCH', '/api/devices/{}'): 'phone',
    ('GET', '/api/tasks/{}'): 'phone',
    ('GET', '/api/tasks/{}/events'): 'phone',
    ('POST', '/api/tasks/{}/messages'): 'phone',
    ('POST', '/api/tasks/{}/stop'): 'phone',
    ('POST', '/api/tasks/{}/archive'): 'phone',
    ('POST', '/api/tasks/{}/handback'): 'phone',
    ('POST', '/api/tasks/{}/move'): 'phone',
    ('GET', '/api/conversations/{}/messages'): 'phone',
    ('POST', '/api/conversations/{}/messages'): 'phone',
    ('POST', '/api/conversations/{}/send-to-main'): 'phone',
    ('GET', '/api/messages/{}'): 'phone',
    ('POST', '/api/messages/{}/thread'): 'phone',
    ('GET', '/api/artifacts/{}'): 'phone',
    ('GET', '/api/artifacts/{}/versions'): 'phone',
    ('GET', '/api/artifacts/{}/content'): 'phone',
    ('POST', '/api/artifacts/{}/move'): 'phone',
    ('GET', '/api/memory/files/{}'): 'phone',
    ('PUT', '/api/memory/files/{}'): 'phone',
    ('GET', '/api/approvals/{}'): 'phone',
    ('POST', '/api/approvals/{}'): 'phone',
    ('GET', '/api/projects/{}'): 'phone',
    ('PATCH', '/api/projects/{}'): 'phone',
    ('POST', '/api/projects/{}/archive'): 'phone',
    ('PATCH', '/api/schedules/{}'): 'phone',
    ('DELETE', '/api/schedules/{}'): 'phone',
    ('GET', '/api/agents/{}'): 'phone',
    ('PATCH', '/api/agents/{}'): 'phone',
    ('POST', '/api/agents/{}/archive'): 'phone',
    ('GET', '/api/agents/{}/avatar'): 'phone',
    ('POST', '/api/agents/{}/brain/rollover'): 'phone',
    ('GET', '/api/agents/{}/brain/rollovers'): 'phone',
    ('GET', '/brain/tasks/{}'): 'brain',
    ('POST', '/brain/tasks/{}/messages'): 'brain',
    ('POST', '/brain/tasks/{}/stop'): 'brain',
    ('POST', '/brain/tasks/{}/move'): 'brain',
    ('PATCH', '/brain/projects/{}'): 'brain',
    ('POST', '/brain/projects/{}/notes'): 'brain',
    ('DELETE', '/brain/schedules/{}'): 'brain',
}

# A valid body for each route that takes one, so a 401 is the token check's, never a 400.
BODIES: dict[tuple[str, str], dict[str, Any]] = {
    ('PUT', '/api/devices/{}/push'): {'kind': 'ntfy', 'ntfy_topic': 'mine'},
    ('PATCH', '/api/devices/{}'): {'agent_id': 'PIP'},
    ('POST', '/api/tasks/{}/messages'): {'text': 'Stop that.'},
    ('POST', '/api/tasks/{}/move'): {'project_id': 'PROJECT'},
    ('POST', '/api/conversations/{}/messages'): {'text': 'Hi', 'client_id': 'c'},
    ('POST', '/api/conversations/{}/send-to-main'): {'message_id': 'MESSAGE'},
    ('POST', '/api/artifacts/{}/move'): {'project_id': 'PROJECT'},
    ('PUT', '/api/memory/files/{}'): {'content': 'Mine now.\n'},
    ('POST', '/api/approvals/{}'): {'decision': 'deny'},
    ('PATCH', '/api/projects/{}'): {'name': 'Mine now'},
    ('PATCH', '/api/schedules/{}'): {'paused': True},
    ('PATCH', '/api/agents/{}'): {'name': 'Mine now'},
}


def normalise(path: str) -> str:
    return re.sub(r'\{[^}]+\}', '{}', path)


def routes_with_ids(app: FastAPI) -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for ctx in iter_route_contexts(app.routes):
        if isinstance(ctx.original_route, APIRoute) and '{' in str(ctx.path):
            found |= {(m, normalise(str(ctx.path))) for m in ctx.methods or ()}
    return found


def settings(data: Path) -> Settings:
    (data / 'hub-secret').write_text(HUB_SECRET)
    return Settings(data_dir=data, db_path=data / 'marcel.db', owner_name='Shaun')


class World:
    """Two buddies of the one person, Marcel and Pip, with something of each kind on Marcel."""

    def __init__(self, client: TestClient) -> None:
        self.client = client
        self.app: Any = client.app
        self.engine = self.app.state.engine
        with Session(self.engine) as s:
            marcel, _ = ensure_owner_agent(s, 'Marcel')
            self.marcel, self.marcel_main = marcel.id, marcel.main_conversation_id
            pip = Agent(owner_user_id=tokens.OWNER_USER_ID, name='Pip', animal='fox',
                        palette='marcel', seed='Pip', slug='pip')  # fmt: skip
            s.add(pip)
            s.commit()
            self.pip = pip.id
            self.pip_main = ensure_main_conversation(s, pip).id
        self.marcels_phone, self.marcels_device = self.phone(self.marcel)
        self.pips_phone, self.pips_device = self.phone(self.pip)
        # Made without the routes, so a router whose token check is taken off cannot break it.
        with Session(self.engine) as s:
            message = Message(
                conversation_id=self.marcel_main,
                role=MessageRole.user,
                kind=MessageKind.text,
                body_json={'text': 'Compare the offers'},
            )
            s.add(message)
            s.commit()
            self.message_id = message.id
        self.project_id = self.app.state.projects.create(self.marcel, 'House').id
        self.schedule_id = self.app.state.scheduler.create(
            self.marcel,
            {'name': 'Monday report', 'cron': '0 8 * * 1', 'tz': 'Europe/Brussels',
             'location': 'nuc', 'prompt': 'Report.'},
        )['id']  # fmt: skip
        self.task_id = self.task(self.marcel, self.project_id)
        art = self.app.state.artifacts.publish_hub_html(
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

    def phone(self, agent_id: str) -> tuple[dict[str, str], str]:
        """A phone paired the way `marcel-hub pair` pairs one, then set to show `agent_id`."""
        with Session(self.engine) as s:
            code = tokens.create_pairing_code(s)
        paired = self.client.post('/api/pair', json={'code': code, 'device_name': agent_id})
        body = paired.json()
        with Session(self.engine) as s:
            device = s.get(Device, body['device_id'])
            assert device is not None
            device.agent_id = agent_id
            s.add(device)
            s.commit()
        return {'Authorization': f'Bearer {body["token"]}'}, body['device_id']

    def task(self, agent_id: str, project_id: str | None = None) -> str:
        with Session(self.engine) as s:
            task = Task(
                agent_id=agent_id,
                project_id=project_id,
                title='Heat pumps',
                location=TaskLocation.nuc,
                state=TaskState.needs_you,
                model='sonnet',
                origin=TaskOrigin.marcel,
            )
            s.add(task)
            s.commit()
            return task.id

    def call(self, method: str, path: str, who: dict[str, str], body: Any = None, **kw: Any) -> Any:
        return self.client.request(method, path, headers=who, json=body, **kw)

    def url(self, method: str, path: str) -> str:
        """The route on Marcel's real object of its kind."""
        ids = {
            '/api/devices/': self.marcels_device,
            '/api/tasks/': self.task_id,
            '/brain/tasks/': self.task_id,
            '/api/conversations/': self.marcel_main,
            '/api/messages/': self.message_id,
            '/api/artifacts/': self.artifact_id,
            '/api/memory/files/': 'MEMORY.md',
            '/api/approvals/': self.approval_id,
            '/api/projects/': self.project_id,
            '/brain/projects/': self.project_id,
            '/api/schedules/': self.schedule_id,
            '/brain/schedules/': self.schedule_id,
            '/api/agents/': self.marcel,
        }
        prefix = path[: path.index('{}')]
        return prefix + ids[prefix] + path[path.index('{}') + 2 :]

    def body(self, method: str, path: str) -> dict[str, Any] | None:
        body = BODIES.get((method, path))
        swap = {'PROJECT': self.project_id, 'MESSAGE': self.message_id, 'PIP': self.pip}
        return {k: swap.get(v, v) if isinstance(v, str) else v for k, v in (body or {}).items()}


def unrefused(world: World) -> list[str]:
    """Every route with an id that answers Marcel's real object without a token, or with an
    unknown one, with anything but 401."""
    leaks: list[str] = []
    stranger = {'Authorization': f'Bearer {tokens.new_token()}'}
    for method, path in sorted(CLASSIFIED):
        for who in ({}, stranger):
            res = world.call(method, world.url(method, path), who, world.body(method, path))
            if res.status_code != 401:
                leaks.append(f'{method} {path} -> {res.status_code}')
                break
    return leaks


@pytest.fixture
def world(tmp_path: Path) -> Iterator[World]:
    with TestClient(create_app(settings(tmp_path))) as client:
        yield World(client)


def test_every_route_with_an_id_is_classified(tmp_path: Path) -> None:
    served = routes_with_ids(create_app(settings(tmp_path)))  # built, never started
    assert sorted(served - set(CLASSIFIED)) == [], (
        'New routes with an id: add each to CLASSIFIED with who may call it, so its token check '
        'is tested on a real object.'
    )
    assert sorted(set(CLASSIFIED) - served) == [], 'CLASSIFIED names a route the hub no longer has'


def test_every_route_with_an_id_answers_401_without_a_token(world: World) -> None:
    assert unrefused(world) == []
    # and the phone still reaches each object: the 401 is about the token, not a missing object
    assert world.call('GET', f'/api/tasks/{world.task_id}', world.pips_phone).status_code == 200


def phone_routers(app: FastAPI) -> list[APIRouter]:
    """The routers that serve a `phone` route with an id, each once."""
    phone = {route for route, who in CLASSIFIED.items() if who == 'phone'}
    found: list[APIRouter] = []
    for included in app.router.routes:
        router = getattr(included, 'original_router', None)
        if not isinstance(router, APIRouter) or router in found:
            continue
        paths = {
            (m, normalise(r.path))
            for r in router.routes
            if isinstance(r, APIRoute)
            for m in r.methods or ()
        }
        if paths & phone:
            found.append(router)
    return found


def without_token_check(route: APIRoute) -> APIRoute:
    deps = [d for d in route.dependencies if d.dependency is not require_device]
    assert len(deps) < len(route.dependencies), f'{route.path} has no token check to remove'
    return APIRoute(
        route.path,
        route.endpoint,
        methods=sorted(route.methods or ()),
        dependencies=deps,
        response_model=route.response_model,
        status_code=route.status_code,
        response_class=route.response_class,
        name=route.name,
    )


ROUTERS = phone_routers(create_app(settings(Path(tempfile.mkdtemp()))))


def first_path(router: APIRouter) -> str:
    return normalise(next(r.path for r in router.routes if isinstance(r, APIRoute)))


@pytest.mark.parametrize('router', ROUTERS, ids=first_path)
def test_the_loop_catches_a_router_without_its_token_check(
    tmp_path: Path, router: APIRouter
) -> None:
    """Take the token check off one router: each of its routes with an id leaks."""
    kept = list(router.routes)
    router.routes[:] = [without_token_check(r) if isinstance(r, APIRoute) else r for r in kept]
    try:
        served = {
            (m, normalise(r.path))
            for r in router.routes
            if isinstance(r, APIRoute) and '{' in r.path
            for m in r.methods or ()
        }
        with TestClient(create_app(settings(tmp_path)), raise_server_exceptions=False) as client:
            leaks = unrefused(World(client))
    finally:
        router.routes[:] = kept
    assert {(leak.split(' ')[0], leak.split(' ')[1]) for leak in leaks} == served


# --- a phone sees every buddy ------------------------------------------------------------------


def test_a_phone_that_shows_pip_sees_marcels_conversation_tasks_items_and_cards(
    world: World,
) -> None:
    pips = world.pips_phone
    messages = world.call('GET', f'/api/conversations/{world.marcel_main}/messages', pips)
    assert world.message_id in [m['id'] for m in messages.json()['items']]
    tasks = world.call('GET', '/api/tasks', pips, params={'agent_id': world.marcel})
    assert [t['id'] for t in tasks.json()['items']] == [world.task_id]
    assert world.call('GET', f'/api/tasks/{world.task_id}', pips).status_code == 200
    items = world.call('GET', '/api/artifacts', pips).json()['items']
    assert world.artifact_id in [a['id'] for a in items]
    content = world.call('GET', f'/api/artifacts/{world.artifact_id}/content', pips)
    assert content.status_code == 200
    cards = world.call('GET', '/api/approvals', pips).json()
    assert world.approval_id in [c['id'] for c in cards]
    me = world.call('GET', '/api/me', pips).json()
    assert [a['id'] for a in me['agents']] == [world.marcel, world.pip]
    assert 'role' not in me


def test_a_phone_reaches_the_other_buddys_task_routes(world: World) -> None:
    """The person check is gone from `/tasks…` (S-17.2's `_not_yours`): steering, archiving and
    handing back reach Marcel's task from Pip's phone; a missing task is still 404."""
    pips = world.pips_phone
    res = world.call('POST', f'/api/tasks/{world.task_id}/archive', pips)
    assert res.status_code == 409 and res.json()['code'] == 'task_not_finished'  # reached it
    res = world.call('GET', f'/api/tasks/{world.task_id}/events', pips)
    assert res.status_code == 200
    for method, path in (('GET', '/api/tasks/tsk_nope'), ('POST', '/api/tasks/tsk_nope/stop')):
        res = world.call(method, path, pips)
        assert res.status_code == 404 and res.json()['code'] == 'task_not_found'
    res = world.call('POST', '/api/tasks/tsk_nope/messages', pips, {'text': 'x'})
    assert res.status_code == 404, 'the missing task is answered before the body'


def test_a_phone_moves_the_other_buddys_item_and_a_missing_one_is_404(world: World) -> None:
    pips = world.pips_phone
    res = world.call(
        'POST', f'/api/artifacts/{world.artifact_id}/move', pips, {'project_id': world.project_id}
    )
    assert res.status_code == 200, res.text
    for path in ('/api/artifacts/art_nope', '/api/artifacts/art_nope/versions'):
        assert world.call('GET', path, pips).json()['code'] == 'artifact_not_found'


def test_every_buddys_events_reach_every_phone(world: World) -> None:
    """One event log for the one person (ws.OwnerPublisher, channel approvals): Pip's task
    events land where Marcel's phone listens too."""
    pips_task = world.task(world.pip)
    world.app.state.tasks._publish_task(pips_task)
    world.app.state.artifacts.publish_hub_html('Plan', '<p>v1</p>', task_id=pips_task)
    with Session(world.engine) as s:
        people = {row.user_id for row in s.exec(select(AppEvent))}
    assert people == {tokens.OWNER_USER_ID}


def self_change_card(world: World, agent_id: str) -> str:
    """A self-change card on a quest of `agent_id`."""
    with Session(world.engine) as s:
        task = Task(
            agent_id=agent_id,
            title='Tweak Marcel',
            location=TaskLocation.cloud,
            state=TaskState.working,
            model='sonnet',
            origin=TaskOrigin.marcel,
        )
        s.add(task)
        s.commit()
        pr = Artifact(
            kind='pr',  # type: ignore[arg-type]
            title='Tweak',
            task_id=task.id,
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


def test_any_phone_answers_a_change_to_marcel(world: World) -> None:
    """S-17.1's owner-only answer is gone: Pip's phone denies a self-change of Marcel's."""
    card = self_change_card(world, world.marcel)
    assert card in [c['id'] for c in world.call('GET', '/api/approvals', world.pips_phone).json()]
    res = world.call('POST', f'/api/approvals/{card}', world.pips_phone, {'decision': 'deny'})
    assert res.status_code == 200, res.text
    assert res.json()['state'] == 'denied'


def test_a_self_change_card_shows_in_its_own_buddys_chat(world: World) -> None:
    """Pip's quest opened the PR: the card is posted in Pip's chat, not moved to Marcel's."""
    card_id = self_change_card(world, world.pip)
    with Session(world.engine) as s:
        card = s.get(Approval, card_id)
    assert card is not None
    world.app.state.selfmod.events = world.app.state.events
    world.app.state.selfmod._post_card(card)
    with Session(world.engine) as s:
        posted = s.exec(select(Message).where(Message.kind == 'approval')).all()
    assert [(m.conversation_id, m.body_json) for m in posted] == [
        (world.pip_main, {'approval_id': card_id})
    ]


def test_a_new_phone_shows_the_first_buddy(world: World) -> None:
    with Session(world.engine) as s:
        code = tokens.create_pairing_code(s)
    paired = world.client.post('/api/pair', json={'code': code, 'device_name': 'new'}).json()
    with Session(world.engine) as s:
        device = s.get(Device, paired['device_id'])
    assert device is not None and device.agent_id == world.marcel


def test_a_phone_whose_buddy_is_archived_shows_the_first_live_one(world: World) -> None:
    def shown() -> str:
        devices = world.call('GET', '/api/devices', world.pips_phone).json()
        return next(d['agent_id'] for d in devices if d['current'])

    assert shown() == world.pip
    with Session(world.engine) as s:
        pip = s.get(Agent, world.pip)
        assert pip is not None
        pip.archived_at = datetime.now(UTC)
        s.add(pip)
        s.commit()
    assert shown() == world.marcel
    me = world.call('GET', '/api/me', world.pips_phone).json()
    assert [a['id'] for a in me['agents']] == [world.marcel], 'an archived buddy is not listed'


def test_the_loop_catches_the_brain_routes_without_their_secret_check(tmp_path: Path) -> None:
    """The brain routes check the hub secret per route (`BrainDep`), not on the router. Let any
    caller in as Marcel's brain: each brain route with an id leaks."""
    brain_routes = {route for route, who in CLASSIFIED.items() if who == 'brain'}
    with TestClient(create_app(settings(tmp_path)), raise_server_exceptions=False) as client:
        world = World(client)
        as_marcel = Brain('ses_x', 'brain', world.marcel, tokens.OWNER_USER_ID, world.marcel_main)
        world.app.dependency_overrides[require_brain] = lambda: as_marcel
        leaks = unrefused(world)
    assert {(leak.split(' ')[0], leak.split(' ')[1]) for leak in leaks} == brain_routes


def test_a_phone_paired_before_any_buddy_exists_still_works(tmp_path: Path) -> None:
    """No buddy at all (`marcel-hub pair` before `setup`): the phone's `agent_id` is left out,
    its memory is empty, and nothing fails."""
    with TestClient(create_app(settings(tmp_path))) as client:
        with Session(client.app.state.engine) as s:  # type: ignore[attr-defined]
            code = tokens.create_pairing_code(s)
        paired = client.post('/api/pair', json={'code': code, 'device_name': 'early'}).json()
        phone = {'Authorization': f'Bearer {paired["token"]}'}
        devices = client.get('/api/devices', headers=phone)
        assert devices.status_code == 200 and 'agent_id' not in devices.json()[0]
        memory = client.get('/api/memory', headers=phone)
        assert memory.status_code == 200 and memory.json() == {'files': []}
        assert client.get('/api/me', headers=phone).json()['agents'] == []
