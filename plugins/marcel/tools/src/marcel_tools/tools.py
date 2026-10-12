"""Marcel's brain tools: what each one is for (written for the model), its input, and its hub call.

Every tool checks its input against its own `inputSchema` before anything reaches the hub, and
answers a problem in one plain sentence that says what to change. The hub checks again.
"""

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from marcel_tools.hub import Hub, HubError, segment

Args = dict[str, Any]

ID = {'type': 'string', 'pattern': '^[A-Za-z0-9_-]{1,80}$'}
PROJECT = {
    'type': 'string',
    'pattern': '^[A-Za-z0-9_-]{1,80}$',
    'description': 'The project’s slug (e.g. "garden") or id. Leave out for the Inbox.',
}
TASK_STATES = ['queued', 'starting', 'working', 'needs_you', 'done', 'failed', 'stopped', 'silent',
               'unknown']  # fmt: skip
ARTIFACT_KINDS = ['pr', 'branch', 'doc', 'file', 'dashboard', 'link']
LOCATION_HINT = (
    'location must be "nuc" or "cloud", and "auto" is not accepted: decide yourself. Use "nuc" '
    'for anything that needs local files, Docker, the home network or the NUC’s tools, and "cloud" '
    'for work on a GitHub repository that needs nothing local.'
)


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    schema: dict[str, Any]
    run: Callable[[Hub, Args], str]
    # What to say when an argument does not fit, by name (kept out of the schema the model sees).
    hints: dict[str, str] = field(default_factory=dict)

    def listing(self) -> dict[str, Any]:
        return {'name': self.name, 'description': self.description, 'inputSchema': self.schema}


def obj(required: list[str] | None = None, **properties: Any) -> dict[str, Any]:
    return {
        'type': 'object',
        'properties': properties,
        'required': required or [],
        'additionalProperties': False,
    }


def text(description: str, max_length: int = 20000, min_length: int = 1) -> dict[str, Any]:
    return {'type': 'string', 'minLength': min_length, 'maxLength': max_length,
            'description': description}  # fmt: skip


# Input checks -------------------------------------------------------------------------------


class BadInput(Exception):
    """The arguments do not fit the tool. The message says what to change."""


def check(tool: Tool, args: Any) -> Args:
    """`args` checked against the tool's schema: required, types, enums, lengths, patterns."""
    if not isinstance(args, dict):
        raise BadInput(f'{tool.name} takes an object of named arguments.')
    schema = tool.schema
    props: dict[str, Any] = schema['properties']
    for key in args:
        if key not in props:
            known = ', '.join(props) or 'no arguments'
            raise BadInput(f'{tool.name} has no argument "{key}". It takes: {known}.')
    for key in schema['required']:
        if key not in args or args[key] is None:
            raise BadInput(f'{tool.name} needs "{key}".')
    checked: Args = {}
    for key, value in args.items():
        if value is None:
            continue
        rule = props[key]
        kind = rule.get('type')
        hint = tool.hints.get(key)
        if 'enum' in rule and value not in rule['enum']:
            raise BadInput(hint or f'"{key}" must be one of {", ".join(map(str, rule["enum"]))}.')
        if kind == 'string':
            if not isinstance(value, str):
                raise BadInput(f'"{key}" must be text.')
            if rule.get('minLength', 0) and not value.strip():
                raise BadInput(f'"{key}" cannot be empty.')
            if len(value) > rule.get('maxLength', 1_000_000):
                raise BadInput(f'"{key}" is too long (at most {rule["maxLength"]} characters).')
            if 'pattern' in rule and not re.fullmatch(rule['pattern'], value):
                raise BadInput(f'"{key}" is not valid: {hint or "letters, digits, - and _ only"}.')
        elif kind == 'integer':
            if isinstance(value, bool) or not isinstance(value, int):
                raise BadInput(f'"{key}" must be a whole number.')
            if not rule['minimum'] <= value <= rule['maximum']:
                raise BadInput(f'"{key}" must be from {rule["minimum"]} to {rule["maximum"]}.')
        elif kind == 'boolean' and not isinstance(value, bool):
            raise BadInput(f'"{key}" must be true or false.')
        checked[key] = value
    return checked


# Answers ------------------------------------------------------------------------------------


