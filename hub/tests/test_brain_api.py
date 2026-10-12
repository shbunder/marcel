"""`/brain/…`: the brain tools' side of the hub (S-05.3), through the real app.

The controls each have a test here that goes red when the control is removed: the tunnel
refusal, the hub secret, the brain-only session check, the own-agent scoping, memory paths that
cannot leave the memory folder, and `location=auto` refused.
"""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlmodel import Session, select
from test_channel_fakes import SECRET, ChannelHub, channel_hub, session_id
from test_tasks_fakes import validator

from marcel_hub.brain_api import NotSetUp
from marcel_hub.models import (
    Agent,
    Artifact,
    Conversation,
    ConversationKind,
    Message,
    MessageKind,
    MessageRole,
    Project,
    Task,
    TaskLocation,
    TaskState,
)

TASK = validator('app-api.yaml#/components/schemas/Task')
PROJECT = validator('app-api.yaml#/components/schemas/Project')
ARTIFACT = validator('app-api.yaml#/components/schemas/Artifact')
MEMORY_FILE = validator('app-api.yaml#/components/schemas/MemoryFile')


class Brain:
    """Calls `/brain` as a session would: hub secret and session id in the headers."""

    def __init__(self, hub: ChannelHub, sid: str, secret: str = SECRET) -> None:
        self.hub = hub
        self.headers = {
            'X-Marcel-Secret': secret,
            'X-Marcel-Session': sid,
            'Authorization': '',  # the phone's token is not what lets the brain in
        }

    def get(self, route: str, **params: Any) -> Any:
        return self.hub.client.get(f'/brain{route}', params=params, headers=self.headers)

    def post(self, path: str, body: Any = None, **kw: Any) -> Any:
        return self.hub.client.post(f'/brain{path}', json=body, headers=self.headers, **kw)

    def put(self, path: str, body: Any) -> Any:
        return self.hub.client.put(f'/brain{path}', json=body, headers=self.headers)

    def patch(self, path: str, body: Any) -> Any:
        return self.hub.client.patch(f'/brain{path}', json=body, headers=self.headers)

    def delete(self, path: str) -> Any:
        return self.hub.client.delete(f'/brain{path}', headers=self.headers)


@pytest.fixture
def hub(tmp_path: Path) -> Iterator[ChannelHub]:
    with channel_hub(tmp_path) as hub:
        yield hub


@pytest.fixture
def brain(hub: ChannelHub) -> Brain:
    return Brain(hub, hub.brain())


def spawn(brain: Brain, **body: Any) -> Any:
    body = {'title': 'Check disk usage', 'prompt': 'List the five largest volumes.',
            'location': 'nuc', **body}  # fmt: skip
    return brain.post('/tasks', body)


def other_agent(hub: ChannelHub) -> str:
    with Session(hub.engine) as s:
        agent = Agent(owner_user_id='usr_other', name='Odile', animal='owl', palette='blue',
                      seed='2')  # fmt: skip
        s.add(agent)
        s.commit()
        return agent.id


# Who may call (controls) --------------------------------------------------------------------


def test_the_brain_is_let_in_with_the_secret_and_its_session(brain: Brain) -> None:
    res = brain.get('/tasks')
    assert res.status_code == 200, res.text
    assert res.json() == {'items': [], 'more': False}


def test_a_side_thread_may_use_the_tools_too(hub: ChannelHub) -> None:
    _, sid = hub.side()
    assert Brain(hub, sid).get('/tasks').status_code == 200


def test_a_request_through_the_tunnel_is_hidden(brain: Brain) -> None:
    res = brain.hub.client.get(
        '/brain/tasks', headers={**brain.headers, 'cf-connecting-ip': '203.0.113.9'}
    )
    assert res.status_code == 404
    assert res.json()['code'] == 'not_found'


def test_a_wrong_secret_is_refused(hub: ChannelHub) -> None:
    res = Brain(hub, hub.brain(), secret='hs_' + 'f' * 40).get('/tasks')
    assert res.status_code == 401
    assert res.json() == {
        'code': 'bad_secret',
        'message': 'The hub secret does not match this hub. Redeploy.',
    }


