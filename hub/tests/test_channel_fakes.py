"""A hub with a channel secret, and a fake channel server, for the channel gateway tests.

`FakeChannel` speaks the channel protocol over the app's real `/channel` socket, as the marcel
plugin's channel server does (plugins/marcel/channel/src/hub.ts). Nothing here reaches a real
`claude`, a runner or the network.
"""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from sqlmodel import Session, col, select
from starlette.testclient import WebSocketTestSession
from test_runner_client_fakes import RecordingAlerts
from test_tasks_fakes import FakeRunner, validator

from marcel_hub import conversations
from marcel_hub.app import Settings, create_app
from marcel_hub.auth import tokens
from marcel_hub.channel import Gateway
from marcel_hub.channel.ports import LogPush
from marcel_hub.models import (
    Agent,
    AppEvent,
    Conversation,
    ConversationKind,
    Device,
    Message,
    Task,
    TaskEvent,
    TaskLocation,
    TaskOrigin,
    TaskState,
)
from marcel_hub.tasks.registry import TaskRegistry

SECRET = 'hs_' + 'a1b2c3d4' * 6
OWNER = tokens.OWNER_USER_ID

TO_SESSION = {
    name: validator(f'channel.schema.json#/$defs/{name}')
    for name in (
        'Welcome',
        'Rejected',
        'UserMessage',
        'Steer',
        'SystemEvent',
        'PermissionDecision',
        'CallAck',
        'Pong',
    )
}
SCHEMA_OF = {
    'welcome': 'Welcome',
    'rejected': 'Rejected',
    'user_message': 'UserMessage',
    'steer': 'Steer',
    'system_event': 'SystemEvent',
    'permission_decision': 'PermissionDecision',
    'call_ack': 'CallAck',
    'pong': 'Pong',
}
APPROVAL = validator('app-api.yaml#/components/schemas/Approval')
MESSAGE = validator('app-api.yaml#/components/schemas/Message')
APP_EVENT = validator('events.schema.json')


def session_id() -> str:
    return str(uuid.uuid4())


def check(frame: dict[str, Any]) -> dict[str, Any]:
    """Every frame the hub sends must match channel.schema.json."""
    TO_SESSION[SCHEMA_OF[frame['type']]].validate(frame)
    return frame


class FakeChannel:
    """One channel server's socket: hello, then frames both ways."""

    def __init__(self, ws: WebSocketTestSession) -> None:
        self.ws = ws
        self.welcome: dict[str, Any] | None = None
        self.calls = 0

    def hello(self, sid: str, secret: str = SECRET, version: str = '0.1.0', **kw: Any) -> Any:
        self.ws.send_json(
            {'type': 'hello', 'session_id': sid, 'secret': secret, 'plugin_version': version, **kw}
        )
        frame = check(self.ws.receive_json())
        if frame['type'] == 'welcome':
            self.welcome = frame
        return frame

    def send(self, **frame: Any) -> None:
        self.ws.send_json(frame)

    def next(self) -> dict[str, Any]:
        """The next hub frame (pongs skipped)."""
        while True:
            frame = check(self.ws.receive_json())
            if frame['type'] != 'pong':
                return frame

    def until_pong(self) -> list[dict[str, Any]]:
        """Send a ping and return the answers that come before the pong. The hub answers frames
        in order, so this shows what it answered to everything sent before."""
        self.send(type='ping')
        got = []
        while True:
            frame = check(self.ws.receive_json())
            if frame['type'] == 'pong':
                return got
            got.append(frame)

    def call(self, frame_type: str, call_id: str | None = None, **fields: Any) -> dict[str, Any]:
        """A `reply` or `report` tool call; returns the hub's answer."""
        self.calls += 1
        call_id = call_id or f'c_{self.calls:04d}'
        self.send(type=frame_type, call_id=call_id, **fields)
        return self.next()

    def deliver(self) -> dict[str, Any]:
        """Take the next sequenced frame and ack it, as the server does once Claude Code has it."""
        frame = self.next()
        self.send(type='ack', seq=frame['seq'])
        return frame


