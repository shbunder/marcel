"""Artifacts (B-21): what workers hand in, how the hub stores it, and `/api/artifacts`.

Checked against contracts/app-api.yaml, contracts/channel.schema.json and
contracts/dashboard.schema.json. Nothing here reaches GitHub or the network.
"""

import copy
import json
import os
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from test_tasks_fakes import RecordingPublisher, add_agent, put_task, run, validator
from test_tasks_routes import pair

from marcel_hub import artifacts
from marcel_hub.app import Settings, create_app
from marcel_hub.artifacts import (
    ArtifactNotFound,
    ArtifactRefused,
    ArtifactStore,
    NoPrStatus,
    PrStatusUnavailable,
    pr_status_from_gh,
)
from marcel_hub.models import ArtifactKind, TaskEventType, TaskState

ARTIFACT = validator('app-api.yaml#/components/schemas/Artifact')
PAGE = validator('app-api.yaml#/components/schemas/ArtifactPage')
ERROR = validator('app-api.yaml#/components/schemas/Error')
REPORTED = validator('channel.schema.json#/$defs/Artifact')
CREATED = validator('events.schema.json#/$defs/ArtifactCreated')
TASK_EVENT = validator('app-api.yaml#/components/schemas/TaskEvent')

CONTRACTS = Path(__file__).resolve().parents[2] / 'contracts'
DASHBOARD = json.loads((CONTRACTS / 'dashboard.schema.json').read_text())['examples'][0]


class Hub:
    def __init__(self, client: TestClient, store: ArtifactStore, workspace: Path) -> None:
        self.client = client
        self.store = store
        self.workspace = workspace

    def file(self, name: str, data: bytes | str) -> str:
        path = self.workspace / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data.encode() if isinstance(data, str) else data)
        return str(path)

    def report(self, spec: dict[str, Any], task_id: str | None = None) -> dict[str, Any]:
        REPORTED.validate(spec)  # the test only hands in what the channel contract allows
        return self.store.to_api(self.store.publish_reported(spec, task_id=task_id))

    def get(self, path: str, **kw: Any) -> Any:
        return self.client.get(f'/api{path}', **kw)


@pytest.fixture
def hub(tmp_path: Path) -> Iterator[Hub]:
    app = create_app(Settings(data_dir=tmp_path / 'data', db_path=tmp_path / 'data' / 'marcel.db'))
    workspace = tmp_path / 'workspaces'
    workspace.mkdir()
    with TestClient(app) as client:
        add_agent(app.state.engine)
        client.headers['Authorization'] = f'Bearer {pair(client, app.state.engine)}'
        store: ArtifactStore = app.state.artifacts
        store.files_root = workspace
        yield Hub(client, store, workspace)


# Done when: a bad chart is refused; worker HTML is untrusted. ------------------------------


def test_a_chart_whose_series_length_differs_from_its_labels_is_refused(hub: Hub) -> None:
    dashboard = copy.deepcopy(DASHBOARD)
    dashboard['widgets'][2]['series'][0]['values'] = [30.1, 33.4, 35.9]  # 4 labels, 3 values
    path = hub.file('volumes.dashboard.json', json.dumps(dashboard))

    with pytest.raises(ArtifactRefused) as refused:
        hub.report({'kind': 'dashboard', 'title': 'Docker volumes', 'path': path})

    message = str(refused.value)
    assert 'Growth per week' in message
    assert '4 labels' in message
    assert '3 values' in message
    assert hub.get('/artifacts').json()['items'] == []
    assert not hub.store.blob_dir.exists() or list(hub.store.blob_dir.iterdir()) == []


def test_a_chart_inside_a_section_is_checked_too(hub: Hub) -> None:
    dashboard = {'version': 1, 'title': 'Board', 'widgets': [{'type': 'section', 'title': 'Now',
                 'widgets': [{'type': 'chart', 'kind': 'line', 'labels': ['a', 'b'],
                              'series': [{'name': 's', 'values': [1, 2, 3]}]}]}]}  # fmt: skip
    path = hub.file('board.json', json.dumps(dashboard))
    with pytest.raises(ArtifactRefused, match='2 labels but series “s” has 3 values'):
        hub.report({'kind': 'dashboard', 'title': 'Board', 'path': path})