def test_no_secret_header_is_refused(brain: Brain) -> None:
    del brain.headers['X-Marcel-Secret']
    assert brain.get('/tasks').status_code == 401


def test_a_hub_without_a_secret_refuses_everyone(tmp_path: Path) -> None:
    with channel_hub(tmp_path, secret=None) as hub:
        res = Brain(hub, hub.brain(), secret='').get('/tasks')
    assert res.status_code == 503
    assert 'Redeploy' in res.json()['message']


def test_a_device_token_does_not_open_the_brain_routes(hub: ChannelHub) -> None:
    res = hub.client.get('/brain/tasks')  # the phone's token only
    assert res.status_code == 401


def test_a_worker_session_cannot_use_the_brain_tools(hub: ChannelHub) -> None:
    _, sid = hub.task()
    worker = Brain(hub, sid)
    res = spawn(worker)
    assert res.status_code == 403
    assert res.json()['code'] == 'not_brain'
    assert hub.runner.started == []


def test_an_unknown_session_is_refused(hub: ChannelHub) -> None:
    hub.brain()
    res = Brain(hub, session_id()).get('/tasks')
    assert res.status_code == 401
    assert res.json()['code'] == 'unknown_session'


def test_the_brain_routes_are_not_under_api(brain: Brain) -> None:
    res = brain.hub.client.get('/api/brain/tasks', headers=brain.headers)
    assert res.status_code == 404


# Tasks --------------------------------------------------------------------------------------


def test_spawn_starts_a_task_in_the_inbox(brain: Brain) -> None:
    res = spawn(brain, expected_minutes=30)
    assert res.status_code == 201, res.text
    task = TASK.validate(res.json()) or res.json()
    assert task['state'] == 'starting'
    assert task['model'] == 'sonnet'  # the agent's worker model
    assert task['origin'] == 'marcel'
    assert 'expected_by' in task
    inbox = brain.get('/projects').json()[-1]
    assert task['project_id'] == inbox['id'] and inbox['slug'] == 'inbox'
    assert brain.hub.runner.started[0][1].endswith('List the five largest volumes.')


def test_spawn_rejects_auto_with_a_hint_to_choose(brain: Brain) -> None:
    res = spawn(brain, location='auto')
    assert res.status_code == 400
    message = res.json()['message']
    assert 'decide yourself' in message and '"nuc"' in message and '"cloud"' in message
    assert brain.hub.runner.started == []


def test_spawn_rejects_an_unknown_location(brain: Brain) -> None:
    res = spawn(brain, location='mars')
    assert res.status_code == 400 and res.json()['message'] == 'location must be "nuc" or "cloud".'


def test_a_cloud_task_needs_a_repo(brain: Brain) -> None:
    res = spawn(brain, location='cloud')
    assert res.status_code == 400 and 'repo' in res.json()['message']
    res = spawn(brain, location='cloud', repo='shbunder/marcel', model='opus')
    assert res.status_code == 201, res.text
    assert res.json()['location'] == 'cloud' and res.json()['model'] == 'opus'


def test_spawn_validates_its_input(brain: Brain) -> None:
    for bad in ({'title': ''}, {'repo': 'not a repo'}, {'model': '-rm'}, {'expected_minutes': 0},
                {'colour': 'red'}):  # fmt: skip
        res = spawn(brain, **bad)
        assert res.status_code == 400, bad
        assert res.json()['code'] == 'bad_request'
    assert brain.post('/tasks').status_code == 400


def test_spawn_files_the_task_in_a_project_by_slug_or_id(brain: Brain) -> None:
    project = brain.post('/projects', {'name': 'Garden'}).json()
    by_slug = spawn(brain, project='garden').json()
    by_id = spawn(brain, project=project['id']).json()
    assert by_slug['project_id'] == by_id['project_id'] == project['id']


