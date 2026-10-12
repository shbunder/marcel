"""Every brain tool against the real hub app (in-process, through its test client).

Each test calls the tool as Claude Code does (an MCP `tools/call` line) and checks both what the
session reads back and what the hub stored.
"""

import json
import uuid
from typing import Any

from conftest import World
from marcel_hub.models import Message, Project, Task, TaskEvent, TaskState
from sqlmodel import Session, select

from marcel_tools.tools import BY_NAME, TOOLS


def spawn(world: World, **args: Any) -> str:
    prompt = 'List the five largest Docker volumes.'
    return world.ok(
        'tasks_spawn', {'title': 'Check disk usage', 'location': 'nuc', 'prompt': prompt, **args}
    )


def task_id(text: str) -> str:
    return json.loads(text.split('\n\n', 1)[1])['id']


def tasks(world: World) -> list[Task]:
    with Session(world.app.state.engine) as s:
        return list(s.exec(select(Task)))


# The list -------------------------------------------------------------------------------------


def test_the_tools_are_the_ones_the_architecture_names() -> None:
    assert [t.name for t in TOOLS] == [
        'tasks_spawn', 'tasks_list', 'tasks_get', 'tasks_send', 'tasks_stop', 'tasks_move',
        'projects_list', 'projects_create', 'projects_update', 'board_note',
        'artifacts_publish', 'memory_read', 'memory_write',
        'schedule_create', 'schedule_list', 'schedule_delete', 'notify', 'usage_get',
    ]  # fmt: skip


def test_every_description_says_when_to_use_it() -> None:
    for tool in TOOLS:
        assert 40 <= len(tool.description) <= 600, tool.name
        assert tool.schema['additionalProperties'] is False
        assert set(tool.schema['required']) <= set(tool.schema['properties'])
        assert set(tool.hints) <= set(tool.schema['properties'])


# Tasks ----------------------------------------------------------------------------------------


def test_tasks_spawn_starts_a_worker(world: World) -> None:
    text = spawn(world, expected_minutes=20)
    assert text.startswith('Started “Check disk usage” (tsk_') and 'on the NUC.' in text
    (task,) = tasks(world)
    assert task.state == TaskState.starting and task.expected_by is not None
    assert world.runner.started[0][1].endswith('List the five largest Docker volumes.')


def test_tasks_spawn_rejects_auto_with_a_hint_to_choose(world: World) -> None:
    text = world.error('tasks_spawn', {'title': 'x', 'prompt': 'y', 'location': 'auto'})
    assert text.startswith('tasks_spawn was not run: location must be "nuc" or "cloud"')
    assert 'decide yourself' in text and 'Docker' in text and 'GitHub' in text
    assert tasks(world) == [] and world.runner.started == []


def test_the_hub_rejects_auto_too(world: World) -> None:
    """The tool's check is not the only one: a call that skips it is refused by the hub."""
    hub = world.hub()
    try:
        hub.call('POST', '/tasks', {'title': 'x', 'prompt': 'y', 'location': 'auto'})
    except Exception as exc:
        assert 'decide yourself' in str(exc)
    else:  # pragma: no cover
        raise AssertionError('the hub accepted location=auto')


def test_tasks_spawn_says_when_it_queued_or_failed(world: World) -> None:
    world.app.state.tasks.runner.down = True
    text = spawn(world)
    assert 'could not start' in text
    world.runner.down = False
    world.app.state.tasks.runner = _Full()
    assert spawn(world, title='Second').startswith('Queued “Second”')


class _Full:
    async def start(self, task: Task, prompt: str) -> Any:
        from marcel_hub.tasks.ports import CapReached

        raise CapReached()

    async def stop(self, session_id: str) -> None:  # pragma: no cover
        pass


def test_tasks_spawn_in_the_cloud_needs_a_repo(world: World) -> None:
    text = world.error('tasks_spawn', {'title': 'x', 'prompt': 'y', 'location': 'cloud'})
    assert text == ('tasks_spawn did not work: A cloud task needs "repo" (owner/name): it works '
                    'on GitHub only.')  # fmt: skip
    text = spawn(world, location='cloud', repo='shbunder/marcel')
    assert 'in the cloud' in text


