"""A project's board (B-35): a dashboard the hub rebuilds from what it holds, with no brain turn."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .hub import Hub

IN_FLIGHT = ('starting', 'working', 'needs_you')
RECENT = 5


def _section(title: str, empty: str, items: list[dict[str, str]]) -> dict[str, Any]:
    widgets = [{'type': 'list', 'items': items}] if items else []
    return {'type': 'section', 'title': title, 'empty_text': empty, 'widgets': widgets}


def build(hub: Hub, project: dict[str, Any]) -> dict[str, Any]:
    """The board's content, matching `contracts/dashboard.schema.json`. Nothing time-dependent goes
    in, so the same state builds the same board and a new version means something changed."""
    pid = project['id']
    tasks = [t for t in hub.tasks.values() if t['project_id'] == pid]
    titles = {t['id']: t['title'] for t in tasks}
    waiting = [
        {'text': a['summary'], 'detail': titles[a['task_id']]}
        for a in hub.approvals.values()
        if a['state'] == 'open' and a['task_id'] in titles
    ]
    flying = [
        {'text': t['title'], 'detail': f'{t["location"]} · {t["model"]}'}
        for t in tasks
        if t['state'] in IN_FLIGHT
    ]
    items = sorted(
        (a for a in hub.artifacts.values() if a['project_id'] == pid and a['kind'] != 'board'),
        key=lambda a: a['updated_at'],
        reverse=True,
    )[:RECENT]
    results = [
        {
            'text': f'{a["title"]} · v{a["current_version"]}',
            'detail': a.get('updated_by', a['kind']),
        }
        for a in items
    ]
    board: dict[str, Any] = {
        'version': 1,
        'title': project['name'],
        'widgets': [
            _section('Waiting on you', 'Nothing waiting on you.', waiting),
            _section('In flight', 'No threads running.', flying),
            _section('Recent results', 'No items yet.', results),
            _section('Upcoming', 'Nothing scheduled.', []),
        ],
    }
    if project.get('goal'):
        board['subtitle'] = project['goal'][:160]
    return board


def summary(board: dict[str, Any]) -> str:
    """One line on a board version, for its `ArtifactVersion.summary`."""
    counts = {w['title']: sum(len(x['items']) for x in w['widgets']) for w in board['widgets']}
    return (
        f'{counts["Waiting on you"]} waiting on you, {counts["In flight"]} in flight, '
        f'{counts["Recent results"]} recent results.'
    )
