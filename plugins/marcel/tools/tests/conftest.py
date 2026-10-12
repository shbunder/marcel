"""A real hub, in-process, for the tools to call: the hub app from `hub/` with a hub secret, a fake
runner, an agent and a brain session. Nothing reaches a network, a runner or `claude`."""

import json
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from marcel_hub import conversations
from marcel_hub.app import Settings, create_app
from marcel_hub.models import Agent, Conversation, Task
from marcel_hub.tasks.ports import RunnerUnavailable, Started
from sqlmodel import Session

from marcel_tools.hub import Config, Hub
from marcel_tools.server import Server
from marcel_tools.tools import call

SECRET = 'hs_' + 'a1b2c3d4' * 6
OWNER = 'usr_owner'


class FakeRunner:
    def __init__(self) -> None:
        self.started: list[tuple[str, str]] = []
        self.stopped: list[str] = []
        self.down = False

    async def start(self, task: Task, prompt: str) -> Started:
        if self.down:
            raise RunnerUnavailable('The runner is not answering.')
        self.started.append((task.id, prompt))
        return Started(session_id=f'ses-{task.id}')

    async def stop(self, session_id: str) -> None:
        if self.down:
            raise RunnerUnavailable('The runner is not answering.')
        self.stopped.append(session_id)


class ClientTransport:
    """The tools' `Transport` over the hub's FastAPI test client."""

    def __init__(self, client: TestClient) -> None:
        self.client = client

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None,
        body: Any,
        headers: dict[str, str],
    ) -> tuple[int, Any]:
        res = self.client.request(method, path, params=params, json=body, headers=headers)
        return res.status_code, res.json() if res.content else None


@dataclass
class World:
    client: TestClient
    app: Any
    runner: FakeRunner
    agent_id: str
    main_id: str
    brain_session: str
    data_dir: Path

    def hub(self, session_id: str | None = None, secret: str = SECRET) -> Hub:
        config = Config('http://hub.test', session_id or self.brain_session, secret)
        return Hub(config, ClientTransport(self.client))

    def call(self, name: str, args: Any = None, hub: Hub | None = None) -> tuple[str, bool]:
        """One MCP `tools/call`, through the server, as Claude Code sends it."""
        out: list[str] = []
        tool_hub = hub or self.hub()
        server = Server(lambda n, a: call(tool_hub, n, a), out.append)
        server.handle_line(json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                                       'params': {'name': name, 'arguments': args}}))  # fmt: skip
        result = json.loads(out[0])['result']
        return result['content'][0]['text'], result.get('isError', False)

    def ok(self, name: str, args: Any = None) -> str:
        text, is_error = self.call(name, args)
        assert not is_error, text
        return text

    def error(self, name: str, args: Any = None, hub: Hub | None = None) -> str:
        text, is_error = self.call(name, args, hub)
        assert is_error, text
        return text


@pytest.fixture
def world(tmp_path: Path) -> Iterator[World]:
    (tmp_path / 'hub-secret').write_text(SECRET + '\n')
    app = create_app(Settings(data_dir=tmp_path, db_path=tmp_path / 'marcel.db'))
    with TestClient(app) as client:
        runner = FakeRunner()
        app.state.tasks.runner = runner
        brain_session = str(uuid.uuid4())
        with Session(app.state.engine) as s:
            agent = Agent(owner_user_id=OWNER, name='Marcel', animal='giraffe', palette='pink',
                          seed='1')  # fmt: skip
            s.add(agent)
            s.commit()
            main = conversations.ensure_main_conversation(s, agent)
            conv = s.get(Conversation, main.id)
            assert conv is not None
            conv.brain_session_id = brain_session
            s.add(conv)
            s.commit()
            agent_id, main_id = agent.id, main.id
        yield World(client, app, runner, agent_id, main_id, brain_session, tmp_path)