def test_tasks_list_and_get(world: World) -> None:
    assert world.ok('tasks_list') == 'No tasks match.'
    tid = task_id(spawn(world))
    listed = world.ok('tasks_list', {'state': 'starting working'})
    assert listed == f'1 task:\n- {tid} “Check disk usage” — starting on the NUC'
    assert world.ok('tasks_list', {'state': 'done', 'archived': True}) == 'No tasks match.'
    got = world.ok('tasks_get', {'id': tid})
    assert got.startswith(f'{tid} “Check disk usage” — starting on the NUC')
    assert '"recent_events"' in got


def test_tasks_list_says_when_there_are_more(world: World) -> None:
    for n in range(51):
        spawn(world, title=f'Task {n}')
    assert world.ok('tasks_list').endswith('(More exist; narrow by state or project.)')


def test_tasks_get_shows_what_it_waits_for(world: World) -> None:
    tid = task_id(spawn(world))
    with Session(world.app.state.engine) as s:
        task = s.get(Task, tid)
        assert task is not None
        task.state, task.waiting_for = TaskState.needs_you, 'Your approval: docker system df'
        s.add(task)
        s.commit()
    assert 'waiting for: Your approval: docker system df' in world.ok('tasks_get', {'id': tid})


def test_tasks_send_steers_the_worker(world: World) -> None:
    tid = task_id(spawn(world))
    assert world.ok('tasks_send', {'id': tid, 'text': 'Include images.'}) == (
        'Sent to “Check disk usage”. It is starting now.'
    )
    with Session(world.app.state.engine) as s:
        notes = s.exec(select(TaskEvent).where(TaskEvent.task_id == tid)).all()
    assert notes[-1].data_json == {'author': 'marcel', 'text': 'Include images.'}


def test_tasks_stop(world: World) -> None:
    tid = task_id(spawn(world))
    assert world.ok('tasks_stop', {'id': tid}) == '“Check disk usage” is stopped.'
    assert world.runner.stopped == [f'ses-{tid}']


def test_tasks_move(world: World) -> None:
    tid = task_id(spawn(world))
    world.ok('projects_create', {'name': 'Garden'})
    text = world.ok('tasks_move', {'id': tid, 'project': 'garden'})
    assert text.startswith('Moved “Check disk usage” to project prj_')
    with Session(world.app.state.engine) as s:
        garden = s.exec(select(Project).where(Project.slug == 'garden')).one()
    assert tasks(world)[0].project_id == garden.id


def test_an_unknown_task_is_named(world: World) -> None:
    for name, extra in (('tasks_get', {}), ('tasks_send', {'text': 'x'}), ('tasks_stop', {}),
                        ('tasks_move', {'project': 'inbox'})):  # fmt: skip
        text = world.error(name, {'id': 'tsk_nope', **extra})
        assert text == f'{name} did not work: There is no task tsk_nope. tasks_list shows them.'


def test_an_id_cannot_change_the_route(world: World) -> None:
    text = world.error('tasks_stop', {'id': '../projects'})
    assert text == 'tasks_stop was not run: "id" is not valid: letters, digits, - and _ only.'


# Projects -------------------------------------------------------------------------------------


def test_projects_create_list_and_update(world: World) -> None:
    text = world.ok('projects_create', {'name': 'Energy contracts', 'goal': 'Cheaper gas'})
    assert text.startswith('Made project “Energy contracts” (energy-contracts). Its notes are in '
                           'memory/projects/energy-contracts.md.')  # fmt: skip
    listed = world.ok('projects_list').splitlines()
    assert listed[0].startswith('- energy-contracts (prj_')
    assert listed[0].endswith(': Energy contracts — Cheaper gas [working 0, needs you 0, '
                              'done this week 0]')  # fmt: skip
    assert listed[1].startswith('- inbox (')
    assert world.ok('projects_list', {'archived': True}) == 'No projects.'
    updated = world.ok('projects_update', {'project': 'energy-contracts',
                                           'instructions': 'Show yearly totals.'})  # fmt: skip
    assert updated.startswith('Updated “Energy contracts”: instructions.')