def test_spawn_into_an_unknown_or_archived_project_is_refused(brain: Brain) -> None:
    res = spawn(brain, project='nowhere')
    assert res.status_code == 404 and 'projects_list' in res.json()['message']
    with Session(brain.hub.engine) as s:
        project = Project(agent_id=brain.hub.agent_id, slug='old', name='Old', archived=True,
                          board_artifact_id='art_x')  # fmt: skip
        s.add(project)
        s.commit()
    res = spawn(brain, project='old')
    assert res.status_code == 409 and res.json()['code'] == 'project_archived'


def test_list_get_send_stop_and_move(brain: Brain) -> None:
    task = spawn(brain).json()
    garden = brain.post('/projects', {'name': 'Garden'}).json()

    listed = brain.get('/tasks', state='starting,working').json()['items']
    assert [t['id'] for t in listed] == [task['id']]
    assert brain.get('/tasks', state='done').json()['items'] == []
    assert brain.get('/tasks', project='garden').json()['items'] == []

    got = brain.get(f'/tasks/{task["id"]}').json()
    assert got['id'] == task['id']
    assert [e['type'] for e in got['recent_events']] == ['state', 'note']

    sent = brain.post(f'/tasks/{task["id"]}/messages', {'text': 'Also count the images.'})
    assert sent.status_code == 200, sent.text
    note = brain.hub.task_events(task['id'], 'note')[-1]
    assert note.data_json == {'author': 'marcel', 'text': 'Also count the images.'}

    moved = brain.post(f'/tasks/{task["id"]}/move', {'project': 'garden'})
    assert moved.status_code == 200 and moved.json()['project_id'] == garden['id']

    stopped = brain.post(f'/tasks/{task["id"]}/stop')
    assert stopped.status_code == 200 and stopped.json()['state'] == 'stopped'
    assert brain.hub.runner.stopped == [f'ses-{task["id"]}']
    reasons = [e.data_json.get('reason') for e in brain.hub.task_events(task['id'], 'state')]
    assert reasons[-1] == 'Stopped by Marcel'

    late = brain.post(f'/tasks/{task["id"]}/messages', {'text': 'One more thing.'})
    assert late.status_code == 409 and late.json()['code'] == 'task_finished'


def test_list_rejects_an_unknown_state_and_project(brain: Brain) -> None:
    assert brain.get('/tasks', state='sleeping').status_code == 400
    assert brain.get('/tasks', project='nowhere').status_code == 404


def test_stop_says_so_when_the_runner_is_down(brain: Brain) -> None:
    task = spawn(brain).json()
    brain.hub.runner.down = True
    res = brain.post(f'/tasks/{task["id"]}/stop')
    assert res.status_code == 503 and res.json()['code'] == 'runner_unreachable'


def test_the_brain_cannot_touch_another_agents_task(brain: Brain) -> None:
    hub = brain.hub
    other = other_agent(hub)
    with Session(hub.engine) as s:
        task = Task(agent_id=other, title='Odile’s', location=TaskLocation.nuc,
                    state=TaskState.working, model='sonnet', origin='marcel',  # pyright: ignore[reportArgumentType]
                    session_id='ses-odile')  # fmt: skip
        s.add(task)
        s.commit()
        task_id = task.id
    for res in (
        brain.get(f'/tasks/{task_id}'),
        brain.post(f'/tasks/{task_id}/messages', {'text': 'hi'}),
        brain.post(f'/tasks/{task_id}/stop'),
        brain.post(f'/tasks/{task_id}/move', {'project': 'inbox'}),
    ):
        assert res.status_code == 404, res.text
        assert res.json()['code'] == 'task_not_found'
    assert brain.get('/tasks').json()['items'] == []
    assert hub.runner.stopped == []
    with Session(hub.engine) as s:
        assert s.get(Task, task_id).state == TaskState.working  # pyright: ignore[reportOptionalMemberAccess]


