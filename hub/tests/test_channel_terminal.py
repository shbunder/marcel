"""Approvals answered somewhere else: in a terminal attached to the session, or never, because the
session stopped or failed. Driven through the runner watcher with the fake runner, as in
production: the runner's state stream and the session's transcript."""

from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.engine import Engine
from test_runner_client_fakes import FakeRunnerServer, RecordingAlerts, Sleeps, until, watch_for
from test_tasks_fakes import AGENT, add_agent, run

from marcel_hub.channel import Gateway
from marcel_hub.models import AnsweredVia, ApprovalState, Task, TaskState
from marcel_hub.runner_client import RunnerWatch
from marcel_hub.tasks.ports import NoRunner
from marcel_hub.tasks.registry import TaskRegistry

PROMPT = {
    'state': 'blocked',
    'status': 'waiting',
    'waiting_for': 'permission prompt',
    'needs': 'approve Bash: docker system df -v',
}
WORKING = {'state': 'working', 'status': 'busy', 'waiting_for': None, 'needs': None}
REQUEST = {
    'type': 'permission_request',
    'request_id': 'qiwxo',
    'tool_name': 'Bash',
    'description': 'Run docker system df -v',
}


class Rig:
    def __init__(self, engine: Engine, tmp_path: Path) -> None:
        add_agent(engine)
        self.fake = FakeRunnerServer()
        self.gateway = Gateway(engine, tmp_path / 'hub-secret')
        self.registry = TaskRegistry(
            engine, NoRunner(), self.gateway.outbox, self.gateway.task_publisher()
        )
        self.gateway.registry = self.registry
        self.watch: RunnerWatch = watch_for(self.fake, self.registry, RecordingAlerts(), Sleeps())
        self.watch.listeners.append(self.gateway)
        self.offset = 100

    async def start(self) -> tuple[Task, str]:
        self.watch.start()
        await until(lambda: self.watch.reachable is True)
        task = await self.registry.spawn(AGENT, 'Check the disk', prompt='Check disk usage.')
        assert task.session_id is not None
        await until(lambda: self.registry.get(task.id).state == TaskState.working)
        return task, task.session_id

    def write(self, sid: str, kind: str, data: dict[str, Any]) -> None:
        self.offset += 10
        self.fake.write(sid, (self.offset, 0, kind, data))

    def call(self, sid: str, tool_use_id: str = 'tu_1', tool: str = 'Bash') -> None:
        self.write(sid, 'tool_call', {'tool_use_id': tool_use_id, 'tool': tool, 'summary': 'df'})

    def result(self, sid: str, tool_use_id: str = 'tu_1', **extra: Any) -> None:
        data = {'tool_use_id': tool_use_id, 'is_error': False, 'text': 'ok', **extra}
        self.write(sid, 'tool_result', data)

    async def prompt(self, task: Task, sid: str, request: dict[str, Any] = REQUEST) -> str:
        """The session reaches a permission prompt: the channel relays it, the runner shows it."""
        await self.gateway.permission_request(task.id, request)
        self.fake.push_state(sid, **PROMPT)
        await until(lambda: sid in self.gateway._in_prompt)  # pyright: ignore[reportPrivateUsage]
        [card] = self.gateway.approvals.open_on(task.id)
        return card.id

    def events(self, task_id: str) -> list[Any]:
        return self.registry.events(task_id, limit=1000)[0]

    def card(self, approval_id: str) -> Any:
        return self.gateway.approvals.get(approval_id)


@pytest.fixture
def rig(engine: Engine, tmp_path: Path) -> Rig:
    return Rig(engine, tmp_path)


def test_a_prompt_approved_in_the_terminal_resolves_the_card_approved(rig: Rig) -> None:
    async def scenario() -> None:
        task, sid = await rig.start()
        rig.call(sid)
        card = await rig.prompt(task, sid)
        assert rig.registry.get(task.id).state == TaskState.needs_you
        rig.result(sid)  # the tool ran
        rig.fake.push_state(sid, **WORKING)  # the session left the prompt
        await until(lambda: rig.card(card).state != ApprovalState.open)
        assert rig.card(card).state == ApprovalState.approved
        assert rig.card(card).answered_via == AnsweredVia.terminal
        assert rig.registry.get(task.id).state == TaskState.working
        # The session was never sent a decision: it already had its answer.
        assert rig.gateway.outbox.pending(f'task:{task.id}') == []
        await rig.watch.close()

    run(scenario())


def test_a_prompt_denied_in_the_terminal_resolves_the_card_denied(rig: Rig) -> None:
    async def scenario() -> None:
        task, sid = await rig.start()
        rig.call(sid)
        card = await rig.prompt(task, sid)
        rig.result(sid, is_error=True, denied=True, text='The user denied this.')
        rig.fake.push_state(sid, **WORKING)
        await until(lambda: rig.card(card).state != ApprovalState.open)
        assert rig.card(card).state == ApprovalState.denied
        assert rig.card(card).answered_via == AnsweredVia.terminal
        await rig.watch.close()

    run(scenario())


