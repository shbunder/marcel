"""The channel gateway: what the hub does with each session's frames (`channel-protocol.md`).

A session proves itself with the hub secret, and is known only by the session id the runner
reported: a task's `session_id` (worker, or adopted) or a conversation's `brain_session_id`
(brain for the main conversation, side for a side thread). A session acts only for that task or
conversation: a worker's `reply`, or a brain's `report`, is refused and never stored; the hub
answers it with `call_ack` + `error`, so the session drops the call instead of re-sending it.
"""

import contextlib
import hmac
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlmodel import Session, col, select

from marcel_hub.artifacts import ArtifactRefused, UnknownArtifact
from marcel_hub.channel.approvals import ApprovalBook
from marcel_hub.channel.frames import next_steps_problem, version_tuple
from marcel_hub.channel.outbox import ChannelOutbox, conversation_target, task_target
from marcel_hub.channel.ports import Artifacts, LogPush, Push
from marcel_hub.conversations import ensure_main_conversation, post_message
from marcel_hub.models import (
    Agent,
    AnsweredVia,
    Approval,
    ApprovalState,
    ChannelCall,
    Conversation,
    ConversationKind,
    MessageKind,
    MessageRole,
    Task,
    TaskEventType,
    TaskLocation,
    TaskOrigin,
    TaskState,
)
from marcel_hub.runner_client import Alerts, LogAlerts
from marcel_hub.tasks.ports import NullPublisher, Publisher
from marcel_hub.tasks.registry import TaskRegistry
from marcel_hub.tasks.states import TRANSITIONS, IllegalTransition
from marcel_hub.ws import EventLog

log = logging.getLogger(__name__)

# The oldest plugin this hub speaks to (plugins/marcel/channel/src/version.ts).
MIN_PLUGIN_VERSION = (0, 1, 0)
SECRET_FILE = 'hub-secret'
MIN_SECRET = 32  # channel.schema.json Hello.secret minLength

FINAL = frozenset({'done', 'failed', 'stopped'})  # a prompt cannot outlive these
PROMPT = 'permission prompt'  # runner-api.yaml Session.waiting_for
WORKER_ROLES = frozenset({'worker', 'adopted'})
CHAT_ROLES = frozenset({'brain', 'side'})


@dataclass(frozen=True)
class Peer:
    """Who a connected session is: its role and the one task or conversation it acts for."""

    session_id: str
    role: str
    agent_id: str
    task_id: str | None = None
    conversation_id: str | None = None

    @property
    def target(self) -> str:
        if self.task_id is not None:
            return task_target(self.task_id)
        assert self.conversation_id is not None
        return conversation_target(self.conversation_id)

    def welcome(self) -> dict[str, Any]:
        frame: dict[str, Any] = {'type': 'welcome', 'role': self.role, 'agent_id': self.agent_id}
        if self.task_id is not None:
            frame['task_id'] = self.task_id
        else:
            frame['conversation_id'] = self.conversation_id
        return frame


@dataclass(frozen=True)
class Rejection:
    reason: str
    message: str

    def frame(self) -> dict[str, Any]:
        return {'type': 'rejected', 'reason': self.reason, 'message': self.message}