def test_the_brain_cannot_use_another_agents_project(brain: Brain) -> None:
    hub = brain.hub
    other = other_agent(hub)
    with Session(hub.engine) as s:
        theirs = s.exec(select(Project).where(Project.agent_id == other)).one()
    assert spawn(brain, project=theirs.id).status_code == 404
    assert brain.patch(f'/projects/{theirs.id}', {'goal': 'x'}).status_code == 404
    assert all(p['agent_id'] == hub.agent_id for p in brain.get('/projects').json())


def test_a_missing_task_is_named(brain: Brain) -> None:
    res = brain.get('/tasks/tsk_nope')
    assert res.status_code == 404
    assert res.json()['message'] == 'There is no task tsk_nope. tasks_list shows them.'


# Projects -----------------------------------------------------------------------------------


def test_projects_create_update_and_list(brain: Brain) -> None:
    link = 'https://claude.ai/project/1'
    body = {'name': 'Energy contracts', 'goal': 'Cheaper gas', 'claude_project_url': link}
    res = brain.post('/projects', body)
    assert res.status_code == 201, res.text
    project = res.json()
    PROJECT.validate(project)
    assert project['slug'] == 'energy-contracts'
    assert (brain.hub.tmp_path / 'memory' / 'projects' / 'energy-contracts.md').is_file()

    taken = brain.post('/projects', {'name': 'Again', 'slug': 'energy-contracts'})
    assert taken.status_code == 409 and taken.json()['code'] == 'slug_taken'

    updated = brain.patch('/projects/energy-contracts', {'instructions': 'Show yearly totals.'})
    assert updated.status_code == 200 and updated.json()['instructions'] == 'Show yearly totals.'
    assert brain.patch('/projects/energy-contracts', {}).status_code == 400
    assert brain.patch('/projects/energy-contracts', {'name': None}).status_code == 400
    assert brain.patch('/projects/nowhere', {'goal': 'x'}).status_code == 404
    assert brain.patch('/projects/energy-contracts', {'colour': 'red'}).status_code == 400
    assert brain.post('/projects', {'name': ''}).status_code == 400

    slugs = [p['slug'] for p in brain.get('/projects').json()]
    assert slugs == ['energy-contracts', 'inbox']
    assert brain.get('/projects', archived='true').json() == []


def test_board_note_says_boards_are_not_built_yet(brain: Brain) -> None:
    res = brain.post('/projects/inbox/notes', {'text': 'Luminus is still cheapest.'})
    assert res.status_code == 503
    assert res.json() == {
        'code': 'not_set_up',
        'message': 'Project boards are not built yet, so the note was not kept.',
    }
    assert brain.post('/projects/nowhere/notes', {'text': 'x'}).status_code == 404
    assert brain.post('/projects/inbox/notes', {'text': ''}).status_code == 400


def test_board_note_goes_to_the_boards_port(brain: Brain) -> None:
    class Boards:
        notes: list[tuple[str, str]] = []

        def add_note(self, project_id: str, text: str) -> dict[str, Any]:
            self.notes.append((project_id, text))
            return {'project_id': project_id}

    boards = Boards()
    brain.hub.app.state.brain_ports.boards = boards
    res = brain.post('/projects/inbox/notes', {'text': 'All quiet.'})
    assert res.status_code == 200
    assert boards.notes == [(res.json()['project_id'], 'All quiet.')]


# Artifacts ----------------------------------------------------------------------------------


def test_artifacts_publish_stores_a_link_in_the_inbox(brain: Brain) -> None:
    res = brain.post('/artifacts', {'kind': 'link', 'title': 'Engie offer',
                                    'url': 'https://engie.be/offer'})  # fmt: skip
    assert res.status_code == 201, res.text
    art = res.json()
    ARTIFACT.validate(art)
    assert art['current_version'] == 1 and art['trusted'] is False
    assert brain.hub.events('artifact.created')[-1]['data']['id'] == art['id']


def test_artifacts_publish_stores_a_workers_file(brain: Brain) -> None:
    (brain.hub.workers / 'notes.md').write_text('# Notes\n')
    res = brain.post('/artifacts', {'kind': 'doc', 'title': 'Notes',
                                    'path': str(brain.hub.workers / 'notes.md')})  # fmt: skip
    assert res.status_code == 201, res.text
    assert res.json()['content_type'] == 'text/markdown'