def answer(lead: str, data: Any = None) -> str:
    """The outcome first, then the data as JSON for the details."""
    if data is None:
        return lead
    return f'{lead}\n\n{json.dumps(data, ensure_ascii=False, indent=1)}'


def _where(task: dict[str, Any]) -> str:
    return 'on the NUC' if task.get('location') == 'nuc' else 'in the cloud'


def _task_line(task: dict[str, Any]) -> str:
    line = f'{task["id"]} “{task["title"]}” — {task["state"]} {_where(task)}'
    if task.get('waiting_for'):
        line += f'; waiting for: {task["waiting_for"]}'
    return line


# Tasks --------------------------------------------------------------------------------------


def tasks_spawn(hub: Hub, args: Args) -> str:
    if args.get('location') not in ('nuc', 'cloud'):
        raise BadInput(LOCATION_HINT)
    task = hub.call('POST', '/tasks', args)
    if task['state'] == 'queued':
        return answer(f'Queued “{task["title"]}” ({task["id"]}): the worker slots are full. It '
                      'starts when one frees up. Tell the owner.', task)  # fmt: skip
    if task['state'] == 'failed':
        return answer(f'“{task["title"]}” ({task["id"]}) could not start. Its events say why; '
                      'tell the owner.', task)  # fmt: skip
    return answer(f'Started “{task["title"]}” ({task["id"]}) {_where(task)}.', task)


def tasks_list(hub: Hub, args: Args) -> str:
    params: dict[str, str] = {}
    if 'state' in args:
        params['state'] = ','.join(args['state'].split())
    if 'project' in args:
        params['project'] = args['project']
    if args.get('archived'):
        params['archived'] = 'true'
    page = hub.call('GET', '/tasks', params=params)
    items = page['items']
    if not items:
        return 'No tasks match.'
    lines = '\n'.join(f'- {_task_line(t)}' for t in items)
    more = '\n(More exist; narrow by state or project.)' if page.get('more') else ''
    return f'{len(items)} task{"s" if len(items) != 1 else ""}:\n{lines}{more}'


def tasks_get(hub: Hub, args: Args) -> str:
    task = hub.call('GET', f'/tasks/{segment(args["id"])}')
    return answer(_task_line(task), task)


def tasks_send(hub: Hub, args: Args) -> str:
    task = hub.call('POST', f'/tasks/{segment(args["id"])}/messages', {'text': args['text']})
    return answer(f'Sent to “{task["title"]}”. It is {task["state"]} now.')


def tasks_stop(hub: Hub, args: Args) -> str:
    task = hub.call('POST', f'/tasks/{segment(args["id"])}/stop')
    return answer(f'“{task["title"]}” is {task["state"]}.')


def tasks_move(hub: Hub, args: Args) -> str:
    task = hub.call('POST', f'/tasks/{segment(args["id"])}/move', {'project': args['project']})
    return answer(f'Moved “{task["title"]}” to project {task["project_id"]}.')


# Projects -----------------------------------------------------------------------------------


def projects_list(hub: Hub, args: Args) -> str:
    params = {'archived': 'true'} if args.get('archived') else None
    projects = hub.call('GET', '/projects', params=params)
    if not projects:
        return 'No projects.'
    lines = []
    for p in projects:
        c = p['counts']
        line = f'- {p["slug"]} ({p["id"]}): {p["name"]}'
        if p.get('goal'):
            line += f' — {p["goal"]}'
        line += (
            f' [working {c["working"]}, needs you {c["needs_you"]}, done this week {c["done_7d"]}]'
        )
        lines.append(line)
    return '\n'.join(lines)


def projects_create(hub: Hub, args: Args) -> str:
    project = hub.call('POST', '/projects', args)
    return answer(
        f'Made project “{project["name"]}” ({project["slug"]}). Its notes are in '
        f'memory/projects/{project["slug"]}.md.',
        project,
    )


def projects_update(hub: Hub, args: Args) -> str:
    changes = {k: v for k, v in args.items() if k != 'project'}
    if not changes:
        raise BadInput('projects_update needs at least one field to change.')
    project = hub.call('PATCH', f'/projects/{segment(args["project"])}', changes)
    return answer(f'Updated “{project["name"]}”: {", ".join(changes)}.', project)


def board_note(hub: Hub, args: Args) -> str:
    hub.call('POST', f'/projects/{segment(args["project"])}/notes', {'text': args['text']})
    return 'The note is on the board.'


# Artifacts and memory -----------------------------------------------------------------------