class CallRefused(Exception):
    """The hub stores nothing of this call and answers with `call_ack.error`."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class Gateway:
    def __init__(
        self,
        engine: Any,
        secret_path: Path,
        *,
        push: Push | None = None,
        artifacts: Artifacts | None = None,
        alerts: Alerts | None = None,
    ) -> None:
        self.engine = engine
        self.secret_path = secret_path
        self.outbox = ChannelOutbox(engine)
        self.events: EventLog | None = None
        self.approvals = ApprovalBook(engine, lambda: self.events)
        self.push = push or LogPush()
        self.artifacts: Artifacts | None = artifacts
        self.alerts = alerts or LogAlerts()
        self.registry: TaskRegistry | None = None
        self._calls_lock = threading.Lock()
        self._in_flight: set[str] = set()
        self._in_prompt: set[str] = set()  # session ids the runner shows at a permission prompt
        self._left_prompt: set[str] = set()  # task ids whose cards wait for a transcript result
        self._alerted: set[str] = set()
        self.links: dict[str, Any] = {}  # target → its live `socket.Link`

    def tasks(self) -> TaskRegistry:
        assert self.registry is not None, 'install the gateway with the task registry'
        return self.registry

    # Hello ------------------------------------------------------------------------------------

    def _secret(self) -> str | None:
        try:
            secret = self.secret_path.read_text().strip()
        except OSError:
            return None
        return secret if len(secret) >= MIN_SECRET else None

    def check_hello(self, hello: dict[str, Any]) -> Peer | Rejection:
        secret = self._secret()
        if secret is None:
            self._alert_once(
                'channel_no_secret',
                'Marcel cannot talk to its sessions',
                f'The hub has no secret in {self.secret_path} (at least {MIN_SECRET} characters), '
                'so it refuses every session. Redeploy to write it.',
            )
            return Rejection('bad_secret', 'The hub has no secret set up. Redeploy Marcel.')
        if not hmac.compare_digest(secret.encode(), hello['secret'].encode()):
            self._alert_once(
                'channel_bad_secret',
                'Marcel cannot talk to its sessions',
                'A session connected with a different hub secret. A deploy wrote a different '
                'secret for the plugin than for the hub. Redeploy to fix it.',
            )
            return Rejection('bad_secret', 'The secret does not match this hub.')
        version = version_tuple(hello['plugin_version'])
        if version is None or version < MIN_PLUGIN_VERSION:
            needed = '.'.join(map(str, MIN_PLUGIN_VERSION))
            self._alert_once(
                'channel_plugin_too_old',
                'Marcel cannot talk to its sessions',
                f'The marcel plugin is {hello["plugin_version"]}; the hub needs {needed} or newer. '
                'Update the plugin with the hub.',
            )
            return Rejection('plugin_too_old', f'This hub needs plugin {needed} or newer.')
        peer = self.resolve(hello['session_id'])
        if peer is None:
            return Rejection(
                'unknown_session',
                f'No task or conversation is waiting for session {hello["session_id"][:8]}.',
            )
        return peer

    def resolve(self, session_id: str) -> Peer | None:
        """The task or conversation the runner reported this session for, or None."""
        with Session(self.engine) as s:
            task = s.exec(
                select(Task)
                .where(Task.session_id == session_id)
                .where(Task.location == TaskLocation.nuc)
            ).first()
            if task is not None:
                role = 'adopted' if task.origin == TaskOrigin.adopted else 'worker'
                return Peer(session_id, role, task.agent_id, task_id=task.id)
            conv = s.exec(
                select(Conversation).where(Conversation.brain_session_id == session_id)
            ).first()
            if conv is not None:
                role = 'brain' if conv.kind == ConversationKind.main else 'side'
                return Peer(session_id, role, conv.agent_id, conversation_id=conv.id)
        return None

    def _alert_once(self, cause: str, title: str, body: str) -> None:
        log.warning('Refused a channel session: %s', body)
        if cause not in self._alerted:
            self._alerted.add(cause)
            self.alerts.broke(cause, title, body)

    # Frames from a session --------------------------------------------------------------------

    async def handle(self, peer: Peer, frame: dict[str, Any]) -> list[dict[str, Any]]:
        """Act on one checked frame; returns the frames to answer with."""
        kind = frame['type']
        if kind == 'ping':
            return [{'type': 'pong'}]
        if kind == 'ack':
            self.outbox.ack(peer.target, frame['seq'])
            return []
        if kind == 'reply':
            if peer.role not in CHAT_ROLES:
                return self._refuse(peer, frame, 'a reply', 'only the brain and side threads reply')
            return await self._once(peer, frame, self._store_reply)
        if kind == 'report':
            if peer.role not in WORKER_ROLES:
                return self._refuse(peer, frame, 'a report', 'only workers report')
            return await self._once(peer, frame, self._store_report)
        if kind == 'permission_request':
            if peer.task_id is None:
                self._deny_chat_prompt(peer, frame)
                return []
            await self.permission_request(peer.task_id, frame)
            return []
        log.info('Ignored a %s frame from session %s after hello.', kind, peer.session_id[:8])
        return []

    def _deny_chat_prompt(self, peer: Peer, frame: dict[str, Any]) -> None:
        """An approval card belongs to a task, so the brain or a side thread cannot get one
        (project/contract-requests/S-04.6.md). Rather than leave the session waiting at its
        prompt for ever, the hub denies it at once; a re-sent prompt is denied again."""
        log.warning(
            'The %s asked to use %s ("%s"). Marcel cannot show an approval card for it, so it '
            'was refused.',
            'brain' if peer.role == 'brain' else 'side thread',
            frame['tool_name'],
            frame['description'],
        )
        self.outbox.put(
            peer.target,
            {'type': 'permission_decision', 'request_id': frame['request_id'], 'behavior': 'deny'},
        )

    @staticmethod
    def _refuse(peer: Peer, frame: dict[str, Any], what: str, why: str) -> list[dict[str, Any]]:
        """A call this session may not make: store nothing, and say so in the ack, so the
        session's tool returns the reason and the call is not re-sent after every reconnect."""
        log.warning('Dropped %s from %s session %s: %s.', what, peer.role, peer.session_id[:8], why)
        error = {
            'code': 'not_allowed',
            'message': f'Marcel did not take {what} from this session: {why}.',
        }
        return [{'type': 'call_ack', 'call_id': frame['call_id'], 'error': error}]

    async def _once(
        self, peer: Peer, frame: dict[str, Any], store: Callable[..., Any]
    ) -> list[dict[str, Any]]:
        """Store a `reply` or `report` once per `call_id`; ack it every time. A call the hub
        refuses (`CallRefused`) is not recorded and is answered with an error ack."""
        call_id = frame['call_id']
        seen = False
        with self._calls_lock:
            busy = call_id in self._in_flight
            if not busy:
                with Session(self.engine) as s:
                    seen = s.get(ChannelCall, call_id) is not None
                if not seen:
                    self._in_flight.add(call_id)
        if busy:
            return []  # the other copy is being stored and will be acked
        if seen:
            return [{'type': 'call_ack', 'call_id': call_id, 'duplicate': True}]
        try:
            await store(peer, frame)
            with Session(self.engine) as s:
                s.add(ChannelCall(call_id=call_id, target=peer.target))
                s.commit()
        except CallRefused as exc:
            log.warning('Refused call %s from session %s: %s', call_id, peer.session_id[:8], exc)
            error = {'code': exc.code, 'message': exc.message}
            return [{'type': 'call_ack', 'call_id': call_id, 'error': error}]
        finally:
            with self._calls_lock:
                self._in_flight.discard(call_id)
        return [{'type': 'call_ack', 'call_id': call_id}]

    async def _store_reply(self, peer: Peer, frame: dict[str, Any]) -> None:
        events = self.events
        assert events is not None and peer.conversation_id is not None
        with Session(self.engine) as s:
            conv = s.get(Conversation, peer.conversation_id)
            assert conv is not None
            post_message(
                s,
                events,
                conv,
                role=MessageRole.agent,
                kind=MessageKind.text,
                body={'text': frame['text']},
            )

    async def _store_report(self, peer: Peer, frame: dict[str, Any]) -> None:
        assert peer.task_id is not None
        registry = self.tasks()
        task_id, kind = peer.task_id, frame['kind']
        # A `next` list past the contract's bounds refuses the report: cutting it short would
        # quietly drop steps the worker meant the owner to see.
        if 'next' in frame and (problem := next_steps_problem(frame['next'])) is not None:
            raise CallRefused('bad_next', f'Marcel did not take this report: {problem}.')
        reported = [frame['artifact']] if 'artifact' in frame else []
        reported += frame.get('artifacts', [])
        # A new version of an artifact the task may not update refuses the whole report, before
        # anything is stored: the worker learns at once, and nothing half-lands.
        for artifact in reported:
            if artifact.get('artifact_id') is None:
                continue
            try:
                if self.artifacts is None:
                    raise UnknownArtifact('The artifact was not saved: this hub has no store.')
                self.artifacts.version_target(artifact['artifact_id'], task_id)
            except UnknownArtifact as exc:
                raise CallRefused('unknown_artifact', str(exc)) from exc
        artifact_ids = []
        for artifact in reported:
            try:
                if self.artifacts is None:
                    raise ArtifactRefused('The artifact was not saved: this hub has no store.')
                artifact_ids.append(self.artifacts.publish_reported(artifact, task_id).id)
            except ArtifactRefused as exc:
                # The report is still stored; the thread shows why the artifact is missing.
                log.warning('Task %s: %s', task_id, exc)
                registry.append_event(task_id, TaskEventType.error, {'message': str(exc)})
        if kind in ('started', 'progress') and frame.get('text'):
            progress: dict[str, Any] = {'text': frame['text']}
            if kind == 'progress' and 'next' in frame:  # the steps still planned (wireframe 2A)
                progress['next'] = list(frame['next'])
            registry.append_event(task_id, TaskEventType.progress, progress)
        if kind == 'started':
            await self._move(task_id, TaskState.working, 'The worker reports started')
            self.milestone(task_id, 'started')
        elif kind == 'progress':
            await self._move(task_id, TaskState.working, 'The worker reports progress')
        elif kind in ('done', 'failed'):
            state = TaskState.done if kind == 'done' else TaskState.failed
            await self._move(task_id, state, f'The worker reports {kind}', summary=frame['summary'])
            self.milestone(task_id, kind, summary=frame['summary'], artifact_ids=artifact_ids)

    async def _move(self, task_id: str, to: TaskState, reason: str, **fields: Any) -> None:
        """A state report from the session; one the state machine does not allow changes
        nothing (e.g. a progress report from a task the owner already stopped)."""
        registry = self.tasks()
        before = registry.get(task_id).state
        if before != to and (before, to) not in TRANSITIONS:
            log.info('Task %s stays %s; its session reports %s.', task_id, before.value, to.value)
            return
        with contextlib.suppress(IllegalTransition):  # moved meanwhile; the registry logged it
            await registry.report_state(task_id, to, reason, **fields)

    def milestone(self, task_id: str, event: str, **extra: Any) -> None:
        """Post a milestone (B-06) for the task in its agent's main conversation."""
        if self.events is None:
            return
        with Session(self.engine) as s:
            task = s.get(Task, task_id)
            agent = s.get(Agent, task.agent_id) if task else None
            if task is None or agent is None:
                return
            body: dict[str, Any] = {
                'task_id': task.id,
                'task_title': task.title,
                'event': event,
                'location': task.location.value,
            }
            body.update({k: v for k, v in extra.items() if v})
            post_message(
                s,
                self.events,
                ensure_main_conversation(s, agent),
                role=MessageRole.agent,
                kind=MessageKind.milestone,
                body={'milestone': body},
                task_id=task.id,
            )

    # Approvals --------------------------------------------------------------------------------

    async def permission_request(self, task_id: str, request: dict[str, Any]) -> None:
        row = self.approvals.open(task_id, request)
        if row is None:
            log.info(
                'Permission request %s on task %s is a repeat.', request['request_id'], task_id
            )
            return
        registry = self.tasks()
        registry.append_event(
            task_id,
            TaskEventType.permission,
            {'approval_id': row.id, 'tool': row.tool, 'summary': row.summary},
        )
        await self._move(
            task_id,
            TaskState.needs_you,
            f'Permission request: {row.tool}',
            waiting_for=f'Your approval: {row.summary}',
        )
        task = registry.get(task_id)
        if self.events is not None:
            with Session(self.engine) as s:
                agent = s.get(Agent, task.agent_id)
                assert agent is not None
                post_message(
                    s,
                    self.events,
                    ensure_main_conversation(s, agent),
                    role=MessageRole.agent,
                    kind=MessageKind.approval,
                    body={'approval_id': row.id},
                    task_id=task_id,
                )
        try:
            self.push.approval_opened(row.id)
        except Exception:  # a failed push never loses the card
            log.exception('Could not send the approval push for %s.', row.id)

    async def answer(self, approval_id: str, approve: bool, note: str | None) -> Any:
        """The owner answered in the app. Returns the closed card, or None if it was not open."""
        state = ApprovalState.approved if approve else ApprovalState.denied
        row = self.approvals.resolve(approval_id, state, AnsweredVia.app)
        if row is None:
            return None
        if row.request_id is not None:
            self.outbox.put(
                task_target(row.task_id),
                {
                    'type': 'permission_decision',
                    'request_id': row.request_id,
                    'behavior': 'allow' if approve else 'deny',
                },
            )
        registry = self.tasks()
        if not approve and note:
            # Right after the decision, so the session reads it as the reason (it also moves the
            # task back to working).
            await registry.steer(row.task_id, note, author='user')
        elif not self.approvals.open_on(row.task_id):
            await self._move(row.task_id, TaskState.working, 'The approval is answered')
        return row

    # The runner and the registry --------------------------------------------------------------

    async def on_session(self, task_id: str, session: dict[str, Any]) -> None:
        """`RunnerWatch` listener: notice a permission prompt answered in a terminal."""
        session_id = session['id']
        at_prompt = session['state'] == 'blocked' and session.get('waiting_for') == PROMPT
        if at_prompt:
            self._in_prompt.add(session_id)
            self._left_prompt.discard(task_id)
            return
        if session_id not in self._in_prompt:
            return
        self._in_prompt.discard(session_id)
        if session['state'] in ('stopped', 'failed'):
            return  # the registry's state change expires the cards
        self._left_prompt.add(task_id)
        self._settle_from_transcript(task_id)

    def task_ended(self, task_id: str, state: str) -> None:
        """A task reached a final state: no prompt of its can be answered any more. A card the
        transcript shows answered is resolved from it; the rest expire. A stopped or failed
        task's session is gone, so frames still waiting for it are dropped."""
        self._left_prompt.discard(task_id)
        self.approvals.resolve_from_transcript(task_id)
        self.approvals.expire_task(task_id)
        if state != TaskState.done.value:  # a done session can still take a follow-up
            dropped = self.outbox.clear(task_target(task_id))
            if dropped:
                log.info('Task %s ended: dropped %d frame(s) it never received.', task_id, dropped)

    def recheck(self) -> None:
        """At hub start: settle open cards whose task ended, or whose answer is already in the
        transcript, while the hub was down."""
        with Session(self.engine) as s:
            rows = s.exec(
                select(Task.id, Task.state)
                .join(Approval, col(Approval.task_id) == col(Task.id))
                .where(Approval.state == ApprovalState.open)
                .distinct()
            ).all()
        for task_id, state in rows:
            if str(state) in FINAL:
                self.task_ended(task_id, str(state))
            else:
                self.approvals.resolve_from_transcript(task_id)

    def _settle_from_transcript(self, task_id: str) -> None:
        _, still_open = self.approvals.resolve_from_transcript(task_id)
        if not still_open:
            self._left_prompt.discard(task_id)

    def task_publisher(self, inner: Publisher | None = None) -> Publisher:
        """The registry's publisher: passes every event on to `inner`, and expires cards when a
        task stops or fails, and settles terminal answers as their tool results arrive."""
        return _TaskWatch(self, inner or NullPublisher())

    def on_task_event(self, type_: str, data: dict[str, Any]) -> None:
        if type_ == 'task.updated' and data.get('state') in FINAL:
            self.task_ended(data['id'], data['state'])
        elif (
            type_ == 'task.event'
            and data.get('type') == 'tool_result'
            and data.get('task_id') in self._left_prompt
        ):
            self._settle_from_transcript(data['task_id'])

    # User messages ----------------------------------------------------------------------------

    def on_app_event(self, _user_id: str, type_: str, data: dict[str, Any]) -> None:
        """`EventLog` observer: a message the owner wrote goes to the session of its conversation.
        A thread that belongs to a task is left to S-04.9's steer path."""
        if type_ != 'message.created' or data.get('role') != MessageRole.user.value:
            return
        if data.get('kind') not in (MessageKind.text.value, MessageKind.quote.value):
            return
        try:
            with Session(self.engine) as s:
                conv = s.get(Conversation, data['conversation_id'])
            if conv is None or conv.task_id is not None:
                return
            if conv.brain_session_id is None:
                # No session runs this conversation yet; the message is stored, and the session
                # that starts for it reads the conversation, not a backlog of frames.
                log.info('Message %s has no session to go to yet.', data['id'])
                return
            frame: dict[str, Any] = {
                'type': 'user_message',
                'message_id': data['id'],
                'conversation_id': conv.id,
                'text': data.get('text', ''),
            }
            if 'quoted_message_id' in data:
                frame['quoted_message_id'] = data['quoted_message_id']
            if data.get('project_id'):  # sent from a project room (B-32)
                frame['project_id'] = data['project_id']
            if data.get('artifact_id'):  # "Ask about this" on an item (B-21)
                frame['artifact_id'] = data['artifact_id']
            self.outbox.to_conversation(conv.id, frame)
        except Exception:  # the message is stored; it must not fail because of the channel
            log.exception('Could not queue message %s for its session.', data.get('id'))


class _TaskWatch:
    def __init__(self, gateway: Gateway, inner: Publisher) -> None:
        self.gateway = gateway
        self.inner = inner
        # The app event log at the end of the publisher chain (`ws.OwnerPublisher.events`).
        found: Any = inner
        while found is not None and not hasattr(found, 'events'):
            found = getattr(found, 'inner', None)
        self.events: EventLog | None = getattr(found, 'events', None)

    def publish(self, type: str, data: dict[str, Any]) -> None:
        self.inner.publish(type, data)
        try:
            self.gateway.on_task_event(type, data)
        except Exception:
            log.exception('The channel gateway could not follow a %s event.', type)
