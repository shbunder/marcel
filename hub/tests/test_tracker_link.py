"""A coding project's tracker (S-20.2, B-46): a worker reports a `link` with `role: tracker`, and
the hub sets its project's `tracker_url`, replacing an earlier one, and tells the app."""

import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlmodel import Session, select
from test_channel_fakes import ChannelHub, channel_hub

from marcel_hub.models import AppEvent, Artifact, ArtifactKind, Project

TRACKER = 'https://claude.ai/artifact/H33nUtv4MC2qfjTiZ3e87M'
LATER = 'https://claude.ai/artifact/Z9Q8newTrackerAbcdefghij'


@pytest.fixture
def hub(tmp_path: Path) -> Iterator[ChannelHub]:
    with channel_hub(tmp_path) as hub:
        yield hub


def garden(hub: ChannelHub) -> str:
    res = hub.client.post('/api/projects', json={'agent_id': hub.agent_id, 'name': 'Garden planner',
                                                 'repo': 'shbunder/garden-planner'})  # fmt: skip
    assert res.status_code == 201, res.text
    return res.json()['id']


def report_trackers(hub: ChannelHub, sid: str, *urls: str) -> list[dict[str, Any]]:
    """One session reporting each url as its project's tracker, in turn. Call ids are unique
    across sessions, as the channel server makes them."""
    with hub.connect() as ch:
        ch.hello(sid)
        return [
            ch.call('report', call_id=f'c_{uuid.uuid4().hex[:12]}', kind='artifact', artifact={
                'kind': 'link', 'title': 'Garden planner tracker', 'url': url, 'role': 'tracker'})
            for url in urls
        ]  # fmt: skip


def tracker_of(hub: ChannelHub, project_id: str) -> str | None:
    with Session(hub.engine) as s:
        project = s.get(Project, project_id)
        assert project is not None
        return project.tracker_url


def test_a_tracker_link_sets_the_projects_tracker(hub: ChannelHub) -> None:
    project_id = garden(hub)
    _task_id, sid = hub.task(title='Kickoff garden-planner', project_id=project_id, kickoff=True)
    [ack] = report_trackers(hub, sid, TRACKER)
    assert 'error' not in ack, ack
    assert tracker_of(hub, project_id) == TRACKER
    shown = hub.client.get(f'/api/projects/{project_id}').json()
    assert shown['tracker_url'] == TRACKER
    with Session(hub.engine) as s:  # the link is kept as an item too, without the hub's mark
        links = s.exec(select(Artifact).where(Artifact.kind == ArtifactKind.link)).all()
        assert [a.url for a in links] == [TRACKER]
        updates = [e.data_json for e in s.exec(select(AppEvent)) if e.type == 'project.updated']
    assert any(u.get('tracker_url') == TRACKER for u in updates)  # the app hears it


def test_a_second_tracker_replaces_the_first(hub: ChannelHub) -> None:
    project_id = garden(hub)
    _task_id, sid = hub.task(title='Kickoff garden-planner', project_id=project_id, kickoff=True)
    report_trackers(hub, sid, TRACKER, LATER)
    assert tracker_of(hub, project_id) == LATER


def test_an_ordinary_link_sets_no_tracker(hub: ChannelHub) -> None:
    project_id = garden(hub)
    _task_id, sid = hub.task(title='Read the docs', project_id=project_id)
    with hub.connect() as ch:
        ch.hello(sid)
        docs = {'kind': 'link', 'title': 'Docs', 'url': 'https://example.com'}
        ch.call('report', kind='artifact', artifact=docs)
    assert tracker_of(hub, project_id) is None


@pytest.mark.parametrize(
    ('artifact', 'why'),
    [
        ({'kind': 'link', 'title': 'T', 'url': 'http://claude.ai/artifact/x', 'role': 'tracker'},
         'a tracker is a claude.ai artifact link'),
        ({'kind': 'link', 'title': 'T', 'url': 'https://github.com/a/b/issues', 'role': 'tracker'},
         'a tracker is a claude.ai artifact link'),
        ({'kind': 'link', 'title': 'T', 'url': 'https://claude.ai.evil.example/artifact/x',
          'role': 'tracker'}, 'a tracker is a claude.ai artifact link'),
        ({'kind': 'link', 'title': 'T', 'url': 'https://claude.ai/artifact/x/../../evil',
          'role': 'tracker'}, 'a tracker is a claude.ai artifact link'),
        ({'kind': 'pr', 'title': 'T', 'url': 'https://claude.ai/artifact/x', 'role': 'tracker'},
         'a tracker is a claude.ai artifact link'),
        ({'kind': 'link', 'title': 'T', 'url': 'https://claude.ai/x', 'role': 'board'}, 'role'),
    ],
)  # fmt: skip
def test_a_bad_tracker_is_refused_and_sets_nothing(
    hub: ChannelHub, artifact: dict[str, Any], why: str
) -> None:
    project_id = garden(hub)
    _task_id, sid = hub.task(title='Kickoff garden-planner', project_id=project_id, kickoff=True)
    with hub.connect() as ch:
        ch.hello(sid)
        ack = ch.call('report', kind='artifact', artifact=artifact)
    assert ack['error']['code'] == 'bad_report' and why in ack['error']['message']
    assert tracker_of(hub, project_id) is None


