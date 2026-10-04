"""The mock's whole world: stored objects, the event log, and the scripted scenario."""

from __future__ import annotations

import asyncio
import copy
from datetime import UTC, datetime
from typing import Any

from . import contract

MAIN_CONVERSATION = 'cnv_main_marcel'
AGENT_ID = 'agt_marcel'
SCENARIO_TITLE = 'Fix the flaky login test'
SCENARIO_REPO = 'shbunder/marcel'


def now() -> str:
    return datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')


class Hub:
    """In-memory state. Nothing survives a restart; that is on purpose."""

    def __init__(self, step_delay: float = 1.0) -> None:
        self.step_delay = step_delay
        self.events: list[dict[str, Any]] = []
        self.subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self.agent_state: dict[str, Any] = {'kind': 'idle'}
        self.messages: list[dict[str, Any]] = []
        self.tasks: dict[str, dict[str, Any]] = {}
        self.task_events: dict[str, list[dict[str, Any]]] = {}
        self.approvals: dict[str, dict[str, Any]] = {}
        self.artifacts: dict[str, dict[str, Any]] = {}
        self.conversations: dict[str, dict[str, Any]] = {}
        self.client_ids: dict[str, dict[str, Any]] = {}
        self.answers: dict[str, asyncio.Event] = {}
        self.runs: set[asyncio.Task[None]] = set()
        self.counter = 0
        self._seed()

    # ---- seed: the contract's examples, nothing else -------------------------------------

    def _seed(self) -> None:
        main = contract.example('Conversation')
        main.update(id=MAIN_CONVERSATION, kind='main', title='Marcel', reply_count=0)
        for key in ('parent_message_id', 'reply_count', 'last_message_at', 'title'):
            main.pop(key, None)
        self.conversations[MAIN_CONVERSATION] = main
        side = contract.example('Conversation')
        self.conversations[side['id']] = side

        page = contract.example('MessagePage')
        self.messages = page['items']
        self.messages.append(contract.example('Message'))

        for task in (contract.example('Task'), *contract.example('TaskPage')['items']):
            self.tasks[task['id']] = task
        for ev in contract.example('TaskEventPage')['items']:
            self.task_events.setdefault(ev['task_id'], []).append(ev)
        approval = contract.example('Approval')
        self.approvals[approval['id']] = approval
        for art in (contract.example('Artifact'), *contract.example('ArtifactPage')['items']):
            self.artifacts[art['id']] = art

    # ---- events --------------------------------------------------------------------------

    def emit(self, type_: str, data: Any) -> dict[str, Any]:
        event = {
            'id': len(self.events) + 1,
            'type': type_,
            'at': now(),
            'data': copy.deepcopy(data),
        }
        self.events.append(event)
        for queue in self.subscribers:
            queue.put_nowait(event)
        return event

    def subscribe(
        self, since: int | None
    ) -> tuple[asyncio.Queue[dict[str, Any]], list[dict[str, Any]]]:
        """Register a listener and return the frames to send first. No await in between, so no
        event can fall in the gap between the replay and the live feed."""
        last = len(self.events)
        if since is not None and since > last:
            head = [self._control('resync.required', {'last_event_id': last})]
            replay: list[dict[str, Any]] = []
        else:
            replay = self.events[since:] if since is not None else []
            head = [self._control('hello', {'last_event_id': last, 'replayed': len(replay)})]
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.subscribers.add(queue)
        return queue, [*head, *replay]

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self.subscribers.discard(queue)

    @staticmethod
    def _control(type_: str, data: dict[str, Any]) -> dict[str, Any]:
        return {'id': 0, 'type': type_, 'at': now(), 'data': data}

    # ---- objects -------------------------------------------------------------------------

    def next_id(self, prefix: str) -> str:
        self.counter += 1
        return f'{prefix}_mock{self.counter:04d}'

    def set_agent_state(self, state: dict[str, Any]) -> None:
        self.agent_state = state
        self.emit('agent.state', {'agent_id': AGENT_ID, 'state': state})

    def agent(self) -> dict[str, Any]:
        agent = contract.example('Agent')
        agent['state'] = copy.deepcopy(self.agent_state)
        return agent

    def me(self) -> dict[str, Any]:
        me = contract.example('Me')
        me['agents'] = [self.agent()]
        return me

    def add_message(self, message: dict[str, Any]) -> dict[str, Any]:
        self.messages.append(message)
        self.emit('message.created', message)
        return message

    def user_message(
        self, conversation_id: str, text: str, client_id: str | None
    ) -> dict[str, Any]:
        if client_id and client_id in self.client_ids:
            return self.client_ids[client_id]
        message: dict[str, Any] = {
            'id': self.next_id('msg'),
            'conversation_id': conversation_id,
            'role': 'user',
            'kind': 'text',
            'text': text,
            'created_at': now(),
        }
        if client_id:
            message['client_id'] = client_id
            self.client_ids[client_id] = message
        return self.add_message(message)

    def update_task(self, task: dict[str, Any], **changes: Any) -> None:
        if changes.get('state', 'needs_you') != 'needs_you':
            task.pop('waiting_for', None)
        task.update(changes, updated_at=now())
        self.emit('task.updated', task)

    def add_task_event(self, task_id: str, type_: str, data: dict[str, Any]) -> None:
        log = self.task_events.setdefault(task_id, [])
        event = {'task_id': task_id, 'seq': len(log) + 1, 'type': type_, 'at': now(), 'data': data}
        log.append(event)
        self.emit('task.event', event)

    def change_state(self, task: dict[str, Any], to: str, reason: str, **changes: Any) -> None:
        self.add_task_event(
            task['id'], 'state', {'from': task['state'], 'to': to, 'reason': reason}
        )
        self.update_task(task, state=to, **changes)

    # ---- the scenario --------------------------------------------------------------------

    @property
    def busy(self) -> bool:
        return any(not run.done() for run in self.runs)

    def start_scenario(self, message: dict[str, Any]) -> None:
        """One run at a time. A message sent while busy is stored, never refused (B-02)."""
        if self.busy:
            return
        run = asyncio.create_task(self._scenario(message))
        self.runs.add(run)
        run.add_done_callback(self.runs.discard)

    async def stop(self) -> None:
        for run in list(self.runs):
            run.cancel()
        await asyncio.gather(*self.runs, return_exceptions=True)

    async def _pause(self, factor: float = 1.0) -> None:
        await asyncio.sleep(self.step_delay * factor)

    async def _scenario(self, user_message: dict[str, Any]) -> None:
        conversation_id = user_message['conversation_id']
        self.set_agent_state({'kind': 'thinking'})
        await self._pause()

        task_id = self.next_id('tsk')
        task: dict[str, Any] = {
            'id': task_id,
            'agent_id': AGENT_ID,
            'title': SCENARIO_TITLE,
            'location': 'cloud',
            'state': 'starting',
            'model': 'sonnet',
            'origin': 'marcel',
            'repo': SCENARIO_REPO,
            'parent_message_id': user_message['id'],
            'artifact_count': 0,
            'created_at': now(),
            'updated_at': now(),
        }
        self.tasks[task_id] = task
        self.emit('task.created', task)
        self.add_message(
            {
                'id': self.next_id('msg'),
                'conversation_id': conversation_id,
                'role': 'agent',
                'kind': 'milestone',
                'milestone': {
                    'task_id': task_id,
                    'task_title': SCENARIO_TITLE,
                    'event': 'started',
                    'location': 'cloud',
                },
                'task_id': task_id,
                'created_at': now(),
            }
        )
        await self._pause()

        self.change_state(task, 'working', 'The runner reports the session is working')
        self.set_agent_state({'kind': 'working', 'active_tasks': 1})
        await self._pause()

        self.add_task_event(
            task_id, 'text', {'text': 'Looking at the login test and its fixtures.'}
        )
        await self._pause()
        self.add_task_event(
            task_id, 'tool_call', {'tool': 'Bash', 'summary': 'pytest tests/test_login.py -x'}
        )
        await self._pause()
        self.add_task_event(
            task_id,
            'diff',
            {
                'path': 'tests/test_login.py',
                'additions': 6,
                'deletions': 1,
                'patch': (
                    '@@ -12,1 +12,6 @@\n-    assert page.logged_in\n'
                    '+    page.wait_for_cookie()\n+    assert page.logged_in\n'
                ),
            },
        )
        await self._pause()

        await self._ask_and_finish(task, conversation_id)

    async def _ask_and_finish(self, task: dict[str, Any], conversation_id: str) -> None:
        task_id = task['id']
        approval_id = self.next_id('apr')
        approval = {
            'id': approval_id,
            'task_id': task_id,
            'tool': 'Bash',
            'summary': 'Run `git push origin fix/login-test`',
            'detail': 'git push origin fix/login-test',
            'state': 'open',
            'created_at': now(),
        }
        self.approvals[approval_id] = approval
        answered = self.answers[approval_id] = asyncio.Event()
        self.add_task_event(
            task_id,
            'permission',
            {'approval_id': approval_id, 'tool': 'Bash', 'summary': approval['summary']},
        )
        self.emit('approval.created', approval)
        self.add_message(
            {
                'id': self.next_id('msg'),
                'conversation_id': conversation_id,
                'role': 'agent',
                'kind': 'approval',
                'approval_id': approval_id,
                'task_id': task_id,
                'created_at': now(),
            }
        )
        self.change_state(
            task,
            'needs_you',
            'Permission request: Bash',
            waiting_for='Permission to push the fix branch.',
            approval_ids=[approval_id],
        )
        self.set_agent_state({'kind': 'needs_you'})

        await answered.wait()
        approved = approval['state'] == 'approved'
        task.pop('approval_ids', None)
        if not approved:
            await self._finish_stopped(task, conversation_id)
            return

        self.change_state(task, 'working', 'You approved the push')
        self.set_agent_state({'kind': 'working', 'active_tasks': 1})
        await self._pause()
        await self._finish_done(task, conversation_id)

    async def _finish_done(self, task: dict[str, Any], conversation_id: str) -> None:
        task_id = task['id']
        summary = 'The test raced the session cookie. Added a wait on the cookie.'
        self.add_task_event(task_id, 'progress', {'text': 'Pushed the branch and opened a PR.'})
        await self._pause()
        artifact = contract.example('Artifact')
        artifact.update(id=self.next_id('art'), task_id=task_id, created_at=now())
        self.artifacts[artifact['id']] = artifact
        self.add_task_event(
            task_id,
            'artifact',
            {'artifact_id': artifact['id'], 'kind': 'pr', 'title': artifact['title']},
        )
        self.emit('artifact.created', artifact)
        self.change_state(
            task, 'done', 'The worker reported it is finished', summary=summary, artifact_count=1
        )
        self.add_message(
            {
                'id': self.next_id('msg'),
                'conversation_id': conversation_id,
                'role': 'agent',
                'kind': 'milestone',
                'milestone': {
                    'task_id': task_id,
                    'task_title': SCENARIO_TITLE,
                    'event': 'done',
                    'location': 'cloud',
                    'summary': summary,
                    'artifact_ids': [artifact['id']],
                },
                'task_id': task_id,
                'created_at': now(),
            }
        )
        await self._settle()

    async def _finish_stopped(self, task: dict[str, Any], conversation_id: str) -> None:
        self.change_state(task, 'stopped', 'You denied the push')
        self.add_message(
            {
                'id': self.next_id('msg'),
                'conversation_id': conversation_id,
                'role': 'agent',
                'kind': 'milestone',
                'milestone': {
                    'task_id': task['id'],
                    'task_title': SCENARIO_TITLE,
                    'event': 'stopped',
                    'location': 'cloud',
                    'summary': 'Stopped before pushing, as you asked.',
                },
                'task_id': task['id'],
                'created_at': now(),
            }
        )
        await self._settle()

    async def _settle(self) -> None:
        """`done` shows for a moment (5 steps), then the avatar goes back to idle."""
        self.set_agent_state({'kind': 'done'})
        await self._pause(5)
        self.set_agent_state({'kind': 'idle'})

    # ---- answers -------------------------------------------------------------------------

    def answer(self, approval_id: str, decision: str) -> tuple[int, dict[str, Any]] | None:
        """Apply the first answer; later ones get the approval as it stands, with a 409."""
        approval = self.approvals.get(approval_id)
        if approval is None:
            return None
        if approval['state'] != 'open':
            return 409, approval
        approval.update(
            state='approved' if decision == 'approve' else 'denied',
            answered_via='app',
            answered_at=now(),
        )
        self.emit('approval.resolved', approval)
        waiter = self.answers.get(approval_id)
        if waiter is not None:
            waiter.set()
        return 200, approval