def test_a_dashboard_that_breaks_the_schema_is_refused_with_where_and_what(hub: Hub) -> None:
    dashboard = copy.deepcopy(DASHBOARD)
    dashboard['widgets'][2]['kind'] = 'pie'
    path = hub.file('d.json', json.dumps(dashboard))

    with pytest.raises(ArtifactRefused) as refused:
        hub.report({'kind': 'dashboard', 'title': 'Volumes', 'path': path})

    assert str(refused.value).startswith('The dashboard was not saved')
    assert 'widgets → 2' in str(refused.value)


def test_a_dashboard_that_is_not_json_is_refused(hub: Hub) -> None:
    path = hub.file('d.json', '{not json')
    with pytest.raises(ArtifactRefused, match='not valid JSON'):
        hub.report({'kind': 'dashboard', 'title': 'Volumes', 'path': path})


def test_a_valid_dashboard_is_stored_as_json(hub: Hub) -> None:
    path = hub.file('volumes.dashboard.json', json.dumps(DASHBOARD))

    art = hub.report({'kind': 'dashboard', 'title': 'Docker volumes', 'path': path})

    ARTIFACT.validate(art)
    assert art['content_type'] == 'application/json'
    res = hub.get(f'/artifacts/{art["id"]}/content')
    assert res.headers['content-type'] == 'application/json'
    assert res.json() == DASHBOARD


def test_a_worker_html_doc_is_stored_untrusted(hub: Hub) -> None:
    path = hub.file('report.html', '<h1>Report</h1><script>alert(1)</script>')

    art = hub.report({'kind': 'doc', 'title': 'Report', 'path': path})

    assert art['trusted'] is False
    assert art['content_type'] == 'text/html'
    assert hub.get(f'/artifacts/{art["id"]}').json()['trusted'] is False


def test_html_the_hub_renders_itself_is_trusted(hub: Hub) -> None:
    art = hub.store.to_api(hub.store.publish_hub_html('Weekly digest', '<h1>Week 41</h1>'))
    ARTIFACT.validate(art)
    assert art['trusted'] is True
    assert art['kind'] == 'doc'


def test_untrusted_html_is_served_so_a_browser_never_runs_it(hub: Hub) -> None:
    path = hub.file('report.html', '<script>alert(1)</script>')
    art = hub.report({'kind': 'doc', 'title': 'Report', 'path': path})

    res = hub.get(f'/artifacts/{art["id"]}/content')

    assert res.status_code == 200
    assert res.headers['content-type'].startswith('text/html')
    assert res.headers['content-security-policy'] == 'sandbox'
    assert res.headers['x-content-type-options'] == 'nosniff'
    assert res.text == '<script>alert(1)</script>'  # bytes as handed in, never rendered


def test_trusted_html_may_run_scripts_but_stays_sandboxed(hub: Hub) -> None:
    art = hub.store.publish_hub_html('Digest', '<p>hi</p>')
    res = hub.get(f'/artifacts/{art.id}/content')
    assert res.headers['content-security-policy'] == 'sandbox allow-scripts'


# Done when: content served with the right content type. ------------------------------------


@pytest.mark.parametrize(
    ('kind', 'name', 'data', 'content_type'),
    [
        ('doc', 'notes.md', '# Notes\n', 'text/markdown'),
        ('doc', 'page.html', '<p>x</p>', 'text/html'),
        ('file', 'shot.png', b'\x89PNG\r\n\x1a\nfake', 'image/png'),
        ('file', 'offer.pdf', b'%PDF-1.7 fake', 'application/pdf'),
        ('file', 'data.bin', b'\x00\x01', 'application/octet-stream'),
    ],
)
def test_content_is_served_with_its_own_content_type(
    hub: Hub, kind: str, name: str, data: bytes | str, content_type: str
) -> None:
    art = hub.report({'kind': kind, 'title': name, 'path': hub.file(name, data)})

    res = hub.get(f'/artifacts/{art["id"]}/content')

    assert res.status_code == 200
    assert res.headers['content-type'].split(';')[0] == content_type
    assert res.content == (data.encode() if isinstance(data, str) else data)
    assert art['content_type'] == content_type
    assert art['size_bytes'] == len(res.content)


