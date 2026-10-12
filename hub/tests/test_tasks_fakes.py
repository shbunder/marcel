"""Fakes and helpers shared by the task tests. Nothing here reaches a runner or the network."""

import asyncio
import json
from collections.abc import Coroutine
from pathlib import Path
from typing import Any

import pytest
import yaml
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from sqlalchemy.engine import Engine
from sqlmodel import Session
from test_contract import PENDING

from marcel_hub.models import Agent, Task, TaskLocation, TaskOrigin, TaskState
from marcel_hub.tasks.ports import CapReached, MemoryOutbox, RunnerUnavailable, Started, StartFailed
from marcel_hub.tasks.registry import TaskRegistry

CONTRACTS = Path(__file__).resolve().parents[2] / 'contracts'
BASE = 'https://marcel.invalid/contracts/'


def run[T](coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)


class FakeRunner:
    """A runner with a worker cap. `down` makes every call fail as unreachable."""

    def __init__(self, cap: int = 4) -> None:
        self.cap = cap
        self.running: list[str] = []
        self.started: list[tuple[str, str]] = []  # (task id, prompt)
        self.stopped: list[str] = []
        self.fail_with: str | None = None
        self.down = False

    async def start(self, task: Task, prompt: str) -> Started:
        if self.down:
            raise RunnerUnavailable('The runner is not answering.')
        if self.fail_with:
            raise StartFailed(self.fail_with)
        if len(self.running) >= self.cap:
            raise CapReached()
        session_id = f'ses-{task.id}'
        self.running.append(session_id)
        self.started.append((task.id, prompt))
        return Started(session_id=session_id)

    async def stop(self, session_id: str) -> None:
        if self.down:
            raise RunnerUnavailable('The runner is not answering.')
        self.stopped.append(session_id)
        if session_id in self.running:
            self.running.remove(session_id)


class RecordingPublisher:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict[str, Any]]] = []

    def publish(self, type: str, data: dict[str, Any]) -> None:
        self.sent.append((type, data))


AGENT = 'agt_marcel'


def add_agent(engine: Engine, agent_id: str = AGENT) -> None:
    with Session(engine) as s:
        s.add(Agent(id=agent_id, owner_user_id='usr_1', name='Marcel', animal='giraffe',
                    palette='pink', seed='1'))  # fmt: skip
        s.commit()


def put_task(engine: Engine, state: TaskState, agent_id: str = AGENT, **fields: Any) -> str:
    """A task row in `state`, written directly: the starting point for a transition test."""
    task = Task(
        agent_id=agent_id,
        title=f'A {state.value} task',
        location=TaskLocation.nuc,
        state=state,
        model='sonnet',
        origin=TaskOrigin.marcel,
        **fields,
    )
    with Session(engine) as s:
        s.add(task)
        s.commit()
        return task.id


@pytest.fixture(name='runner')
def runner_fixture() -> FakeRunner:
    return FakeRunner()


@pytest.fixture(name='outbox')
def outbox_fixture() -> MemoryOutbox:
    return MemoryOutbox()


@pytest.fixture(name='publisher')
def publisher_fixture() -> RecordingPublisher:
    return RecordingPublisher()


@pytest.fixture(name='registry')
def registry_fixture(
    engine: Engine, runner: FakeRunner, outbox: MemoryOutbox, publisher: RecordingPublisher
) -> TaskRegistry:
    add_agent(engine)
    return TaskRegistry(engine, runner, outbox, publisher)


def _registry() -> Registry:
    api = yaml.safe_load((CONTRACTS / 'app-api.yaml').read_text())
    api['$schema'] = 'https://json-schema.org/draft/2020-12/schema'
    # Contract fields no story stores yet are not required here (none since S-04.11);
    # this follows test_contract.PENDING, so the check tightens when that list shrinks.
    for schema, fields in PENDING.items():
        node = api['components']['schemas'][schema]
        node['required'] = [f for f in node.get('required', []) if f not in fields]
    registry: Registry = Registry()
    for name, doc in (
        ('app-api.yaml', api),
        ('transcript.schema.json', json.loads((CONTRACTS / 'transcript.schema.json').read_text())),
        ('channel.schema.json', json.loads((CONTRACTS / 'channel.schema.json').read_text())),
        ('events.schema.json', json.loads((CONTRACTS / 'events.schema.json').read_text())),
    ):
        registry = registry.with_resource(BASE + name, Resource.from_contents(doc))
    return registry


REGISTRY = _registry()


def validator(ref: str) -> Draft202012Validator:
    """A validator for e.g. `app-api.yaml#/components/schemas/Task`."""
    return Draft202012Validator({'$ref': BASE + ref}, registry=REGISTRY)