def test_artifacts_publish_refusals_are_plain(brain: Brain) -> None:
    res = brain.post('/artifacts', {'kind': 'link', 'title': 'x', 'url': 'ftp://x'})
    assert res.status_code == 400 and 'not a web address' in res.json()['message']
    res = brain.post('/artifacts', {'kind': 'link', 'title': 'x', 'url': 'https://x.be',
                                    'artifact_id': 'art_1'})  # fmt: skip
    assert res.status_code == 400 and 'new version' in res.json()['message']
    res = brain.post('/artifacts', {'kind': 'video', 'title': 'x'})
    assert res.status_code == 400
    assert brain.post('/artifacts', {'kind': 'link', 'title': 'x', 'url': 'https://x.be',
                                     'project': 'nowhere'}).status_code == 404  # fmt: skip


def test_artifacts_publish_without_a_store(brain: Brain) -> None:
    brain.hub.app.state.brain_ports.artifacts.store = None
    res = brain.post('/artifacts', {'kind': 'link', 'title': 'x', 'url': 'https://x.be'})
    assert res.status_code == 503 and res.json()['code'] == 'not_set_up'


# Memory -------------------------------------------------------------------------------------


def test_memory_write_then_read_commits_as_agent(brain: Brain) -> None:
    res = brain.put('/memory', {'path': 'MEMORY.md', 'content': '# Memory\n- Likes tea.\n'})
    assert res.status_code == 200, res.text
    saved = res.json()
    MEMORY_FILE.validate(saved)
    assert saved['updated_by'] == 'agent'
    read = brain.get('/memory', path='MEMORY.md').json()
    assert read['content'] == '# Memory\n- Likes tea.\n' and read['etag'] == saved['etag']
    tree = brain.get('/memory').json()['files']
    assert [f['path'] for f in tree] == ['MEMORY.md']

    again = brain.put('/memory', {'path': 'MEMORY.md', 'content': 'x'})  # no etag: a conflict
    assert again.status_code == 409
    assert saved['etag'] in again.json()['message']
    ok = brain.put('/memory', {'path': 'MEMORY.md', 'content': 'y\n', 'etag': saved['etag']})
    assert ok.status_code == 200 and ok.json()['content'] == 'y\n'


def test_memory_paths_cannot_leave_the_memory_folder(brain: Brain, tmp_path: Path) -> None:
    outside = tmp_path / 'outside.md'
    for path in ('../outside.md', '/etc/passwd.md', '.git/config.md', 'a/../../outside.md'):
        res = brain.put('/memory', {'path': path, 'content': 'pwned'})
        assert res.status_code == 400, path
        assert res.json()['code'] == 'invalid_memory_path'
        assert brain.get('/memory', path=path).status_code == 400
    assert not outside.exists()
    memory = brain.hub.tmp_path / 'memory'
    memory.mkdir(exist_ok=True)
    (memory / 'link.md').symlink_to(outside)
    assert brain.put('/memory', {'path': 'link.md', 'content': 'pwned'}).status_code == 400
    assert not outside.exists()


def test_memory_errors_are_plain(brain: Brain) -> None:
    res = brain.get('/memory', path='nothing.md')
    assert res.status_code == 404 and res.json()['message'] == 'There is no memory file nothing.md.'
    gone = brain.put('/memory', {'path': 'gone.md', 'content': 'x', 'etag': 'abc'})
    assert gone.status_code == 404 and 'without an etag' in gone.json()['message']
    too_long = brain.put('/memory', {'path': 'big.md', 'content': 'é' * 600_000})
    assert too_long.status_code == 400 and '1 MB' in too_long.json()['message']
    memory = brain.hub.tmp_path / 'memory'
    (memory / 'huge.md').write_bytes(b'x' * 1_000_001)
    assert brain.get('/memory', path='huge.md').json()['code'] == 'memory_file_too_big'
    huge = brain.put('/memory', {'path': 'huge.md', 'content': 'x', 'etag': 'abc'})
    assert huge.json()['code'] == 'memory_file_too_big'


