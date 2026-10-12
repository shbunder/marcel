"""`POST /api/session/adopt` (S-05.4, B-14): a session the owner hands over with `/marcel-adopt`
becomes an adopted task with a milestone in the main conversation; a NUC session's transcript is
followed. Nothing here reaches a runner, `claude` or the network."""

import asyncio
import stat
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from jsonschema import Draft202012Validator
from sqlmodel import Session, col, select
from test_channel_fakes import ChannelHub, channel_hub
from test_runner_client_fakes import FakeRunnerServer, RecordingAlerts, Sleeps, until, watch_for
from test_tasks_fakes import (  # noqa: F401  (fixtures)
    outbox_fixture,
    publisher_fixture,
    put_task,
    registry_fixture,
    runner_fixture,
    validator,
)

from marcel_hub import adopt
from marcel_hub.adopt import Adopter, AdoptRequest, TokenCheck, new_adopt_token
from marcel_hub.app import main
from marcel_hub.models import (
    Agent,
    Message,
    MessageKind,
    Task,
    TaskEvent,
    TaskLocation,
    TaskOrigin,
    TaskState,
)
from marcel_hub.tasks.ports import RunnerUnavailable
from marcel_hub.tasks.registry import TaskRegistry

CONTRACTS = Path(__file__).resolve().parents[2] / 'contracts'
SESSION_API = yaml.safe_load((CONTRACTS / 'session-api.yaml').read_text())
ADOPTED = Draft202012Validator(SESSION_API['components']['schemas']['Adopted'])
TASK = validator('app-api.yaml#/components/schemas/Task')
MESSAGE = validator('app-api.yaml#/components/schemas/Message')
URL = '/api/session/adopt'
CWD = '/home/owner/projects/harry'


def local(**fields: Any) -> dict[str, Any]:
    return {
        'session_id': str(uuid.uuid4()),
        'kind': 'local',
        'title': 'Refactor the Harry digest renderer',
        'cwd': CWD,
        'machine': 'bunderserver',
        **fields,
    }


def cloud(**fields: Any) -> dict[str, Any]:
    return {'session_id': 'session_0168oBNCs8Q2BrHDFu6tYUdh', 'kind': 'cloud',
            'title': 'Write the release notes', **fields}  # fmt: skip


class FakeWatch:
    """What the adopter needs from the runner watcher: the runner's session list and `follow`."""

    def __init__(self, sessions: list[str] | None = None) -> None:
        self.listed = sessions or []
        self.followed: list[tuple[str, str]] = []
        self.client = self
        self.down = False

    async def sessions(self) -> list[dict[str, Any]]:
        if self.down:
            raise RunnerUnavailable('The runner socket is not there.')
        return [{'id': sid} for sid in self.listed]

    def follow(self, task_id: str, session_id: str) -> None:
        self.followed.append((task_id, session_id))


@pytest.fixture
def hub(tmp_path: Path) -> Iterator[ChannelHub]:
    with channel_hub(tmp_path) as hub:
        yield hub


@pytest.fixture
def token(tmp_path: Path) -> str:
    return new_adopt_token(tmp_path)


def post(hub: ChannelHub, body: Any, token: str | None, **kw: Any) -> Any:
    headers = {'Authorization': f'Bearer {token}' if token else ''}
    return hub.client.post(URL, json=body, headers=headers, **kw)


def tasks(hub: ChannelHub) -> list[Task]:
    with Session(hub.engine) as s:
        return list(s.exec(select(Task)))


def milestones(hub: ChannelHub) -> list[Message]:
    with Session(hub.engine) as s:
        return list(
            s.exec(
                select(Message)
                .where(Message.conversation_id == hub.main_id)
                .where(Message.kind == MessageKind.milestone)
                .order_by(col(Message.created_at))
            )
        )


def hidden_404(hub: ChannelHub) -> dict[str, Any]:
    """What the hub answers for an address it does not have."""
    res = hub.client.post('/api/session/nothing-here', json={})
    assert res.status_code == 404
    return res.json()


# Done when: adopt creates a task and a message ------------------------------------------------


def test_adopt_creates_an_adopted_task_and_a_milestone_in_the_main_chat(
    hub: ChannelHub, token: str
) -> None:
    body = local()
    res = post(hub, body, token)

    assert res.status_code == 201, res.text
    ADOPTED.validate(res.json())
    assert res.json()['tracked'] == 'links_only'  # no runner in this app
    [task] = tasks(hub)
    assert res.json()['task_id'] == task.id
    assert task.origin == TaskOrigin.adopted
    assert task.state == TaskState.working
    assert task.location == TaskLocation.nuc
    assert task.session_id == body['session_id']
    assert task.title == 'Refactor the Harry digest renderer'
    assert task.agent_id == hub.agent_id
    TASK.validate(hub.registry.to_api(task))

    [msg] = milestones(hub)
    assert msg.task_id == task.id
    assert msg.body_json['milestone'] == {
        'task_id': task.id,
        'task_title': 'Refactor the Harry digest renderer',
        'event': 'adopted',
        'location': 'nuc',
    }
    shown = hub.client.get(f'/api/conversations/{hub.main_id}/messages').json()
    MESSAGE.validate(next(m for m in shown['items'] if m['id'] == msg.id))

    with Session(hub.engine) as s:
        notes = [e.data_json['text'] for e in s.exec(select(TaskEvent)) if e.type.value == 'note']
    assert notes == [f'Handed over with /marcel-adopt from bunderserver in {CWD}.']