def test_an_unfiled_tasks_tracker_goes_to_its_inbox(hub: ChannelHub) -> None:
    _task_id, sid = hub.task(title='Kickoff garden-planner', kickoff=True)
    report_trackers(hub, sid, TRACKER)
    with Session(hub.engine) as s:
        inbox = s.exec(select(Project).where(Project.agent_id == hub.agent_id,
                                             Project.slug == 'inbox')).one()  # fmt: skip
    assert inbox.tracker_url == TRACKER


def test_a_quest_in_a_project_with_a_tracker_is_told_where_its_card_is(hub: ChannelHub) -> None:
    project_id = garden(hub)
    _kickoff, sid = hub.task(title='Kickoff garden-planner', project_id=project_id, kickoff=True)
    report_trackers(hub, sid, TRACKER)
    task_id, _sid = hub.task(title='Fix flaky login test', project_id=project_id)
    briefing = hub.app.state.projects.briefing(hub.registry.get(task_id))
    assert f'Tracker: {TRACKER} (your card: cards/{task_id})' in briefing


def test_an_inbox_with_a_tracker_still_names_it(hub: ChannelHub) -> None:
    """An inbox with no notes or instructions gives an empty briefing, except for its tracker."""
    _kickoff, sid = hub.task(title='Kickoff garden-planner', kickoff=True)  # unfiled: the inbox
    report_trackers(hub, sid, TRACKER)
    task_id, _sid = hub.task(title='Tidy the README')
    briefing = hub.app.state.projects.briefing(hub.registry.get(task_id))
    assert f'Tracker: {TRACKER} (your card: cards/{task_id})' in briefing


# S-20.3: only the kickoff quest, and only a claude.ai artifact ------------------------------


def test_a_code_artifact_link_is_a_tracker_too(hub: ChannelHub) -> None:
    project_id = garden(hub)
    _task_id, sid = hub.task(title='Kickoff garden-planner', project_id=project_id, kickoff=True)
    code = 'https://claude.ai/code/artifact/0b8c1f9e-tracker'
    [ack] = report_trackers(hub, sid, code)
    assert 'error' not in ack, ack
    assert tracker_of(hub, project_id) == code


def test_only_the_kickoff_quest_sets_or_replaces_the_tracker(hub: ChannelHub) -> None:
    project_id = garden(hub)
    _kickoff, kickoff_sid = hub.task(
        title='Kickoff garden-planner', project_id=project_id, kickoff=True
    )
    report_trackers(hub, kickoff_sid, TRACKER)
    _other, other_sid = hub.task(title='Fix flaky login test', project_id=project_id)
    [ack] = report_trackers(hub, other_sid, LATER)
    assert ack['error'] == {
        'code': 'not_kickoff',
        'message': "Only the project's kickoff quest sets its tracker.",
    }
    assert tracker_of(hub, project_id) == TRACKER  # the kickoff's stays
    with Session(hub.engine) as s:  # and the refused link is not kept as an item either
        links = s.exec(select(Artifact).where(Artifact.kind == ArtifactKind.link)).all()
        assert [a.url for a in links] == [TRACKER]
    [again] = report_trackers(hub, kickoff_sid, LATER)  # the kickoff's own second report does
    assert 'error' not in again
    assert tracker_of(hub, project_id) == LATER


def test_a_quest_that_is_no_kickoff_never_sets_a_first_tracker(hub: ChannelHub) -> None:
    project_id = garden(hub)
    _task_id, sid = hub.task(title='Fix flaky login test', project_id=project_id)
    [ack] = report_trackers(hub, sid, TRACKER)
    assert ack['error']['code'] == 'not_kickoff'
    assert tracker_of(hub, project_id) is None