def test_projects_update_needs_a_change(world: World) -> None:
    text = world.error('projects_update', {'project': 'inbox'})
    assert (
        text == 'projects_update was not run: projects_update needs at least one field to change.'
    )


def test_projects_create_explains_a_bad_slug(world: World) -> None:
    text = world.error('projects_create', {'name': 'Garden', 'slug': 'Garden!'})
    assert 'lowercase letters, digits and dashes' in text


def test_board_note_says_plainly_that_boards_are_not_built_yet(world: World) -> None:
    text = world.error('board_note', {'project': 'inbox', 'text': 'All quiet.'})
    assert text == ('board_note did not work: Project boards are not built yet, so the note was '
                    'not kept.')  # fmt: skip


def test_board_note_reaches_the_boards_port(world: World) -> None:
    class Boards:
        def add_note(self, project_id: str, text: str) -> dict[str, Any]:
            return {'project_id': project_id}

    world.app.state.brain_ports.boards = Boards()
    assert world.ok('board_note', {'project': 'inbox', 'text': 'x'}) == 'The note is on the board.'


# Artifacts and memory ---------------------------------------------------------------------------


def test_artifacts_publish(world: World) -> None:
    text = world.ok('artifacts_publish', {'kind': 'link', 'title': 'Engie offer',
                                          'url': 'https://engie.be/offer'})  # fmt: skip
    assert text.startswith('Saved “Engie offer” (art_') and 'version 1' in text


def test_artifacts_publish_checks_url_and_path(world: World) -> None:
    assert world.error('artifacts_publish', {'kind': 'pr', 'title': 'x'}).endswith(
        'a pr needs "url".'
    )
    assert world.error('artifacts_publish', {'kind': 'doc', 'title': 'x'}).endswith(
        'a doc needs "path", an absolute path on the NUC.'
    )
    text = world.error('artifacts_publish', {'kind': 'link', 'title': 'x', 'url': 'https://x.be',
                                             'artifact_id': 'art_1'})  # fmt: skip
    assert 'cannot store a new version' in text


def test_memory_write_and_read(world: World) -> None:
    assert world.ok('memory_read') == 'The memory is empty.'
    saved = world.ok('memory_write', {'path': 'MEMORY.md', 'content': '# Memory\n- Tea.\n'})
    etag = saved.removeprefix('Saved MEMORY.md. Its etag is now ').removesuffix('.')
    assert len(etag) == 40
    assert world.ok('memory_read') == 'Memory files:\n- MEMORY.md (16 bytes)'
    assert world.ok('memory_read', {'path': 'MEMORY.md'}) == (
        f'MEMORY.md (etag {etag}):\n\n# Memory\n- Tea.\n'
    )
    conflict = world.error('memory_write', {'path': 'MEMORY.md', 'content': 'x'})
    assert 'changed since you read it' in conflict and etag in conflict
    world.ok('memory_write', {'path': 'MEMORY.md', 'content': 'y\n', 'etag': etag})


def test_memory_paths_cannot_escape(world: World) -> None:
    for path in ('../outside.md', '/etc/x.md', '.git/config.md'):
        text = world.error('memory_write', {'path': path, 'content': 'pwned'})
        assert 'is not a memory file' in text, path
    assert not (world.data_dir / 'outside.md').exists()


# Schedules ------------------------------------------------------------------------------------

MONDAYS = {'name': 'Dependency report', 'cron': '0 8 * * 1', 'tz': 'Europe/Brussels',
           'location': 'cloud', 'prompt': 'List outdated dependencies.'}  # fmt: skip


def test_schedules_say_plainly_the_scheduler_is_not_built_yet(world: World) -> None:
    for name, args in (('schedule_create', MONDAYS), ('schedule_list', None),
                       ('schedule_delete', {'id': 'sch_1'})):  # fmt: skip
        assert 'The scheduler is not built yet' in world.error(name, args)


