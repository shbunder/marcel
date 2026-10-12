"""The mock's world: stored objects and the event log. The scripted runs are in scenario.py."""

from __future__ import annotations

import asyncio
import copy
import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from . import boards, contract

MAIN_CONVERSATION = 'cnv_main_marcel'
AGENT_ID = 'agt_marcel'
INBOX = 'prj_inbox'
# The contract's Task and Artifact examples file their work under this id; the mock gives it a
# project so every example points at something that exists.
MARCEL_DEV = 'prj_01J9ZM1'
SCENARIO_REPO = 'shbunder/marcel'
HUB_AUTHOR = 'the hub'
BOARD_TYPE = 'application/json'
FINISHED = ('done', 'failed', 'stopped')


def now() -> str:
    return datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')


def slugify(name: str) -> str:
    slug = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')[:40].strip('-')
    return slug or 'project'


class Refused(Exception):
    """A request the hub would refuse: carries the status and the `Error` body."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


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
        self.versions: dict[str, list[dict[str, Any]]] = {}  # oldest first
        self.contents: dict[str, dict[int, str]] = {}  # artifact id -> version -> body
        self.projects: dict[str, dict[str, Any]] = {}
        self.conversations: dict[str, dict[str, Any]] = {}
        self.client_ids: dict[str, dict[str, Any]] = {}
        self.answers: dict[str, asyncio.Event] = {}
        self.runs: set[asyncio.Task[None]] = set()
        self.counter = 0
        self._seed()

    # ---- seed: the contract's examples, plus what they point at -------------------------

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

        energy = contract.example('Project')
        inbox = {
            **copy.deepcopy(energy),
            'id': INBOX,
            'slug': 'inbox',
            'name': 'Inbox',
            'goal': 'Threads that fit no project yet.',
            'board_artifact_id': 'art_board_inbox',
        }
        dev = {
            **copy.deepcopy(energy),
            'id': MARCEL_DEV,
            'slug': 'marcel-dev',
            'name': 'Marcel dev',
            'goal': 'Marcel builds and improves itself.',
            'repo': SCENARIO_REPO,
            'board_artifact_id': 'art_board_marcel_dev',
        }
        for project in (inbox, dev, energy):
            self.projects[project['id']] = project
            if project['board_artifact_id'] not in self.artifacts:
                self.artifacts[project['board_artifact_id']] = self._new_board(project)
        for art in self.artifacts.values():
            self._seed_versions(art)
        for project in self.projects.values():
            project['counts'] = self.counts(project['id'])

    def _new_board(self, project: dict[str, Any]) -> dict[str, Any]:
        return {
            'id': project['board_artifact_id'],
            'project_id': project['id'],
            'kind': 'board',
            'title': f'{project["name"]} — board',
            'current_version': 1,
            'updated_by': HUB_AUTHOR,
            'content_type': BOARD_TYPE,
            'created_at': project['created_at'],
            'updated_at': project['created_at'],
        }

    def _seed_versions(self, art: dict[str, Any]) -> None:
        """Versions 1..current for a seeded artifact; the current one carries its fields."""
        example = contract.example('ArtifactVersion')
        history = []
        for n in range(1, art['current_version'] + 1):
            history.append(
                {'artifact_id': art['id'], 'version': n, 'created_at': art['created_at']}
            )
        latest = self._version_record(art, art.get('updated_by'), None)
        latest['created_at'] = art['updated_at']
        if (example['artifact_id'], example['version']) == (art['id'], art['current_version']):
            latest = example
        history[-1] = latest
        self.versions[art['id']] = history
        for n in range(1, art['current_version'] + 1):
            body = self._body(art, n)
            if body is not None:
                self.contents.setdefault(art['id'], {})[n] = body

    def _version_record(
        self, art: dict[str, Any], author: str | None, summary: str | None
    ) -> dict[str, Any]:
        record: dict[str, Any] = {'artifact_id': art['id'], 'version': art['current_version']}
        if summary:
            record['summary'] = summary
        if author:
            record['author'] = author
        for key in ('task_id', 'url', 'content_type', 'size_bytes'):
            if key in art:
                record[key] = art[key]
        record['created_at'] = art['updated_at']
        return record

    def _body(self, art: dict[str, Any], version: int, note: str = '') -> str | None:
        """The bytes of one version. `pr`, `branch` and `link` have none."""
        if art['kind'] == 'board':
            return json.dumps(boards.build(self, self.projects[art['project_id']]), indent=2)
        if 'content_type' not in art:
            return None
        text = note or 'The mock hub has no real content for this document.'
        return f'# {art["title"]}\n\nVersion {version}. {text}\n'

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

    # ---- agent and messages --------------------------------------------------------------

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
        self,
        conversation_id: str,
        text: str,
        client_id: str | None,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        if client_id and client_id in self.client_ids:
            return self.client_ids[client_id]
        if project_id is not None and project_id not in self.projects:
            raise Refused(400, 'bad_request', 'There is no project with that id.')
        message: dict[str, Any] = {
            'id': self.next_id('msg'),
            'conversation_id': conversation_id,
            'role': 'user',
            'kind': 'text',
            'text': text,
            'created_at': now(),
        }
        if project_id:
            message['project_id'] = project_id
        if client_id:
            message['client_id'] = client_id
            self.client_ids[client_id] = message
        return self.add_message(message)

    # ---- tasks ---------------------------------------------------------------------------

    def create_task(self, task: dict[str, Any]) -> None:
        self.tasks[task['id']] = task
        self.emit('task.created', task)
        self.refresh(task['project_id'])

    def update_task(self, task: dict[str, Any], **changes: Any) -> None:
        if changes.get('state', 'needs_you') != 'needs_you':
            task.pop('waiting_for', None)
        task.update(changes, updated_at=now())
        self.emit('task.updated', task)
        self.refresh(task['project_id'])

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

    def move_task(self, task: dict[str, Any], project_id: str) -> dict[str, Any]:
        """B-31. The task's items follow it; both boards are rebuilt."""
        target = self.projects.get(project_id)
        if target is None:
            raise Refused(404, 'not_found', 'There is no project with that id.')
        if target['archived']:
            raise Refused(409, 'project_archived', f'{target["name"]} is archived.')
        source = task['project_id']
        if source == project_id:
            return task
        task.update(project_id=project_id, updated_at=now())
        for art in self.artifacts.values():
            if art.get('task_id') == task['id'] and art['kind'] != 'board':
                art['project_id'] = project_id
        self.emit(
            'task.moved',
            {'task_id': task['id'], 'from_project_id': source, 'to_project_id': project_id},
        )
        self.refresh(source)
        self.refresh(project_id)
        return task

    # ---- artifacts -----------------------------------------------------------------------

    def publish(self, art: dict[str, Any], task: dict[str, Any], note: str = '') -> None:
        """A task's new item, v1. The first item a task makes is announced in the chat (B-36)."""
        first = not any(a.get('task_id') == task['id'] for a in self.artifacts.values())
        stamp = now()
        art.update(
            task_id=task['id'],
            project_id=task['project_id'],
            current_version=1,
            updated_by=task['title'],
            created_at=stamp,
            updated_at=stamp,
        )
        self._store(art, task['title'], 'First version.', note)
        self.add_task_event(
            task['id'],
            'artifact',
            {'artifact_id': art['id'], 'kind': art['kind'], 'title': art['title']},
        )
        self.emit('artifact.created', art)
        if first:
            self.add_message(
                {
                    'id': self.next_id('msg'),
                    'conversation_id': MAIN_CONVERSATION,
                    'role': 'agent',
                    'kind': 'artifact',
                    'artifact_id': art['id'],
                    'project_id': art['project_id'],
                    'task_id': task['id'],
                    'created_at': now(),
                }
            )
        self.refresh(art['project_id'])

    def republish(
        self, art: dict[str, Any], task: dict[str, Any], summary: str, note: str = ''
    ) -> None:
        """A new version of a living item (B-34): `artifact.updated`, no second banner."""
        art.update(
            current_version=art['current_version'] + 1, updated_by=task['title'], updated_at=now()
        )
        version = self._store(art, task['title'], summary, note)
        self.add_task_event(
            task['id'],
            'artifact',
            {'artifact_id': art['id'], 'kind': art['kind'], 'title': art['title']},
        )
        self.emit('artifact.updated', {'artifact': art, 'version': version})
        self.refresh(art['project_id'])

    def _store(
        self, art: dict[str, Any], author: str, summary: str | None, note: str = ''
    ) -> dict[str, Any]:
        self.artifacts[art['id']] = art
        body = self._body(art, art['current_version'], note)
        if body is not None:
            self.contents.setdefault(art['id'], {})[art['current_version']] = body
            if art['kind'] != 'pr':
                art['size_bytes'] = len(body.encode())
        version = self._version_record(art, author, summary)
        self.versions.setdefault(art['id'], []).append(version)
        return version

    def content(self, art: dict[str, Any], version: int | None) -> str | None:
        return self.contents.get(art['id'], {}).get(version or art['current_version'])

    # ---- projects ------------------------------------------------------------------------

    def counts(self, project_id: str) -> dict[str, int]:
        week_ago = (datetime.now(UTC) - timedelta(days=7)).strftime('%Y-%m-%dT%H:%M:%SZ')
        tasks = [t for t in self.tasks.values() if t['project_id'] == project_id]
        return {
            'working': sum(t['state'] in ('starting', 'working') for t in tasks),
            'needs_you': sum(t['state'] == 'needs_you' for t in tasks),
            'done_7d': sum(t['state'] == 'done' and t['updated_at'] >= week_ago for t in tasks),
        }

    def refresh(self, project_id: str, force: bool = False) -> None:
        """Rebuild a project's board and counts. A board that changed gets a new version
        (`artifact.updated`); the project follows with `project.updated` when anything changed."""
        project = self.projects[project_id]
        board = self.artifacts[project['board_artifact_id']]
        body = self._body(board, board['current_version'] + 1)
        changed = body != self.content(board, None)
        if changed:
            board.update(
                current_version=board['current_version'] + 1,
                updated_by=HUB_AUTHOR,
                updated_at=now(),
            )
            content = json.loads(body or '{}')
            version = self._store(board, HUB_AUTHOR, boards.summary(content))
            self.emit('artifact.updated', {'artifact': board, 'version': version})
        counts = self.counts(project_id)
        if changed or force or counts != project['counts']:
            project.update(counts=counts, updated_at=now())
            self.emit('project.updated', project)

    def live_projects(self, agent_id: str | None, archived: bool) -> list[dict[str, Any]]:
        """The Map: newest activity first, `inbox` last."""
        found = [
            p
            for p in self.projects.values()
            if p['archived'] == archived and agent_id in (None, p['agent_id'])
        ]
        found.sort(key=lambda p: p['updated_at'], reverse=True)
        found.sort(key=lambda p: p['id'] == INBOX)
        return found

    def create_project(self, body: dict[str, Any]) -> dict[str, Any]:
        slug = body.get('slug') or slugify(body['name'])
        if any(p['slug'] == slug and not p['archived'] for p in self.projects.values()):
            raise Refused(409, 'slug_taken', f'A project called "{slug}" already exists.')
        stamp = now()
        project = {
            **{k: v for k, v in body.items() if k != 'slug'},
            'id': self.next_id('prj'),
            'slug': slug,
            'board_artifact_id': self.next_id('art'),
            'archived': False,
            'counts': {'working': 0, 'needs_you': 0, 'done_7d': 0},
            'created_at': stamp,
            'updated_at': stamp,
        }
        self.projects[project['id']] = project
        board = self._new_board(project)
        self._store(board, HUB_AUTHOR, 'The board was created with the project.')
        self.emit('artifact.created', board)
        self.emit('project.created', project)
        return project

    def update_project(self, project: dict[str, Any], changes: dict[str, Any]) -> dict[str, Any]:
        project.update(changes)
        self.refresh(project['id'], force=True)
        return project

    def archive_project(self, project: dict[str, Any]) -> dict[str, Any]:
        if project['id'] == INBOX:
            raise Refused(409, 'conflict', 'The inbox is always there; it cannot be archived.')
        live = [
            t
            for t in self.tasks.values()
            if t['project_id'] == project['id'] and t['state'] not in FINISHED
        ]
        if live:
            raise Refused(
                409, 'conflict', f'{len(live)} thread(s) in {project["name"]} are still going.'
            )
        if not project['archived']:
            project['archived'] = True
            self.refresh(project['id'], force=True)
        return project

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
        task = self.tasks.get(approval['task_id'])
        if task is not None:
            self.refresh(task['project_id'])
        waiter = self.answers.get(approval_id)
        if waiter is not None:
            waiter.set()
        return 200, approval

    # ---- runs ----------------------------------------------------------------------------

    @property
    def busy(self) -> bool:
        return any(not run.done() for run in self.runs)

    def start_scenario(self, message: dict[str, Any]) -> None:
        """One run at a time. A message sent while busy is stored, never refused (B-02)."""
        from .scenario import run_for

        if self.busy:
            return
        run = asyncio.create_task(run_for(self, message))
        self.runs.add(run)
        run.add_done_callback(self.runs.discard)

    async def stop(self) -> None:
        for run in list(self.runs):
            run.cancel()
        await asyncio.gather(*self.runs, return_exceptions=True)

    async def pause(self, factor: float = 1.0) -> None:
        await asyncio.sleep(self.step_delay * factor)
