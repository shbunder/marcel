"""The scripted runs a message to Marcel starts.

- The **login run** (flows 1-3): a cloud coding thread in *Marcel dev* that asks to push, then
  opens a PR.
- The **items run** (flows 4-6), for a message that mentions energy: a NUC thread filed in the
  inbox that publishes a living document (v1), asks to send a switch request, and after your yes
  updates the document to v2. Move it to *Energy contracts* while it waits; its board follows.

A message sent from a project room (`project_id`, B-32) files the thread in that project instead.
Every milestone, approval card and item message of a run carries `reply_to_message_id`: the message
that started it (B-41), so the app can quote it once you have written something since.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from . import contract
from .hub import AGENT_ID, INBOX, MAIN_CONVERSATION, MARCEL_DEV, SCENARIO_REPO, Hub, now, reply_to

SCENARIO_TITLE = 'Fix the flaky login test'
ITEMS_TITLE = 'Compare the energy offers'
ITEM_TITLE = 'Switching to Luminus'


@dataclass
class Ask:
    tool: str
    summary: str
    detail: str
    waiting_for: str
    approved: str
    denied: str


async def run_for(hub: Hub, message: dict[str, Any]) -> None:
    if 'energy' in message['text'].lower():
        await items_run(hub, message)
    else:
        await login_run(hub, message)


async def _start(
    hub: Hub, message: dict[str, Any], title: str, location: str, default_project: str
) -> dict[str, Any]:
    """thinking -> task.created (filed in a project) -> "started" milestone."""
    hub.set_agent_state({'kind': 'thinking'})
    await hub.pause()
    task: dict[str, Any] = {
        'id': hub.next_id('tsk'),
        'agent_id': AGENT_ID,
        'project_id': message.get('project_id', default_project),
        'title': title,
        'location': location,
        'state': 'starting',
        'model': 'sonnet',
        'origin': 'marcel',
        'parent_message_id': message['id'],
        'artifact_count': 0,
        'created_at': now(),
        'updated_at': now(),
    }
    if location == 'cloud':
        task['repo'] = SCENARIO_REPO
    hub.create_task(task)
    _milestone(hub, task, 'started')
    await hub.pause()
    hub.change_state(task, 'working', 'The runner reports the session is working')
    hub.set_agent_state({'kind': 'working', 'active_tasks': 1})
    await hub.pause()
    return task


def _milestone(hub: Hub, task: dict[str, Any], event: str, **extra: Any) -> None:
    hub.add_message(
        {
            'id': hub.next_id('msg'),
            'conversation_id': MAIN_CONVERSATION,
            'role': 'agent',
            'kind': 'milestone',
            'milestone': {
                'task_id': task['id'],
                'task_title': task['title'],
                'event': event,
                'location': task['location'],
                **extra,
            },
            'task_id': task['id'],
            **reply_to(task),
            'created_at': now(),
        }
    )


async def _ask(hub: Hub, task: dict[str, Any], ask: Ask) -> bool:
    """An approval card, then wait for the answer with no timeout. True when approved."""
    approval_id = hub.next_id('apr')
    approval = {
        'id': approval_id,
        'task_id': task['id'],
        'tool': ask.tool,
        'summary': ask.summary,
        'detail': ask.detail,
        'state': 'open',
        'created_at': now(),
    }
    hub.approvals[approval_id] = approval
    answered = hub.answers[approval_id] = asyncio.Event()
    hub.add_task_event(
        task['id'],
        'permission',
        {'approval_id': approval_id, 'tool': ask.tool, 'summary': ask.summary},
    )
    hub.emit('approval.created', approval)
    hub.add_message(
        {
            'id': hub.next_id('msg'),
            'conversation_id': MAIN_CONVERSATION,
            'role': 'agent',
            'kind': 'approval',
            'approval_id': approval_id,
            'task_id': task['id'],
            **reply_to(task),
            'created_at': now(),
        }
    )
    hub.change_state(
        task,
        'needs_you',
        f'Permission request: {ask.tool}',
        waiting_for=ask.waiting_for,
        approval_ids=[approval_id],
    )
    hub.set_agent_state({'kind': 'needs_you'})

    await answered.wait()
    task.pop('approval_ids', None)
    if approval['state'] != 'approved':
        hub.change_state(task, 'stopped', ask.denied)
        _milestone(hub, task, 'stopped', summary='Stopped before going further, as you asked.')
        await _settle(hub)
        return False
    hub.change_state(task, 'working', ask.approved)
    hub.set_agent_state({'kind': 'working', 'active_tasks': 1})
    await hub.pause()
    return True


async def _finish(hub: Hub, task: dict[str, Any], summary: str, artifact_ids: list[str]) -> None:
    hub.change_state(
        task,
        'done',
        'The worker reported it is finished',
        summary=summary,
        artifact_count=len(artifact_ids),
    )
    _milestone(hub, task, 'done', summary=summary, artifact_ids=artifact_ids)
    await _settle(hub)


async def _settle(hub: Hub) -> None:
    """`done` shows for a moment (5 steps), then the avatar goes back to idle."""
    hub.set_agent_state({'kind': 'done'})
    await hub.pause(5)
    hub.set_agent_state({'kind': 'idle'})


# ---- the login run (flows 1-3) -------------------------------------------------------------


async def login_run(hub: Hub, message: dict[str, Any]) -> None:
    task = await _start(hub, message, SCENARIO_TITLE, 'cloud', MARCEL_DEV)
    steps: list[tuple[str, dict[str, Any]]] = [
        ('text', {'role': 'assistant', 'text': 'Looking at the login test and its fixtures.'}),
        (
            'tool_call',
            {
                'tool_use_id': 'toolu_mock_pytest',
                'tool': 'Bash',
                'summary': 'pytest tests/test_login.py -x',
            },
        ),
        (
            'diff',
            {
                'tool_use_id': 'toolu_mock_edit',
                'file_path': 'tests/test_login.py',
                'hunks': [
                    {
                        'old_start': 12,
                        'old_lines': 1,
                        'new_start': 12,
                        'new_lines': 2,
                        'lines': ['+    page.wait_for_cookie()', '     assert page.logged_in'],
                    }
                ],
            },
        ),
    ]
    await _events(hub, task, steps)
    ask = Ask(
        tool='Bash',
        summary='Run `git push origin fix/login-test`',
        detail='git push origin fix/login-test',
        waiting_for='Permission to push the fix branch.',
        approved='You approved the push',
        denied='You denied the push',
    )
    if not await _ask(hub, task, ask):
        return
    hub.add_task_event(task['id'], 'progress', {'text': 'Pushed the branch and opened a PR.'})
    await hub.pause()
    pr = contract.example('Artifact')
    pr['id'] = hub.next_id('art')
    hub.publish(pr, task)
    await _finish(
        hub, task, 'The test raced the session cookie. Added a wait on the cookie.', [pr['id']]
    )


# ---- the items run (flows 4-6) -------------------------------------------------------------


async def items_run(hub: Hub, message: dict[str, Any]) -> None:
    task = await _start(hub, message, ITEMS_TITLE, 'nuc', INBOX)
    await _events(
        hub,
        task,
        [
            ('text', {'role': 'assistant', 'text': 'Collecting the offers for our address.'}),
            (
                'tool_call',
                {
                    'tool_use_id': 'toolu_mock_fetch',
                    'tool': 'WebFetch',
                    'summary': 'Fetch the Luminus and Engie offers',
                },
            ),
        ],
    )
    doc = contract.example('ArtifactPage')['items'][0]  # a Markdown doc
    for key in ('task_id', 'size_bytes'):
        doc.pop(key, None)
    doc.update(id=hub.next_id('art'), title=ITEM_TITLE)
    hub.publish(doc, task, 'Luminus is cheapest: €412 a year less than now.')
    await hub.pause()
    ask = Ask(
        tool='Email',
        summary='Send the switch request to Luminus',
        detail='To: switch@luminus.be — switch gas and electricity from 1 November.',
        waiting_for='Permission to send the switch request.',
        approved='You approved the switch request',
        denied='You said not now',
    )
    if not await _ask(hub, task, ask):
        return
    hub.add_task_event(task['id'], 'progress', {'text': 'Sent the switch request to Luminus.'})
    await hub.pause()
    hub.republish(
        doc,
        task,
        'Switch requested; Luminus from 1 November.',
        'Switch requested: Luminus from 1 November, €412 a year less than now.',
    )
    await hub.pause()
    await _finish(
        hub,
        task,
        'Asked Luminus to switch us from 1 November; the comparison is updated.',
        [doc['id']],
    )


async def _events(hub: Hub, task: dict[str, Any], steps: list[tuple[str, dict[str, Any]]]) -> None:
    for type_, data in steps:
        hub.add_task_event(task['id'], type_, data)
        await hub.pause()