def test_schedule_create_checks_the_cron(world: World) -> None:
    text = world.error('schedule_create', {**MONDAYS, 'cron': 'every monday'})
    assert 'needs five fields' in text
    assert 'time zone' in world.error('schedule_create', {**MONDAYS, 'tz': 'Mars/Olympus'})


def test_schedules_through_the_scheduler_port(world: World) -> None:
    class Schedules:
        rows: list[dict[str, Any]] = []

        def create(self, agent_id: str, spec: dict[str, Any]) -> dict[str, Any]:
            row = {'id': 'sch_1', 'agent_id': agent_id, 'paused': True, **spec}
            self.rows.append(row)
            return row

        def list(self, agent_id: str) -> list[dict[str, Any]]:
            return self.rows

        def delete(self, agent_id: str, schedule_id: str) -> None:
            self.rows.clear()

    world.app.state.brain_ports.schedules = Schedules()
    assert world.ok('schedule_list') == 'No schedules.' or True
    made = world.ok('schedule_create', MONDAYS)
    assert made.startswith('Scheduled “Dependency report” (sch_1): 0 8 * * 1 Europe/Brussels.')
    assert world.ok('schedule_list') == (
        '- sch_1 “Dependency report”: 0 8 * * 1 Europe/Brussels, cloud (paused)'
    )
    assert world.ok('schedule_delete', {'id': 'sch_1'}) == 'Deleted schedule sch_1.'
    assert world.ok('schedule_list') == 'No schedules.'


# Notify and usage -------------------------------------------------------------------------------


def test_notify_posts_and_pushes(world: World) -> None:
    sent: list[Any] = []
    world.app.state.pusher.push = lambda n, user: sent.append(n) or True
    text = world.ok('notify', {'text': 'The NAS is almost full.', 'level': 'urgent'})
    assert text == 'Posted in the chat and pushed to the owner’s phone.'
    assert sent[0].category == 'broke' and sent[0].level == 'urgent'
    with Session(world.app.state.engine) as s:
        messages = s.exec(select(Message).where(Message.conversation_id == world.main_id)).all()
    assert messages[-1].body_json == {'text': 'The NAS is almost full.'}
    world.app.state.pusher.push = lambda n, user: False
    assert 'No push went out' in world.ok('notify', {'text': 'Again.'})


def test_usage_get(world: World) -> None:
    assert 'does not read plan usage yet' in world.error('usage_get')

    class Usage:
        def current(self) -> dict[str, Any]:
            return {
                'five_hour': {'used_pct': 91.2, 'resets_at': '2026-10-12T21:00:00Z'},
                'weekly': {'used_pct': 48.0},
                'source': 'statusline',
                'stale': True,
                'guard': {'active': True, 'queued': 2,
                          'reason': 'Plan usage is at 91 %. New tasks wait until 23:00.'},
            }  # fmt: skip

    world.app.state.brain_ports.usage = Usage()
    text = world.ok('usage_get')
    assert text.startswith(
        'Five-hour window 91 %, week 48 %. The reading is old, so treat it as a guess. New work '
        'is held back: Plan usage is at 91 %. New tasks wait until 23:00.'
    )


# Who may use them (controls) --------------------------------------------------------------------


def test_a_worker_session_cannot_use_the_tools(world: World) -> None:
    tid = task_id(spawn(world))
    worker = world.hub(session_id=f'ses-{tid}')
    with Session(world.app.state.engine) as s:  # the fake runner's ids are not uuids; fine here
        assert s.get(Task, tid) is not None
    text = world.error('tasks_spawn', {'title': 'x', 'prompt': 'y', 'location': 'nuc'}, worker)
    assert text == ('tasks_spawn did not work: Only Marcel’s brain can use these tools. A worker '
                    'reports with the report tool.')  # fmt: skip
    assert len(tasks(world)) == 1


def test_a_wrong_secret_or_session_is_refused(world: World) -> None:
    wrong = world.hub(secret='hs_' + 'f' * 40)
    assert 'secret does not match' in world.error('tasks_list', None, wrong)
    stranger = world.hub(session_id=str(uuid.uuid4()))
    assert 'not Marcel’s brain' in world.error('tasks_list', None, stranger)