def test_memory_says_so_when_git_is_missing(brain: Brain, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr('marcel_hub.memory.GIT', 'no-such-git-binary')
    res = brain.put('/memory', {'path': 'MEMORY.md', 'content': 'x'})
    assert res.status_code == 503 and res.json()['code'] == 'memory_unavailable'
    assert brain.get('/memory').json()['code'] == 'memory_unavailable'


# Schedules ----------------------------------------------------------------------------------

MONDAYS = {'name': 'Dependency report', 'cron': '0 8 * * 1', 'tz': 'Europe/Brussels',
           'location': 'cloud', 'prompt': 'List outdated dependencies.'}  # fmt: skip


def test_schedules_say_the_scheduler_is_not_built_yet(brain: Brain) -> None:
    for res in (brain.post('/schedules', MONDAYS), brain.get('/schedules'),
                brain.delete('/schedules/sch_1')):  # fmt: skip
        assert res.status_code == 503
        assert res.json()['message'].startswith('The scheduler is not built yet')


def test_schedule_create_checks_cron_and_time_zone(brain: Brain) -> None:
    res = brain.post('/schedules', {**MONDAYS, 'cron': '0 8 * *'})
    assert res.status_code == 400 and 'five fields' in res.json()['message']
    res = brain.post('/schedules', {**MONDAYS, 'tz': 'Mars/Olympus'})
    assert res.status_code == 400 and 'Europe/Brussels' in res.json()['message']
    assert brain.post('/schedules', {**MONDAYS, 'location': 'moon'}).status_code == 400


class FakeSchedules:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}

    def create(self, agent_id: str, spec: dict[str, Any]) -> dict[str, Any]:
        row = {'id': f'sch_{len(self.rows) + 1}', 'agent_id': agent_id, **spec,
               'paused': False, 'builtin': False}  # fmt: skip
        self.rows[row['id']] = row
        return row

    def list(self, agent_id: str) -> list[dict[str, Any]]:
        return [r for r in self.rows.values() if r['agent_id'] == agent_id]

    def delete(self, agent_id: str, schedule_id: str) -> None:
        if schedule_id == 'sch_digest_morning':
            raise PermissionError('The morning digest can be paused, not deleted.')
        if self.rows.get(schedule_id, {}).get('agent_id') != agent_id:
            raise LookupError(schedule_id)
        del self.rows[schedule_id]


def test_schedules_go_to_the_schedules_port(brain: Brain) -> None:
    brain.hub.app.state.brain_ports.schedules = FakeSchedules()
    created = brain.post('/schedules', MONDAYS)
    assert created.status_code == 201 and created.json()['agent_id'] == brain.hub.agent_id
    assert [s['id'] for s in brain.get('/schedules').json()] == ['sch_1']
    assert brain.delete('/schedules/sch_1').json() == {'deleted': 'sch_1'}
    assert brain.delete('/schedules/sch_1').status_code == 404
    builtin = brain.delete('/schedules/sch_digest_morning')
    assert builtin.status_code == 409 and 'paused' in builtin.json()['message']


# Notify and usage ---------------------------------------------------------------------------


def test_notify_posts_in_the_main_chat_and_pushes(brain: Brain) -> None:
    pushed: list[tuple[Any, str | None]] = []
    pusher = brain.hub.app.state.pusher
    pusher.push = lambda n, user_id: pushed.append((n, user_id)) or True
    res = brain.post('/notify', {'text': 'The NAS is almost full.', 'level': 'urgent'})
    assert res.status_code == 200, res.text
    message_id = res.json()['message_id']
    assert res.json()['pushed'] is True
    with Session(brain.hub.engine) as s:
        message = s.get(Message, message_id)
    assert message is not None and message.conversation_id == brain.hub.main_id
    assert message.body_json == {'text': 'The NAS is almost full.'}
    (n, user_id), = pushed  # fmt: skip
    assert user_id == 'usr_owner'
    assert n.to_api() == {
        'title': 'Marcel',
        'body': 'The NAS is almost full.',
        'category': 'broke',
        'level': 'urgent',
        'deep_link': f'marcel://message/{message_id}',
        'collapse_key': message_id,
    }
    validator('app-api.yaml#/components/schemas/PushNotification').validate(n.to_api())
    brain.post('/notify', {'text': 'The digest is ready.'})
    assert pushed[-1][0].category == 'digest' and pushed[-1][0].level == 'normal'