def artifacts_publish(hub: Hub, args: Args) -> str:
    kind = args['kind']
    if kind in ('pr', 'branch', 'link') and 'url' not in args:
        raise BadInput(f'a {kind} needs "url".')
    if kind in ('doc', 'file', 'dashboard') and 'path' not in args:
        raise BadInput(f'a {kind} needs "path", an absolute path on the NUC.')
    art = hub.call('POST', '/artifacts', args)
    return answer(f'Saved “{art["title"]}” ({art["id"]}, version {art["current_version"]}).', art)


def memory_read(hub: Hub, args: Args) -> str:
    if 'path' not in args:
        files = hub.call('GET', '/memory')['files']
        if not files:
            return 'The memory is empty.'
        return 'Memory files:\n' + '\n'.join(f'- {f["path"]} ({f["size_bytes"]} bytes)'
                                             for f in files)  # fmt: skip
    found = hub.call('GET', '/memory', params={'path': args['path']})
    return f'{found["path"]} (etag {found["etag"]}):\n\n{found["content"]}'


def memory_write(hub: Hub, args: Args) -> str:
    saved = hub.call('PUT', '/memory', args)
    return f'Saved {saved["path"]}. Its etag is now {saved["etag"]}.'


# Schedules ----------------------------------------------------------------------------------


def schedule_create(hub: Hub, args: Args) -> str:
    if len(args['cron'].split()) != 5:
        raise BadInput(
            f'cron "{args["cron"]}" needs five fields: minute hour day month weekday, '
            'e.g. "0 8 * * 1" for Mondays at 08:00.'
        )
    made = hub.call('POST', '/schedules', args)
    return answer(f'Scheduled “{made["name"]}” ({made["id"]}): {made["cron"]} {made["tz"]}.', made)


def schedule_list(hub: Hub, args: Args) -> str:
    rows = hub.call('GET', '/schedules')
    if not rows:
        return 'No schedules.'
    return '\n'.join(f'- {r["id"]} “{r["name"]}”: {r["cron"]} {r["tz"]}, {r["location"]}'
                     f'{" (paused)" if r.get("paused") else ""}' for r in rows)  # fmt: skip


def schedule_delete(hub: Hub, args: Args) -> str:
    hub.call('DELETE', f'/schedules/{segment(args["id"])}')
    return f'Deleted schedule {args["id"]}.'


# Other --------------------------------------------------------------------------------------


def notify(hub: Hub, args: Args) -> str:
    sent = hub.call('POST', '/notify', args)
    if sent['pushed']:
        return 'Posted in the chat and pushed to the owner’s phone.'
    return 'Posted in the chat. No push went out (a repeat, or no push is set up).'


def usage_get(hub: Hub, args: Args) -> str:
    usage = hub.call('GET', '/usage')
    five, week = usage['five_hour'], usage['weekly']
    lead = f'Five-hour window {five["used_pct"]:.0f} %, week {week["used_pct"]:.0f} %.'
    if usage.get('stale'):
        lead += ' The reading is old, so treat it as a guess.'
    guard = usage.get('guard') or {}
    if guard.get('active'):
        lead += f' New work is held back: {guard.get("reason", "")}'
    return answer(lead, usage)


# The list -----------------------------------------------------------------------------------