def test_the_same_session_adopted_twice_is_one_task(hub: ChannelHub, token: str) -> None:
    body = local()
    first = post(hub, body, token)
    again = post(hub, {**body, 'title': 'Another name'}, token)

    assert (first.status_code, again.status_code) == (201, 200)
    assert again.json() == first.json()
    assert len(tasks(hub)) == 1
    assert len(milestones(hub)) == 1


def test_a_session_marcel_started_is_already_tracked(hub: ChannelHub, token: str) -> None:
    task_id, sid = hub.task()
    res = post(hub, local(session_id=sid), token)
    assert res.status_code == 200
    assert res.json()['task_id'] == task_id
    assert milestones(hub) == []


def test_a_cloud_session_keeps_its_link_and_is_not_tailed(hub: ChannelHub, token: str) -> None:
    watch = FakeWatch(['session_0168oBNCs8Q2BrHDFu6tYUdh'])
    hub.app.state.adopter.watch = watch
    res = post(hub, cloud(), token)

    assert res.status_code == 201, res.text
    assert res.json()['tracked'] == 'links_only'
    [task] = tasks(hub)
    assert task.location == TaskLocation.cloud
    assert task.cloud_url == 'https://claude.ai/code/session_0168oBNCs8Q2BrHDFu6tYUdh'
    assert watch.followed == []
    assert milestones(hub)[0].body_json['milestone']['location'] == 'cloud'


def test_a_session_the_runner_sees_is_followed_live(hub: ChannelHub, token: str) -> None:
    body = local()
    watch = FakeWatch([str(uuid.uuid4()), body['session_id']])
    hub.app.state.adopter.watch = watch
    res = post(hub, body, token)

    assert res.status_code == 201
    assert res.json()['tracked'] == 'live'
    assert watch.followed == [(res.json()['task_id'], body['session_id'])]


def test_a_local_session_the_runner_does_not_see_is_links_only(hub: ChannelHub, token: str) -> None:
    watch = FakeWatch([str(uuid.uuid4())])
    hub.app.state.adopter.watch = watch
    assert post(hub, local(), token).json()['tracked'] == 'links_only'
    watch.down = True
    assert post(hub, local(), token).json()['tracked'] == 'links_only'
    assert watch.followed == []


def test_a_given_url_is_kept(hub: ChannelHub, token: str) -> None:
    url = 'https://claude.ai/code/session_01remote'
    post(hub, local(url=url), token)
    assert tasks(hub)[0].remote_url == url


# The token ------------------------------------------------------------------------------------


@pytest.mark.parametrize('given', [None, 'mra_wrong', 'Basic abc'])
def test_without_the_right_token_the_route_looks_absent(
    hub: ChannelHub, token: str, given: str | None
) -> None:
    headers = {'Authorization': given if given == 'Basic abc' else f'Bearer {given or ""}'}
    res = hub.client.post(URL, json=local(), headers=headers)
    assert res.status_code == 404
    assert res.json() == hidden_404(hub)
    assert tasks(hub) == []


def test_a_device_token_is_not_an_adopt_token(hub: ChannelHub, token: str) -> None:
    res = post(hub, local(), hub.token)
    assert res.status_code == 404
    assert tasks(hub) == []


def test_a_bad_body_with_a_wrong_token_is_still_hidden(hub: ChannelHub, token: str) -> None:
    res = post(hub, {'nonsense': True}, 'mra_wrong')
    assert res.status_code == 404


def test_a_hub_without_an_adopt_token_refuses_every_call(
    hub: ChannelHub, caplog: pytest.LogCaptureFixture
) -> None:
    for _ in range(2):
        assert post(hub, local(), 'mra_anything').status_code == 404
    assert tasks(hub) == []
    assert caplog.text.count('marcel-hub adopt-token') == 1  # said once, not per call


def test_too_many_wrong_tokens_lock_the_route_for_a_minute(hub: ChannelHub, token: str) -> None:
    clock = [1000.0]
    hub.app.state.adopter.tokens.clock = lambda: clock[0]
    for _ in range(adopt.MAX_WRONG_TOKENS):
        assert post(hub, local(), 'mra_guess').status_code == 404
    locked = post(hub, local(), token)
    assert locked.status_code == 429
    assert locked.json()['code'] == 'too_many_tries'
    assert locked.headers['Retry-After'] == '60'
    clock[0] += adopt.WRONG_TOKEN_WINDOW
    assert post(hub, local(), token).status_code == 201