def test_notify_validates_its_input(brain: Brain) -> None:
    assert brain.post('/notify', {'text': ''}).status_code == 400
    assert brain.post('/notify', {'text': 'x', 'level': 'loud'}).status_code == 400


def test_notify_without_a_pusher_still_posts(brain: Brain) -> None:
    del brain.hub.app.state.pusher
    res = brain.post('/notify', {'text': 'Hello.'})
    assert res.status_code == 200 and res.json()['pushed'] is False


def test_usage_says_it_is_not_read_yet_until_the_guard_plugs_in(brain: Brain) -> None:
    res = brain.get('/usage')
    assert res.status_code == 503 and res.json()['code'] == 'not_set_up'

    class Usage:
        def current(self) -> dict[str, Any]:
            return {'five_hour': {'used_pct': 12.0}}

    brain.hub.app.state.brain_ports.usage = Usage()
    assert brain.get('/usage').json() == {'five_hour': {'used_pct': 12.0}}


def test_not_set_up_is_an_exception_with_the_message() -> None:
    assert str(NotSetUp('x')) == 'x'


# Review fixes ---------------------------------------------------------------------------------


def brain_of(hub: ChannelHub, agent_id: str) -> Brain:
    """A brain session for another agent, with its own main conversation."""
    sid = session_id()
    with Session(hub.engine) as s:
        s.add(Conversation(agent_id=agent_id, kind=ConversationKind.main, brain_session_id=sid))
        s.commit()
    return Brain(hub, sid)


def test_a_project_slug_of_another_agent_is_not_found(brain: Brain) -> None:
    hub = brain.hub
    theirs = brain_of(hub, other_agent(hub))
    assert theirs.post('/projects', {'name': 'Odile only'}).status_code == 201
    for res in (
        spawn(brain, project='odile-only'),
        brain.get('/tasks', project='odile-only'),
        brain.patch('/projects/odile-only', {'goal': 'x'}),
        brain.post('/projects/odile-only/notes', {'text': 'x'}),
    ):
        assert res.status_code == 404, res.text
        assert res.json()['code'] == 'project_not_found'
    assert hub.runner.started == []


def test_brain_artifacts_are_filed_under_the_callers_project(brain: Brain) -> None:
    hub = brain.hub
    second = brain_of(hub, other_agent(hub))  # not the hub's first agent
    link = {'kind': 'link', 'title': 'Offer', 'url': 'https://x.be/offer'}
    in_inbox = second.post('/artifacts', link)
    assert in_inbox.status_code == 201, in_inbox.text
    second.post('/projects', {'name': 'Energy'})
    in_energy = second.post('/artifacts', {**link, 'project': 'energy'})
    assert in_energy.status_code == 201, in_energy.text
    projects = {p['slug']: p['id'] for p in second.get('/projects').json()}
    assert in_inbox.json()['project_id'] == projects['inbox']
    assert in_energy.json()['project_id'] == projects['energy']
    mine = brain.post('/artifacts', link).json()
    assert mine['project_id'] == brain.get('/projects').json()[-1]['id']
    with Session(hub.engine) as s:  # stored as answered, and nothing filed afterwards changed
        assert s.get(Artifact, in_energy.json()['id']).project_id == projects['energy']  # pyright: ignore[reportOptionalMemberAccess]
    worker_art = {'kind': 'link', 'title': 'w', 'url': 'https://x.be/w'}
    task_id, _ = hub.task()
    art = hub.app.state.artifacts.publish_reported(worker_art, task_id)
    assert art.project_id == hub.registry.get(task_id).project_id  # workers' filing unchanged