class ChannelHub:
    """The real app, with a hub secret on disk, a fake runner, an agent and a paired phone."""

    def __init__(self, client: TestClient, tmp_path: Path) -> None:
        self.client = client
        self.tmp_path = tmp_path
        app: Any = client.app
        self.app = app
        self.engine = app.state.engine
        self.gateway: Gateway = app.state.channel
        self.registry: TaskRegistry = app.state.tasks
        self.runner = FakeRunner()
        self.registry.runner = self.runner
        # The real app pushes and alerts through the notifier (test_the_gateway_uses_the_
        # notifier_and_the_artifact_store); here they are recorded instead.
        self.pushes = LogPush()
        self.gateway.push = self.pushes
        self.alerts = RecordingAlerts()
        self.gateway.alerts = self.alerts
        # Workers' files, as the hub sees them: reported doc/file/dashboard paths are read here.
        self.workers = tmp_path / 'workers'
        self.workers.mkdir()
        app.state.artifacts.files_root = self.workers
        with Session(self.engine) as s:
            agent = Agent(owner_user_id=OWNER, name='Marcel', animal='giraffe', palette='pink',
                          seed='1')  # fmt: skip
            s.add(agent)
            s.commit()
            self.agent_id = agent.id
            self.main_id = conversations.ensure_main_conversation(s, agent).id
        self.token = self.phone(OWNER)
        self.client.headers['Authorization'] = f'Bearer {self.token}'

    def phone(self, user_id: str) -> str:
        token = tokens.new_token()
        with Session(self.engine) as s:
            s.add(Device(user_id=user_id, name='iPhone', token_hash=tokens.hash_secret(token)))
            s.commit()
        return token

    # Seeding ----------------------------------------------------------------------------------

    def task(
        self, state: TaskState = TaskState.working, sid: str | None = None, **fields: Any
    ) -> tuple[str, str]:
        """A NUC task whose session the runner reported: (task id, session id)."""
        sid = sid or session_id()
        task = Task(
            agent_id=self.agent_id,
            title=fields.pop('title', 'Check disk usage'),
            location=fields.pop('location', TaskLocation.nuc),
            state=state,
            model='sonnet',
            origin=fields.pop('origin', TaskOrigin.marcel),
            session_id=sid,
            **fields,
        )
        with Session(self.engine) as s:
            s.add(task)
            s.commit()
            return task.id, sid

    def brain(self, sid: str | None = None) -> str:
        """Give the main conversation a brain session; returns its id."""
        sid = sid or session_id()
        with Session(self.engine) as s:
            conv = s.get(Conversation, self.main_id)
            assert conv is not None
            conv.brain_session_id = sid
            s.add(conv)
            s.commit()
        return sid

    def side(self, task_id: str | None = None, sid: str | None = None) -> tuple[str, str]:
        """A side conversation (a fork of the brain, or a task's thread): (id, session id)."""
        sid = sid or session_id()
        with Session(self.engine) as s:
            conv = Conversation(
                agent_id=self.agent_id,
                kind=ConversationKind.side,
                task_id=task_id,
                brain_session_id=sid,
            )
            s.add(conv)
            s.commit()
            return conv.id, sid

    # Reading ----------------------------------------------------------------------------------

    def events(self, type_: str | None = None) -> list[dict[str, Any]]:
        with Session(self.engine) as s:
            rows = s.exec(select(AppEvent).where(AppEvent.user_id == OWNER).order_by(AppEvent.id))  # pyright: ignore[reportArgumentType]
            frames = [
                {'id': e.id, 'type': e.type, 'at': e.at.isoformat(), 'data': e.data_json}
                for e in rows
            ]
        for frame in frames:
            APP_EVENT.validate(frame)
        return [f for f in frames if type_ is None or f['type'] == type_]

    def messages(self, conversation_id: str | None = None) -> list[Message]:
        with Session(self.engine) as s:
            return list(
                s.exec(
                    select(Message)
                    .where(Message.conversation_id == (conversation_id or self.main_id))
                    .order_by(col(Message.created_at))
                )
            )

    def task_events(self, task_id: str, *types: str) -> list[TaskEvent]:
        with Session(self.engine) as s:
            rows = s.exec(
                select(TaskEvent).where(TaskEvent.task_id == task_id).order_by(col(TaskEvent.seq))
            )
            return [r for r in rows if not types or r.type.value in types]

    def approvals(self, **params: Any) -> list[dict[str, Any]]:
        res = self.client.get('/api/approvals', params=params)
        assert res.status_code == 200, res.text
        for body in res.json():
            APPROVAL.validate(body)
        return res.json()

    def answer(self, approval_id: str, decision: str, **kw: Any) -> Any:
        return self.client.post(f'/api/approvals/{approval_id}', json={'decision': decision, **kw})

    @contextmanager
    def connect(self) -> Iterator[FakeChannel]:
        with self.client.websocket_connect('/channel') as ws:
            yield FakeChannel(ws)


@contextmanager
def channel_hub(tmp_path: Path, secret: str | None = SECRET) -> Iterator[ChannelHub]:
    if secret is not None:
        (tmp_path / 'hub-secret').write_text(secret + '\n')
    app = create_app(Settings(data_dir=tmp_path, db_path=tmp_path / 'marcel.db'))
    with TestClient(app) as client:
        yield ChannelHub(client, tmp_path)


def permission(request_id: str = 'qiwxo', **kw: Any) -> dict[str, Any]:
    return {
        'type': 'permission_request',
        'request_id': request_id,
        'tool_name': kw.pop('tool_name', 'Bash'),
        'description': kw.pop('description', 'Run docker system df -v'),
        'input_preview': kw.pop('input_preview', '{ "command": "docker system df -v" }'),
        **kw,
    }