def test_pr_and_link_have_no_content(hub: Hub) -> None:
    pr = hub.report(
        {'kind': 'pr', 'title': 'Fix', 'url': 'https://github.com/shbunder/marcel/pull/14'}
    )
    res = hub.get(f'/artifacts/{pr["id"]}/content')
    assert res.status_code == 404
    ERROR.validate(res.json())
    assert 'Open its link' in res.json()['message']


def test_an_older_version_of_content_is_not_found_yet(hub: Hub) -> None:
    art = hub.report({'kind': 'doc', 'title': 'n', 'path': hub.file('n.md', 'x')})
    assert hub.get(f'/artifacts/{art["id"]}/content?version=1').status_code == 200
    assert hub.get(f'/artifacts/{art["id"]}/content?version=2').status_code == 404
    assert hub.get(f'/artifacts/{art["id"]}/content?version=0').status_code == 400


def test_a_blob_missing_from_disk_is_a_readable_404(hub: Hub) -> None:
    art = hub.report({'kind': 'doc', 'title': 'n', 'path': hub.file('n.md', 'x')})
    (hub.store.blob_dir / art['id']).unlink()
    res = hub.get(f'/artifacts/{art["id"]}/content')
    assert res.status_code == 404
    assert res.json()['code'] == 'artifact_not_found'


# What each kind requires (channel.schema.json Artifact). -----------------------------------


def test_a_pr_gets_its_number_and_an_open_state(hub: Hub) -> None:
    art = hub.report(
        {'kind': 'pr', 'title': 'Fix', 'url': 'https://github.com/shbunder/marcel/pull/14'}
    )
    ARTIFACT.validate(art)
    assert art['pr'] == {'number': 14, 'state': 'open'}
    assert art['url'] == 'https://github.com/shbunder/marcel/pull/14'
    assert 'content_type' not in art


def test_a_pr_url_without_a_pull_number_is_refused(hub: Hub) -> None:
    with pytest.raises(ArtifactRefused, match='pull request'):
        hub.report({'kind': 'pr', 'title': 'Fix', 'url': 'https://github.com/shbunder/marcel'})


@pytest.mark.parametrize('kind', ['branch', 'link'])
def test_branches_and_links_keep_their_url(hub: Hub, kind: str) -> None:
    art = hub.report({'kind': kind, 'title': 'x', 'url': 'https://example.com/a'})
    ARTIFACT.validate(art)
    assert art['url'] == 'https://example.com/a'


@pytest.mark.parametrize(
    'spec',
    [
        {'kind': 'link', 'title': 'x'},
        {'kind': 'link', 'title': 'x', 'url': 'javascript:alert(1)'},
        {'kind': 'link', 'title': 'x', 'url': 'file:///etc/passwd'},
        {'kind': 'doc', 'title': 'x'},
        {'kind': 'board', 'title': 'x', 'path': '/x.json'},
        {'kind': 'doc', 'title': '', 'path': '/x.md'},
        {'kind': 'doc', 'title': 'x', 'path': '/x.md', 'surprise': 1},
    ],
)
def test_a_report_missing_what_its_kind_needs_is_refused(hub: Hub, spec: dict[str, Any]) -> None:
    with pytest.raises(ArtifactRefused):
        hub.store.publish_reported(spec)


def test_a_new_version_is_refused_until_living_artifacts_exist(hub: Hub) -> None:
    with pytest.raises(ArtifactRefused, match='new version'):
        hub.report({'kind': 'link', 'title': 'x', 'url': 'https://a.b/', 'artifact_id': 'art_1'})


def test_a_doc_must_be_markdown_or_html(hub: Hub) -> None:
    with pytest.raises(ArtifactRefused, match='Markdown or HTML'):
        hub.report({'kind': 'doc', 'title': 'x', 'path': hub.file('x.pdf', b'%PDF')})


# The file guard: the hub copies only from the workers' folder. ------------------------------


def test_a_file_outside_the_workspaces_is_refused(hub: Hub, tmp_path: Path) -> None:
    secret = tmp_path / 'data' / 'marcel.toml'
    secret.write_text('owner_name = "x"\n')
    for path in (str(secret), str(hub.workspace / '..' / 'data' / 'marcel.toml')):
        with pytest.raises(ArtifactRefused, match='outside'):
            hub.report({'kind': 'file', 'title': 'x', 'path': path})