def test_an_oversized_field_is_refused(brain: Brain) -> None:
    long = 'x' * 5000
    cases = [
        ('/projects', {'name': 'Garden', 'folder': '/' + long}),
        ('/tasks', {'title': 't', 'prompt': 'p', 'location': 'nuc', 'project': long}),
        ('/tasks', {'title': 't', 'prompt': 'p', 'location': 'nuc', 'message_id': long}),
        ('/artifacts', {'kind': 'link', 'title': 't', 'url': 'https://x.be/' + long}),
        ('/artifacts', {'kind': 'doc', 'title': 't', 'path': '/' + long}),
        ('/schedules', {'name': 'n', 'cron': '0 8 * * 1', 'tz': long, 'location': 'nuc',
                        'prompt': 'p'}),
    ]  # fmt: skip
    for route, body in cases:
        res = brain.post(route, body)
        assert res.status_code == 400, (route, body.keys())
        assert 'at most' in res.json()['message']
    assert brain.put('/memory', {'path': long + '.md', 'content': 'x'}).status_code == 400
    assert brain.put('/memory', {'path': 'a.md', 'content': 'x', 'etag': long}).status_code == 400
    assert brain.get('/memory', path=long + '.md').status_code == 400
    assert brain.get('/tasks', project=long).status_code == 400
    assert brain.patch(f'/projects/{"p" * 81}', {'goal': 'x'}).status_code == 400


def test_an_oversized_body_is_not_read(brain: Brain) -> None:
    big = {'title': 't', 'prompt': 'p', 'location': 'nuc', 'padding': 'x' * 70_000}
    res = brain.post('/tasks', big)
    assert res.status_code == 413 and res.json()['code'] == 'too_large'

    # A body without a Content-Length is cut off as it is read.
    def chunks() -> Any:
        for _ in range(100):
            yield b'x' * 1024

    res = brain.hub.client.post('/brain/notify', content=chunks(), headers=brain.headers)
    assert res.status_code == 413
    # A memory write may carry a whole memory file.
    ok = brain.put('/memory', {'path': 'big.md', 'content': 'x' * 900_000})
    assert ok.status_code == 200, ok.text
    assert brain.hub.runner.started == []


def test_repo_is_owner_slash_name_only(brain: Brain) -> None:
    for repo in ('-x/marcel', 'shbunder/-u', '../etc', 'shbunder/..', 'a/b/c', 'shbunder/mar..cel',
                 'shbunder/marcel --upload-pack=x'):  # fmt: skip
        res = spawn(brain, location='cloud', repo=repo)
        assert res.status_code == 400, repo
        assert brain.post('/projects', {'name': 'R', 'repo': repo}).status_code == 400, repo
    assert spawn(brain, location='cloud', repo='shbunder/marcel.v3_x-1').status_code == 201
    assert brain.hub.runner.started[-1] is not None


def test_message_id_must_be_in_the_brains_own_conversation(brain: Brain) -> None:
    hub = brain.hub
    side_id, _ = hub.side()
    with Session(hub.engine) as s:
        mine = Message(conversation_id=hub.main_id, role=MessageRole.user, kind=MessageKind.text,
                       body_json={'text': 'Check the disks'})  # fmt: skip
        elsewhere = Message(conversation_id=side_id, role=MessageRole.user,
                            kind=MessageKind.text, body_json={'text': 'x'})  # fmt: skip
        s.add(mine)
        s.add(elsewhere)
        s.commit()
        mine_id, elsewhere_id = mine.id, elsewhere.id
    for bad in ('msg_nope', elsewhere_id):
        res = spawn(brain, message_id=bad)
        assert res.status_code == 404 and res.json()['code'] == 'message_not_found'
    assert hub.runner.started == []
    res = spawn(brain, message_id=mine_id)
    assert res.status_code == 201 and res.json()['parent_message_id'] == mine_id