TOOLS: list[Tool] = [
    Tool(
        'tasks_spawn',
        'Start a thread: a worker session that does one piece of work. Use it for anything '
        'substantial instead of doing it yourself; answer quick questions directly. You choose '
        'where it runs: "nuc" for local files, Docker, the home network or the NUC’s tools; '
        '"cloud" for GitHub work that needs nothing local (then give "repo"). Before starting '
        'one, check tasks_list for a live thread the work belongs to and use tasks_send instead. '
        'Tell the owner in one line what you started and where.',
        obj(
            ['title', 'prompt', 'location'],
            title=text('A short name the owner will recognise.', 120),
            prompt=text('Everything the worker needs: the goal, context, and what done means.'),
            location={'type': 'string', 'enum': ['nuc', 'cloud']},
            project=PROJECT,
            repo={
                'type': 'string',
                'pattern': '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$',
                'description': 'owner/name of the GitHub repo.',
            },
            model={
                'type': 'string',
                'pattern': '^[A-Za-z0-9._\\[\\]][A-Za-z0-9._\\[\\]-]{0,79}$',
                'description': 'Leave out for the default (sonnet). "opus" for hard work.',
            },
            expected_minutes={
                'type': 'integer',
                'minimum': 1,
                'maximum': 10080,
                'description': 'How long it should take; silence after that is reported.',
            },
            message_id={**ID, 'description': 'The owner’s message that asked for this.'},
        ),  # fmt: skip
        tasks_spawn,
        {
            'location': LOCATION_HINT,
            'repo': 'use owner/name',
            'model': 'use a name like sonnet or opus',
        },  # fmt: skip
    ),
    Tool(
        'tasks_list',
        'List threads and their state. Use it to answer "what is going on?" and before starting '
        'a thread, to find one a follow-up belongs to. Do not read transcripts for this.',
        obj(
            state={
                'type': 'string',
                'description': 'Only these states, comma-separated: '
                + ', '.join(TASK_STATES)
                + '.',
            },
            project=PROJECT,
            archived={'type': 'boolean', 'description': 'True for archived threads only.'},
        ),  # fmt: skip
        tasks_list,
    ),
    Tool(
        'tasks_get',
        'Look at one thread: its state, what it waits for, its summary and its latest events. '
        'Use it when the owner asks about a specific thread.',
        obj(['id'], id=ID),
        tasks_get,
    ),
    Tool(
        'tasks_send',
        'Send a message into a running or finished thread: a follow-up, a correction or an '
        'answer to its question. A finished thread picks it up as a follow-up. Do not use it to '
        'talk to the owner; use reply.',
        obj(['id', 'text'], id=ID, text=text('What to tell the worker.')),
        tasks_send,
    ),
    Tool(
        'tasks_stop',
        'Stop a thread for good. Only when the owner asks, or the work is clearly no longer '
        'wanted. Stopped threads cannot be resumed.',
        obj(['id'], id=ID),
        tasks_stop,
    ),
    Tool(
        'tasks_move',
        'File a thread under another project, with its artifacts. Use it when a thread landed '
        'in the wrong project or the Inbox.',
        obj(['id', 'project'], id=ID, project={**PROJECT, 'description': 'Slug or id.'}),
        tasks_move,
    ),
    Tool(
        'projects_list',
        'List the owner’s projects with their slugs and how many threads are working, need '
        'the owner, or finished this week. Use it to pick a project for a new thread.',
        obj(archived={'type': 'boolean', 'description': 'True for archived projects only.'}),
        projects_list,
    ),
    Tool(
        'projects_create',
        'Make a project for a goal that will take several threads (e.g. "Energy contracts"). '
        'Not for one-off work: that goes in the Inbox.',
        obj(
            ['name'],
            name=text('The project’s name.', 60),
            slug={
                'type': 'string',
                'pattern': '^[a-z0-9][a-z0-9-]{0,39}$',
                'description': 'Leave out to make one from the name.',
            },
            goal=text('One line on what the project is for.', 300),
            repo={'type': 'string', 'description': 'owner/name when it works on one repo.'},
            folder=text('A folder on the NUC its threads work in.', 500),
            instructions=text('Standing instructions every thread in it starts with.'),
            claude_project_url={'type': 'string', 'description': 'A linked Claude Project.'},
        ),  # fmt: skip
        projects_create,
        {'slug': 'lowercase letters, digits and dashes, starting with a letter or digit'},
    ),
    Tool(
        'projects_update',
        'Change a project’s name, goal, repo, folder or standing instructions. Use it when the '
        'owner says how work in a project should always be done.',
        obj(
            ['project'],
            project={**PROJECT, 'description': 'Slug or id.'},
            name=text('The new name.', 60),
            goal=text('One line on what the project is for.', 300),
            repo={'type': 'string', 'description': 'owner/name.'},
            folder=text('A folder on the NUC.', 500),
            instructions=text('Standing instructions every thread in it starts with.'),
            claude_project_url={'type': 'string', 'description': 'A linked Claude Project.'},
        ),
        projects_update,
    ),
    Tool(
        'board_note',
        'Add one line to a project’s board saying what the latest results mean ("Luminus is '
        'still the cheapest"). The board lists threads by itself; do not repeat them.',
        obj(
            ['project', 'text'],
            project={**PROJECT, 'description': 'Slug or id.'},
            text=text('One short line.', 500),
        ),  # fmt: skip
        board_note,
    ),
    Tool(
        'artifacts_publish',
        'Save something worth keeping in the owner’s Items: a link, PR or branch (give "url"), or '
        'a Markdown/HTML doc, file or dashboard JSON on the NUC (give "path"). With "artifact_id" '
        'it becomes a new version of that artifact instead of a new one. Workers save their own '
        'results; use this for what you made yourself.',
        obj(
            ['kind', 'title'],
            kind={'type': 'string', 'enum': ARTIFACT_KINDS},
            title=text('What it is, in a few words.', 300),
            url={'type': 'string', 'description': 'For pr, branch and link.'},
            path={'type': 'string', 'description': 'Absolute path, for doc, file and dashboard.'},
            project=PROJECT,
            artifact_id={**ID, 'description': 'Make this a new version of that artifact.'},
            summary=text('What changed in this version, in one line.', 200),
        ),
        artifacts_publish,
    ),
    Tool(
        'memory_read',
        'Read a memory file (MEMORY.md, a dated note memory/YYYY-MM-DD.md, or a project’s '
        'notes projects/<slug>.md). Without "path" it lists the files. Note the etag: '
        'memory_write needs it to change the file.',
        obj(path={'type': 'string', 'description': 'Relative path ending in .md.'}),
        memory_read,
    ),
    Tool(
        'memory_write',
        'Save a memory file; the hub commits it to git. Give the etag from memory_read to change '
        'an existing file; leave it out only to create a new one. Write the whole file. If it '
        'changed since you read it, read it again and merge.',
        obj(
            ['path', 'content'],
            path={'type': 'string', 'description': 'Relative path ending in .md.'},
            content=text('The whole file, in Markdown.', 1_000_000, 0),
            etag={'type': 'string', 'description': 'From memory_read.'},
        ),
        memory_write,
    ),
    Tool(
        'schedule_create',
        'Set up recurring work when the owner asks ("every Monday at 8, …"). "brain" runs it '
        'here with you; "nuc" or "cloud" starts a thread there at each fire.',
        obj(
            ['name', 'cron', 'tz', 'location', 'prompt'],
            name=text('A short name the owner will recognise.', 120),
            cron={'type': 'string', 'description': 'Five-field cron, e.g. "0 8 * * 1".'},
            tz={'type': 'string', 'description': 'IANA time zone, e.g. Europe/Brussels.'},
            location={'type': 'string', 'enum': ['brain', 'nuc', 'cloud']},
            prompt=text('What to do at each fire.'),
            deadline_minutes={
                'type': 'integer',
                'minimum': 1,
                'maximum': 10080,
                'description': 'Say so if no run started this long after the fire time.',
            },
        ),  # fmt: skip
        schedule_create,
    ),
    Tool(
        'schedule_list',
        'List every schedule, the built-in digests included. Use it before making a new one.',
        obj(),
        schedule_list,
    ),
    Tool(
        'schedule_delete',
        'Delete a schedule when the owner asks. Built-in digests can only be paused, in the app.',
        obj(['id'], id=ID),
        schedule_delete,
    ),
    Tool(
        'notify',
        'Interrupt the owner: posts in the chat and pushes to their phone. Only when they would '
        'want to know now — something broke ("urgent") or news that can wait ("normal"). For a '
        'normal answer use reply; task milestones are pushed already.',
        obj(
            ['text'],
            text=text('One or two short sentences, the outcome first.', 2000),
            level={'type': 'string', 'enum': ['normal', 'urgent']},
        ),  # fmt: skip
        notify,
    ),
    Tool(
        'usage_get',
        'Read plan usage: how much of the five-hour and weekly windows is used, and whether new '
        'work is held back. Use it when the owner asks, or before starting a lot of work.',
        obj(),
        usage_get,
    ),
]

BY_NAME = {tool.name: tool for tool in TOOLS}


def call(hub: Hub, name: str, args: Any) -> tuple[str, bool]:
    """Run tool `name`. Returns the text for the session and whether it is an error."""
    tool = BY_NAME.get(name)
    if tool is None:
        return f'There is no tool "{name}". The tools are: {", ".join(BY_NAME)}.', True
    try:
        return tool.run(hub, check(tool, args if args is not None else {})), False
    except BadInput as exc:
        return f'{name} was not run: {exc}', True
    except HubError as exc:
        return f'{name} did not work: {exc}', True