def test_a_symlink_out_of_the_workspaces_is_refused(hub: Hub, tmp_path: Path) -> None:
    secret = tmp_path / 'data' / 'marcel.db'
    (hub.workspace / 'db.bin').symlink_to(secret)
    with pytest.raises(ArtifactRefused, match='outside'):
        hub.report({'kind': 'file', 'title': 'x', 'path': str(hub.workspace / 'db.bin')})


def test_a_folder_swapped_for_a_link_while_copying_is_refused(
    hub: Hub, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A worker swaps a folder for a link to the hub's data just as the hub starts reading.
    The hub opens one folder at a time without following links, so it never reads through it."""
    hub.file('reports/marcel.toml', 'harmless\n')
    secret_dir = tmp_path / 'data'
    (secret_dir / 'marcel.toml').write_text('owner token\n')
    real_open = os.open
    swapped: list[bool] = []

    def open_and_swap(path: Any, flags: int, *args: Any, **kw: Any) -> int:
        if not swapped:  # first open: the workers' folder itself
            swapped.append(True)
            shutil.rmtree(hub.workspace / 'reports')
            (hub.workspace / 'reports').symlink_to(secret_dir)
        return real_open(path, flags, *args, **kw)

    monkeypatch.setattr(artifacts.os, 'open', open_and_swap)
    with pytest.raises(ArtifactRefused, match='goes through a link'):
        hub.report({'kind': 'file', 'title': 'x',
                    'path': str(hub.workspace / 'reports' / 'marcel.toml')})  # fmt: skip
    assert swapped


def test_a_file_in_a_subfolder_of_the_workspaces_is_copied(hub: Hub) -> None:
    path = hub.file('task-1/out/notes.md', '# Notes\n')
    art = hub.report({'kind': 'doc', 'title': 'Notes', 'path': path})
    assert hub.get(f'/artifacts/{art["id"]}/content').text == '# Notes\n'


def test_a_file_where_a_folder_should_be_is_missing(hub: Hub) -> None:
    hub.file('plain', 'x')
    with pytest.raises(ArtifactRefused, match='no file'):
        hub.report({'kind': 'file', 'title': 'x', 'path': str(hub.workspace / 'plain' / 'x')})


def test_a_relative_or_missing_path_is_refused(hub: Hub) -> None:
    with pytest.raises(ArtifactRefused, match='absolute'):
        hub.report({'kind': 'file', 'title': 'x', 'path': 'x.bin'})
    with pytest.raises(ArtifactRefused, match='no file'):
        hub.report({'kind': 'file', 'title': 'x', 'path': str(hub.workspace / 'nope.bin')})
    (hub.workspace / 'dir').mkdir()
    with pytest.raises(ArtifactRefused, match='no file'):
        hub.report({'kind': 'file', 'title': 'x', 'path': str(hub.workspace / 'dir')})


def test_without_a_workspaces_folder_no_file_is_copied(hub: Hub) -> None:
    hub.store.files_root = None
    with pytest.raises(ArtifactRefused, match='cannot read files'):
        hub.report({'kind': 'file', 'title': 'x', 'path': hub.file('x.bin', b'x')})


def test_a_file_over_the_size_limit_is_refused(hub: Hub, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(artifacts, 'MAX_BLOB_BYTES', 4)
    with pytest.raises(ArtifactRefused, match='too big'):
        hub.report({'kind': 'file', 'title': 'x', 'path': hub.file('x.bin', b'12345')})


def test_an_unknown_task_is_refused_before_anything_is_written(hub: Hub) -> None:
    with pytest.raises(ArtifactRefused, match='no task tsk_no'):
        hub.report({'kind': 'doc', 'title': 'x', 'path': hub.file('x.md', 'x')}, task_id='tsk_no')
    assert not hub.store.blob_dir.exists() or list(hub.store.blob_dir.iterdir()) == []


def test_a_failed_database_write_leaves_no_blob(hub: Hub, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(art: Any) -> Any:
        raise RuntimeError('database is locked')

    monkeypatch.setattr(hub.store, '_insert', broken)
    with pytest.raises(RuntimeError):
        hub.report({'kind': 'doc', 'title': 'x', 'path': hub.file('x.md', 'x')})
    assert list(hub.store.blob_dir.iterdir()) == []


# Recording: the task's event and the app event. ---------------------------------------------


def test_an_artifact_is_recorded_on_its_task_and_announced(hub: Hub) -> None:
    publisher = RecordingPublisher()
    hub.store.publisher = publisher
    tasks = hub.client.app.state.tasks  # type: ignore[attr-defined]
    tasks.publisher = publisher
    task_id = put_task(tasks.engine, TaskState.working)

    art = hub.report({'kind': 'link', 'title': 'Docs', 'url': 'https://a.b/'}, task_id=task_id)

    events, _ = tasks.events(task_id)
    assert [e.type for e in events] == [TaskEventType.artifact]
    TASK_EVENT.validate(tasks.event_to_api(events[0]))
    assert events[0].data_json == {'artifact_id': art['id'], 'kind': 'link', 'title': 'Docs'}
    created = [d for t, d in publisher.sent if t == 'artifact.created']
    assert created == [art]
    CREATED.validate({'id': 1, 'at': '2026-10-12T00:00:00Z', 'type': 'artifact.created',
                      'data': art})  # fmt: skip
    assert hub.get(f'/tasks/{task_id}').json()['artifact_count'] == 1


# The routes. ------------------------------------------------------------------------------


def test_the_list_is_newest_first_and_pages(hub: Hub) -> None:
    ids = [hub.report({'kind': 'link', 'title': f'n{i}', 'url': 'https://a.b/'})['id']
           for i in range(3)]  # fmt: skip

    first = hub.get('/artifacts?limit=2').json()
    PAGE.validate(first)
    second = hub.get(f'/artifacts?limit=2&cursor={first["next_cursor"]}').json()

    assert [a['id'] for a in first['items'] + second['items']] == ids[::-1]
    assert 'next_cursor' not in second


def test_the_list_filters_by_task_and_kind(hub: Hub) -> None:
    task_id = put_task(hub.store.engine, TaskState.working)
    on_task = hub.report({'kind': 'link', 'title': 't', 'url': 'https://a.b/'}, task_id=task_id)
    pr = hub.report({'kind': 'pr', 'title': 'p', 'url': 'https://github.com/a/b/pull/1'})

    assert [a['id'] for a in hub.get(f'/artifacts?task_id={task_id}').json()['items']] == [
        on_task['id']
    ]
    assert [a['id'] for a in hub.get('/artifacts?kind=pr').json()['items']] == [pr['id']]
    assert hub.get('/artifacts?kind=board').json() == {'items': []}


@pytest.mark.parametrize('query', ['kind=pie', 'limit=0', 'limit=201', 'limit=x', 'cursor=abc'])
def test_a_bad_list_query_is_a_400(hub: Hub, query: str) -> None:
    res = hub.get(f'/artifacts?{query}')
    assert res.status_code == 400
    ERROR.validate(res.json())


def test_an_unknown_artifact_is_a_404(hub: Hub) -> None:
    for path in ('/artifacts/art_nope', '/artifacts/art_nope/content'):
        res = hub.get(path)
        assert res.status_code == 404
        assert res.json()['code'] == 'artifact_not_found'


# PR status refresh: the source is a port; GitHub is never reached in tests. -----------------


class FakeGitHub:
    def __init__(self, status: dict[str, Any] | None = None) -> None:
        self.status = status
        self.asked: list[str] = []

    async def fetch(self, url: str) -> dict[str, Any]:
        self.asked.append(url)
        if self.status is None:
            raise PrStatusUnavailable('GitHub did not answer.')
        return self.status


def test_refreshing_a_pr_stores_its_checks_and_review(hub: Hub) -> None:
    url = 'https://github.com/shbunder/marcel/pull/14'
    art = hub.report({'kind': 'pr', 'title': 'Fix', 'url': url})
    github = FakeGitHub({'state': 'open', 'checks': 'passing', 'review': 'approved'})
    hub.store.pr_source = github

    run(hub.store.refresh_pr(art['id']))

    got = hub.get(f'/artifacts/{art["id"]}').json()
    ARTIFACT.validate(got)
    assert got['pr'] == {'number': 14, 'state': 'open', 'checks': 'passing', 'review': 'approved'}
    assert github.asked == [url]


def test_a_failed_refresh_keeps_the_last_status(hub: Hub) -> None:
    art = hub.report({'kind': 'pr', 'title': 'Fix', 'url': 'https://github.com/a/b/pull/2'})
    hub.store.pr_source = FakeGitHub(None)
    with pytest.raises(PrStatusUnavailable):
        run(hub.store.refresh_pr(art['id']))
    assert hub.get(f'/artifacts/{art["id"]}').json()['pr'] == {'number': 2, 'state': 'open'}


def test_without_a_github_source_refresh_says_why(hub: Hub) -> None:
    art = hub.report({'kind': 'pr', 'title': 'Fix', 'url': 'https://github.com/a/b/pull/2'})
    assert isinstance(hub.store.pr_source, NoPrStatus)
    with pytest.raises(PrStatusUnavailable, match='GitHub'):
        run(hub.store.refresh_pr(art['id']))


def test_refresh_refuses_what_is_not_a_pr(hub: Hub) -> None:
    link = hub.report({'kind': 'link', 'title': 'x', 'url': 'https://a.b/'})
    with pytest.raises(ArtifactRefused):
        run(hub.store.refresh_pr(link['id']))
    with pytest.raises(ArtifactNotFound):
        run(hub.store.refresh_pr('art_nope'))


def test_a_refresh_with_values_outside_the_contract_is_refused(hub: Hub) -> None:
    art = hub.report({'kind': 'pr', 'title': 'Fix', 'url': 'https://github.com/a/b/pull/2'})
    hub.store.pr_source = FakeGitHub({'state': 'exploded'})
    with pytest.raises(PrStatusUnavailable, match='state'):
        run(hub.store.refresh_pr(art['id']))


@pytest.mark.parametrize(
    ('pull', 'runs', 'reviews', 'expected'),
    [
        ({'state': 'open'}, [], [], {'state': 'open', 'checks': 'none', 'review': 'none'}),
        ({'state': 'open', 'draft': True, 'requested_reviewers': [{'login': 'a'}]},
         [{'status': 'in_progress', 'conclusion': None}], [],
         {'state': 'draft', 'checks': 'pending', 'review': 'requested'}),
        ({'state': 'closed', 'merged_at': '2026-10-12T00:00:00Z'},
         [{'status': 'completed', 'conclusion': 'success'},
          {'status': 'completed', 'conclusion': 'skipped'}],
         [{'user': {'login': 'a'}, 'state': 'APPROVED'}],
         {'state': 'merged', 'checks': 'passing', 'review': 'approved'}),
        ({'state': 'closed', 'merged_at': None},
         [{'status': 'completed', 'conclusion': 'failure'}],
         [{'user': {'login': 'a'}, 'state': 'APPROVED'},
          {'user': {'login': 'b'}, 'state': 'CHANGES_REQUESTED'}],
         {'state': 'closed', 'checks': 'failing', 'review': 'changes_requested'}),
        ({'state': 'open'}, [{'status': 'completed', 'conclusion': 'success'}],
         [{'user': {'login': 'a'}, 'state': 'CHANGES_REQUESTED'},
          {'user': {'login': 'a'}, 'state': 'COMMENTED'},
          {'user': {'login': 'a'}, 'state': 'APPROVED'}],
         {'state': 'open', 'checks': 'passing', 'review': 'approved'}),
    ],
)  # fmt: skip
def test_gh_api_answers_map_to_the_contract(
    pull: dict[str, Any], runs: list[dict[str, Any]], reviews: list[dict[str, Any]],
    expected: dict[str, str],
) -> None:  # fmt: skip
    assert pr_status_from_gh(pull, {'check_runs': runs}, reviews) == expected


def test_the_store_works_without_the_app(tmp_path: Path, engine: Any) -> None:
    store = ArtifactStore(engine, tmp_path / 'blobs')
    art = store.publish_hub_html('Digest', '<p>x</p>')
    assert store.content(art.id)[0] == b'<p>x</p>'
    assert store.get(art.id).kind is ArtifactKind.doc