def test_a_result_that_arrives_after_the_session_left_the_prompt_still_resolves(rig: Rig) -> None:
    async def scenario() -> None:
        task, sid = await rig.start()
        rig.call(sid)
        card = await rig.prompt(task, sid)
        rig.fake.push_state(sid, **WORKING)
        await until(lambda: task.id in rig.gateway._left_prompt)  # pyright: ignore[reportPrivateUsage]
        assert rig.card(card).state == ApprovalState.open  # no result in the transcript yet
        rig.result(sid)
        await until(lambda: rig.card(card).state != ApprovalState.open)
        assert rig.card(card).state == ApprovalState.approved
        assert task.id not in rig.gateway._left_prompt  # pyright: ignore[reportPrivateUsage]
        await rig.watch.close()

    run(scenario())


def test_a_card_stays_open_while_the_session_is_still_at_the_prompt(rig: Rig) -> None:
    async def scenario() -> None:
        task, sid = await rig.start()
        rig.call(sid)
        card = await rig.prompt(task, sid)
        rig.call(sid, 'tu_0', 'Read')
        rig.result(sid, 'tu_0')  # another tool's result, while Bash still waits
        rig.fake.push_state(sid, **PROMPT, remote_url='https://claude.ai/code/x')
        await until(lambda: rig.registry.get(task.id).remote_url is not None)
        assert rig.card(card).state == ApprovalState.open
        await rig.watch.close()

    run(scenario())


def test_a_card_stays_open_until_the_runner_has_seen_the_prompt(rig: Rig) -> None:
    """Another call of the same tool, allowed without a prompt, can finish while this one waits.
    Its result must not answer the card while the runner has not shown the session at the
    prompt and leaving it."""

    async def scenario() -> None:
        task, sid = await rig.start()
        rig.call(sid, 'tu_1')
        await rig.gateway.permission_request(task.id, REQUEST)
        [card] = rig.gateway.approvals.open_on(task.id)
        rig.call(sid, 'tu_2')
        rig.result(sid, 'tu_2')  # allowed by auto mode, ran at once
        await until(lambda: any(e.type.value == 'tool_result' for e in rig.events(task.id)))
        rig.fake.push_state(sid, **WORKING, remote_url='https://claude.ai/code/x')
        await until(lambda: rig.registry.get(task.id).remote_url is not None)
        assert rig.card(card.id).state == ApprovalState.open
        await rig.watch.close()

    run(scenario())


def test_an_older_result_of_the_same_tool_does_not_answer_a_new_prompt(rig: Rig) -> None:
    async def scenario() -> None:
        task, sid = await rig.start()
        rig.call(sid, 'tu_old')
        rig.result(sid, 'tu_old')  # an earlier Bash call that ran before this prompt
        await until(lambda: len(rig.registry.events(task.id, limit=100)[0]) >= 4)
        rig.call(sid, 'tu_new')
        card = await rig.prompt(task, sid)
        rig.fake.push_state(sid, **WORKING)
        await until(lambda: task.id in rig.gateway._left_prompt)  # pyright: ignore[reportPrivateUsage]
        assert rig.card(card).state == ApprovalState.open
        rig.result(sid, 'tu_new', denied=True, is_error=True)
        await until(lambda: rig.card(card).state != ApprovalState.open)
        assert rig.card(card).state == ApprovalState.denied
        await rig.watch.close()

    run(scenario())


def test_an_app_answer_first_wins_over_the_terminal(rig: Rig) -> None:
    async def scenario() -> None:
        task, sid = await rig.start()
        rig.call(sid)
        card = await rig.prompt(task, sid)
        assert await rig.gateway.answer(card, approve=False, note=None) is not None
        rig.result(sid)  # the session got the denial... but suppose the tool ran anyway
        rig.fake.push_state(sid, **WORKING)
        await until(lambda: rig.registry.get(task.id).state == TaskState.working)
        assert rig.card(card).state == ApprovalState.denied
        assert rig.card(card).answered_via == AnsweredVia.app
        await rig.watch.close()

    run(scenario())


@pytest.mark.parametrize('ending', ['failed', 'stopped'])
def test_a_session_that_ends_at_the_prompt_expires_the_card(rig: Rig, ending: str) -> None:
    async def scenario() -> None:
        task, sid = await rig.start()
        rig.call(sid)
        card = await rig.prompt(task, sid)
        rig.fake.push_state(sid, state=ending, waiting_for=None)
        await until(lambda: rig.card(card).state != ApprovalState.open)
        assert rig.card(card).state == ApprovalState.expired
        assert rig.card(card).answered_via is None
        await rig.watch.close()

    run(scenario())
