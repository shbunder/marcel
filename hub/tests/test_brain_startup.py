"""A brain that calls the hub before the runner has answered its spawn (found live on the NUC).

The runner answers `POST /sessions` only once the new session's channel is registered, but the
session is already running by then: its `SessionStart` hook and its first tool calls reach
`/brain` before the hub has written `brain_session_id`. While a fresh brain is starting, the hub
asks the runner and accepts the session the runner lists under that agent's brain name.

Nothing here reaches a real `claude`, a runner or the network."""

import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from test_brain import FakeBrains
from test_brain_app import brain_hub, current, keeper_of, wait
from test_channel_fakes import SECRET, ChannelHub
from test_tasks_fakes import run

from marcel_hub import brain
from marcel_hub.brain import brain_name


@pytest.fixture(autouse=True)
def _fast_ticks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(brain, 'TICK_S', 0.05)


@pytest.fixture
def fake() -> FakeBrains:
    runner = FakeBrains()
    runner.list_before_return = True
    runner.spawn_delay = 1.5  # the runner waiting for the channel to register
    return runner


@pytest.fixture
def hub(tmp_path: Path, fake: FakeBrains) -> Iterator[ChannelHub]:
    with brain_hub(tmp_path, fake) as hub:
        yield hub


def tasks_as(hub: ChannelHub, session_id: str) -> int:
    headers = {'X-Marcel-Secret': SECRET, 'X-Marcel-Session': session_id}
    return hub.client.get('/brain/tasks', headers=headers).status_code


def starting_brain(hub: ChannelHub, fake: FakeBrains) -> str:
    """The new brain's id, as soon as the runner lists it (its spawn not answered yet)."""
    return wait(lambda: next(iter(fake.by_id), None))


def test_a_starting_brain_is_known_before_its_spawn_returns(
    hub: ChannelHub, fake: FakeBrains
) -> None:
    sid = starting_brain(hub, fake)
    assert fake.by_id[sid]['name'] == brain_name(hub.agent_id)
    assert current(hub) is None, 'the spawn has not returned yet'
    assert tasks_as(hub, sid) == 200, 'its SessionStart hook and first tool call get through'
    assert current(hub) == sid
    wait(lambda: len(fake.spawned) == 1 and current(hub) == sid)
    time.sleep(2 * fake.spawn_delay)
    assert current(hub) == sid and len(fake.spawned) == 1, 'the spawn returning changes nothing'


def test_only_the_session_listed_as_this_brain_is_let_in(hub: ChannelHub, fake: FakeBrains) -> None:
    starting_brain(hub, fake)
    other = fake.add(name='harry-worker')  # listed, but not as this agent's brain
    assert tasks_as(hub, other) == 401
    assert tasks_as(hub, str(uuid.uuid4())) == 401  # not listed at all
    assert current(hub) is None


def test_no_start_under_way_means_no_runner_call_and_no_claim(
    hub: ChannelHub, fake: FakeBrains
) -> None:
    # Control: delete the "only while starting" check in `claim` and this goes red.
    sid = starting_brain(hub, fake)
    wait(lambda: current(hub) == sid)
    keeper = keeper_of(hub)
    wait(lambda: not keeper._state(hub.agent_id).starting)
    asked: list[int] = []
    listing = fake.sessions

    async def counting() -> list[dict[str, Any]]:
        asked.append(1)
        return await listing()

    keeper.runner = SimpleNamespace(sessions=counting)  # type: ignore[assignment]
    stranger = fake.add(name=brain_name(hub.agent_id))  # even one listed like the brain
    assert run(keeper.claim(stranger)) is False
    assert asked == [], 'no brain starting: the runner is not asked'
    assert current(hub) == sid


def test_a_session_the_runner_did_not_start_as_a_brain_is_refused(
    hub: ChannelHub, fake: FakeBrains
) -> None:
    starting_brain(hub, fake)
    # Someone ran `claude --bg --name brain-<agent>` on the NUC: listed, same name, no brain role.
    impostor = fake.add(name=brain_name(hub.agent_id), role='')
    assert tasks_as(hub, impostor) == 401
    assert current(hub) != impostor


def test_an_unreachable_runner_claims_nothing(hub: ChannelHub, fake: FakeBrains) -> None:
    sid = starting_brain(hub, fake)
    fake.unreachable = True
    assert tasks_as(hub, sid) == 401
    fake.unreachable = False