def test_a_new_token_replaces_the_old_one_and_only_its_hash_is_kept(
    hub: ChannelHub, tmp_path: Path, token: str
) -> None:
    newer = new_adopt_token(tmp_path)
    path = tmp_path / adopt.TOKEN_FILE
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert newer not in path.read_text() and newer.startswith('mra_')
    assert post(hub, local(), token).status_code == 404
    assert post(hub, local(), newer).status_code == 201


def test_marcel_hub_adopt_token_prints_a_working_token_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv('MARCEL_CONFIG', str(tmp_path / 'none.toml'))
    monkeypatch.setenv('MARCEL_DATA_DIR', str(tmp_path))
    with pytest.raises(SystemExit) as done:
        main(['adopt-token'])
    assert done.value.code == 0
    out = capsys.readouterr().out
    [token] = [w for w in out.split() if w.startswith('mra_')]
    TokenCheck(tmp_path / adopt.TOKEN_FILE).check(f'Bearer {token}')
    assert '~/.marcel/adopt-token' in out and 'umask 077' in out


def test_marcel_hub_adopt_token_takes_no_arguments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv('MARCEL_DATA_DIR', str(tmp_path))
    with pytest.raises(SystemExit) as done:
        main(['adopt-token', 'extra'])
    assert done.value.code == 2
    assert not (tmp_path / adopt.TOKEN_FILE).exists()


def test_marcel_hub_adopt_token_says_when_settings_are_broken(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broken = tmp_path / 'broken.toml'
    broken.write_text('this is = = not toml')
    monkeypatch.setenv('MARCEL_CONFIG', str(broken))
    with pytest.raises(SystemExit, match='No adopt token was made'):
        main(['adopt-token'])


# Checking the request -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ('body', 'says'),
    [
        (local(session_id='not-a-session'), 'local session id'),
        (local(session_id='session_0168oBNCs8Q2BrHDFu6tYUdh'), 'local session id'),
        (cloud(session_id=str(uuid.uuid4())), 'cloud session id'),
        (local(cwd='projects/harry'), 'full path'),
        (local(cwd=None), 'needs `cwd`'),
        (local(title=''), 'title'),
        (local(title='   '), 'title'),
        (local(title='x' * 121), 'title'),
        (local(title='two\nlines'), 'one line'),
        (local(machine='bad host!'), 'host name'),
        (local(url='http://claude.ai/code/x'), 'https'),
        (local(kind='laptop'), 'kind'),
        (local(token='mra_x'), 'token'),
        ({'kind': 'local'}, 'session_id'),
    ],
)
def test_a_bad_request_gets_a_400_that_says_what_to_change(
    hub: ChannelHub, token: str, body: dict[str, Any], says: str
) -> None:
    body = {k: v for k, v in body.items() if v is not None}
    res = post(hub, body, token)
    assert res.status_code == 400, res.text
    assert res.json()['code'] == 'bad_request'
    assert says in res.json()['message']
    assert tasks(hub) == []


def test_a_body_that_is_not_json_or_too_big_is_refused(hub: ChannelHub, token: str) -> None:
    headers = {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}
    assert hub.client.post(URL, content=b'{nope', headers=headers).status_code == 400
    big = hub.client.post(URL, content=b' ' * (adopt.MAX_BODY + 1), headers=headers)
    assert big.status_code == 400
    assert 'too big' in big.json()['message']


def test_a_hub_with_no_agent_says_so(tmp_path: Path, token: str) -> None:
    with channel_hub(tmp_path) as hub, Session(hub.engine) as s:
        for agent in s.exec(select(Agent)):
            agent.main_conversation_id = None
            s.add(agent)
        s.commit()
        res = post(hub, local(), token)
    assert res.status_code == 400
    assert res.json()['code'] == 'no_agent'


# One task per session, even when two calls race ----------------------------------------------


def test_two_calls_at_once_for_one_session_make_one_task(hub: ChannelHub) -> None:
    adopter: Adopter = hub.app.state.adopter
    req = AdoptRequest.model_validate(local())

    async def both() -> list[tuple[dict[str, str], bool]]:
        return await asyncio.gather(adopter.adopt(req), adopter.adopt(req))

    results = asyncio.run(both())
    assert sorted(created for _, created in results) == [False, True]
    assert results[0][0] == results[1][0]
    assert len(tasks(hub)) == 1


# The runner watcher follows an adopted session ------------------------------------------------


def test_follow_reads_a_session_the_runner_did_not_start(registry: TaskRegistry) -> None:
    fake = FakeRunnerServer()
    sid = fake.add_session(state='working')
    task_id = put_task(registry.engine, TaskState.working, session_id=sid)
    fake.write(sid, (120, 0, 'text', {'role': 'assistant', 'text': 'Reading the renderer.'}))

    async def scenario() -> None:
        watch = watch_for(fake, registry, RecordingAlerts(), Sleeps())
        watch.follow(task_id, sid)

        def texts() -> list[str]:
            with Session(registry.engine) as s:
                rows = s.exec(select(TaskEvent).where(TaskEvent.task_id == task_id))
                return [r.data_json.get('text', '') for r in rows if r.type.value == 'text']

        await until(lambda: texts() == ['Reading the renderer.'])
        await watch.close()

    asyncio.run(scenario())
