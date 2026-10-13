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


# --- a rollover's new brain (found live on the NUC: its SessionStart hook got 401 six times) ------


def rolling_over(hub: ChannelHub, fake: FakeBrains) -> tuple[str, str]:
    """(old brain, new brain): the old one current, the new one listed by the runner but not
    handed the conversation (FakeBrains never says hello)."""
    old = wait(lambda: current(hub))
    wait(lambda: not keeper_of(hub)._state(hub.agent_id).starting)
    assert keeper_of(hub).roll_over(hub.agent_id)
    new = wait(lambda: next((s for s in fake.by_id if s != old), None))
    return old, new


def test_a_rollovers_brain_reads_its_context_before_the_hand_over(
    hub: ChannelHub, fake: FakeBrains
) -> None:
    """Its `SessionStart` hook asks for the open threads before its hello. Delete `incoming` in
    `require_brain` and both reads go 401."""
    old, new = rolling_over(hub, fake)
    assert tasks_as(hub, new) == 200, 'while its spawn is still under way'
    wait(lambda: keeper_of(hub)._state(hub.agent_id).pending is not None)
    assert tasks_as(hub, new) == 200, 'and while the keeper waits for its hello'
    assert current(hub) == old, 'reading hands it nothing: the old brain keeps the conversation'


def test_a_rollovers_brain_cannot_act_before_the_hand_over(
    hub: ChannelHub, fake: FakeBrains
) -> None:
    old, new = rolling_over(hub, fake)
    headers = {'X-Marcel-Secret': SECRET, 'X-Marcel-Session': new}
    res = hub.client.post('/brain/notify', headers=headers, json={'title': 't', 'body': 'b'})
    assert res.status_code == 401
    assert res.json()['code'] == 'unknown_session'
    assert 'only read' in res.json()['message']
    assert current(hub) == old


def test_during_a_rollover_strangers_are_still_refused(hub: ChannelHub, fake: FakeBrains) -> None:
    rolling_over(hub, fake)
    assert tasks_as(hub, str(uuid.uuid4())) == 401  # not listed at all
    assert tasks_as(hub, fake.add(name='harry-worker')) == 401  # listed, not as this brain
    impostor = fake.add(name=brain_name(hub.agent_id), role='')  # same name, no brain role
    assert tasks_as(hub, impostor) == 401


def test_no_rollover_under_way_lets_no_listed_brain_read(hub: ChannelHub, fake: FakeBrains) -> None:
    # Control: delete the "only while rolling over" check in `incoming` and this goes red.
    sid = starting_brain(hub, fake)
    wait(lambda: current(hub) == sid)
    wait(lambda: not keeper_of(hub)._state(hub.agent_id).starting)
    stranger = fake.add(name=brain_name(hub.agent_id))
    assert run(keeper_of(hub).incoming(stranger)) is None
    assert tasks_as(hub, stranger) == 401


def test_the_reads_before_the_hello_are_exactly_the_brain_apis_get_routes() -> None:
    """A new GET under /brain is closed to a rollover's brain until someone adds it to
    READ_BEFORE_HELLO, after checking it has no side effects."""
    from fastapi.routing import APIRoute

    from marcel_hub.brain_api import READ_BEFORE_HELLO, router

    gets = {r.path for r in router.routes if isinstance(r, APIRoute) and 'GET' in r.methods}
    assert gets == READ_BEFORE_HELLO


def test_a_route_not_on_the_list_is_closed_before_the_hand_over(
    hub: ChannelHub, fake: FakeBrains, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marcel_hub import brain_api

    old, new = rolling_over(hub, fake)
    monkeypatch.setattr(brain_api, 'READ_BEFORE_HELLO', frozenset({'/brain/usage'}))
    assert tasks_as(hub, new) == 401
    assert current(hub) == old


def test_once_the_spawn_returns_only_the_session_waited_for_may_read(
    hub: ChannelHub, fake: FakeBrains
) -> None:
    """`rolling` ends with the spawn: another session listed under the brain name is a stranger
    again. Never clear `rolling` and it could read."""
    _, new = rolling_over(hub, fake)
    keeper = keeper_of(hub)
    wait(lambda: keeper._state(hub.agent_id).pending is not None)
    assert not keeper._state(hub.agent_id).rolling
    other = fake.add(name=brain_name(hub.agent_id))
    assert tasks_as(hub, other) == 401
    assert tasks_as(hub, new) == 200


def test_a_failed_rollover_spawn_ends_the_reading_window(hub: ChannelHub, fake: FakeBrains) -> None:
    old = wait(lambda: current(hub))
    keeper = keeper_of(hub)
    wait(lambda: not keeper._state(hub.agent_id).starting)
    fake.fail_spawns = 1
    assert keeper.roll_over(hub.agent_id)
    wait(lambda: keeper._state(hub.agent_id).failures >= 1)
    assert not keeper._state(hub.agent_id).rolling
    assert current(hub) == old


def test_the_runner_down_during_a_rollover_read_is_a_401_not_a_crash(
    hub: ChannelHub, fake: FakeBrains
) -> None:
    _, new = rolling_over(hub, fake)
    assert keeper_of(hub)._state(hub.agent_id).rolling
    fake.unreachable = True
    assert tasks_as(hub, new) == 401
    fake.unreachable = False


def test_a_stopped_or_retired_brain_cannot_read_during_a_rollover(
    hub: ChannelHub, fake: FakeBrains
) -> None:
    old, _ = rolling_over(hub, fake)
    keeper = keeper_of(hub)
    assert keeper._state(hub.agent_id).rolling
    stopped = fake.add(name=brain_name(hub.agent_id), state='stopped')  # an earlier, failed try
    assert tasks_as(hub, stopped) == 401
    retired = fake.add(name=brain_name(hub.agent_id))
    keeper._state(hub.agent_id).retiring.add(retired)  # an old brain whose stop failed
    assert tasks_as(hub, retired) == 401
    assert run(keeper.incoming(old)) is None  # the current brain resolves on its own


def test_incoming_takes_only_a_live_brain_named_session_while_rolling(engine: Any) -> None:
    """`incoming` on its own, with no keeper loop to retire strangers first: each of its checks
    decides alone."""
    from sqlmodel import Session

    from marcel_hub.brain import BrainKeeper
    from marcel_hub.models import Agent

    with Session(engine) as s:
        agent = Agent(owner_user_id='usr_owner', name='Marcel', animal='giraffe', palette='marcel',
                      seed='Marcel')  # fmt: skip
        s.add(agent)
        s.commit()
        agent_id = agent.id
    fake = FakeBrains()
    keeper = BrainKeeper(engine, fake, cwd='/home/owner/marcel/brain')
    name = brain_name(agent_id)
    live = fake.add(name=name)
    stopped = fake.add(name=name, state='stopped')
    retired = fake.add(name=name)
    assert run(keeper.incoming(live)) is None, 'no rollover under way'
    st = keeper._state(agent_id)
    st.rolling = True
    st.retiring.add(retired)
    assert run(keeper.incoming(live)) is not None
    assert run(keeper.incoming(stopped)) is None, 'a stopped brain reads nothing'
    assert run(keeper.incoming(retired)) is None, 'an old brain being retired reads nothing'