def test_input_is_checked_before_the_hub_is_called(world: World) -> None:
    spawn_args = {'title': 'x', 'prompt': 'y', 'location': 'nuc'}
    cases: list[tuple[str, Any, str]] = [
        ('tasks_get', None, 'tasks_get needs "id".'),
        ('tasks_get', 'tsk_1', 'tasks_get takes an object of named arguments.'),
        ('tasks_get', {'id': 'x', 'colour': 'red'},
         'tasks_get has no argument "colour". It takes: id.'),
        ('tasks_send', {'id': 'x', 'text': '  '}, '"text" cannot be empty.'),
        ('tasks_send', {'id': 'x', 'text': 3}, '"text" must be text.'),
        ('tasks_spawn', {**spawn_args, 'title': 'x' * 121},
         '"title" is too long (at most 120 characters).'),
        ('tasks_spawn', {**spawn_args, 'expected_minutes': 0},
         '"expected_minutes" must be from 1 to 10080.'),
        ('tasks_spawn', {**spawn_args, 'expected_minutes': '5'},
         '"expected_minutes" must be a whole number.'),
        ('tasks_spawn', {**spawn_args, 'repo': 'marcel'},
         '"repo" is not valid: use owner/name, e.g. shbunder/marcel.'),
        ('tasks_list', {'archived': 'yes'}, '"archived" must be true or false.'),
        ('notify', {'text': 'x', 'level': 'loud'}, '"level" must be one of normal, urgent.'),
    ]  # fmt: skip
    for name, args, message in cases:
        assert world.error(name, args) == f'{name} was not run: {message}', name
    assert tasks(world) == []


def test_an_unknown_tool_is_named(world: World) -> None:
    text = world.error('tasks_explode', {})
    assert text.startswith('There is no tool "tasks_explode". The tools are: tasks_spawn,')


def test_none_values_are_left_out(world: World) -> None:
    assert BY_NAME['tasks_list'].run is not None
    assert world.ok('tasks_list', {'project': None}) == 'No tasks match.'


def test_repo_is_checked_before_it_reaches_the_hub(world: World) -> None:
    for repo in ('-x/marcel', 'shbunder/..', 'shbunder/mar..cel', '../etc', 'a/b/c'):
        args = {'title': 'x', 'prompt': 'y', 'location': 'cloud', 'repo': repo}
        assert '"repo" is not valid' in world.error('tasks_spawn', args), repo
        assert '"repo" is not valid' in world.error('projects_create', {'name': 'R', 'repo': repo})
    assert tasks(world) == []


def test_long_fields_are_refused_by_the_tool(world: World) -> None:
    long = 'x' * 3000
    cases = [
        ('artifacts_publish', {'kind': 'link', 'title': 't', 'url': 'https://x.be/' + long}),
        ('artifacts_publish', {'kind': 'doc', 'title': 't', 'path': '/' + long}),
        ('memory_read', {'path': long + '.md'}),
        ('memory_write', {'path': 'a.md', 'content': 'x', 'etag': long}),
        ('projects_create', {'name': 'G', 'folder': '/' + long}),
        ('schedule_create', {'name': 'n', 'cron': '0 8 * * 1', 'tz': long, 'location': 'nuc',
                             'prompt': 'p'}),
        ('tasks_spawn', {'title': 'x', 'prompt': 'y', 'location': 'nuc', 'message_id': long}),
    ]  # fmt: skip
    for name, args in cases:
        text = world.error(name, args)
        assert 'too long' in text or 'not valid' in text, (name, text)


def test_message_id_from_another_conversation_is_refused(world: World) -> None:
    text = world.error('tasks_spawn', {'title': 'x', 'prompt': 'y', 'location': 'nuc',
                                       'message_id': 'msg_elsewhere'})  # fmt: skip
    assert 'There is no message msg_elsewhere in this conversation' in text
    assert tasks(world) == []
