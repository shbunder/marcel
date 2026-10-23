"""Living artifacts (B-34) and the item-get message (B-36).

Without an id a report makes an artifact at version 1; with an id it adds a version. Every
version stays readable. Only the first version of a task's artifact posts the item-get message.
Checked against contracts/app-api.yaml, events.schema.json and channel.schema.json.
"""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlmodel import Session, select
from test_channel_fakes import MESSAGE, ChannelHub, channel_hub, check
from test_tasks_fakes import validator

from marcel_hub import db
from marcel_hub.artifacts import (
    ArtifactRefused,
    ArtifactStore,
    Content,
    UnknownArtifact,
    blob_name,
)
from marcel_hub.models import (
    Artifact,
    ArtifactKind,
    ArtifactVersion,
    ChannelCall,
    Message,
    MessageKind,
    Project,
    TaskState,
)

ARTIFACT = validator('app-api.yaml#/components/schemas/Artifact')
VERSION = validator('app-api.yaml#/components/schemas/ArtifactVersion')
CREATED = validator('events.schema.json#/$defs/ArtifactCreated')
UPDATED = validator('events.schema.json#/$defs/ArtifactUpdated')


@pytest.fixture
def hub(tmp_path: Path) -> Iterator[ChannelHub]:
    with channel_hub(tmp_path) as hub:
        yield hub


def store(hub: ChannelHub) -> ArtifactStore:
    return hub.app.state.artifacts


def doc(hub: ChannelHub, name: str, body: str) -> str:
    path = hub.workers / name
    path.write_text(body)
    return str(path)


def item_messages(hub: ChannelHub) -> list[Message]:
    return [m for m in hub.messages() if m.kind == MessageKind.artifact]


# Done when: a version bump does not post a second item-get message. ------------------------


def test_the_first_version_posts_the_item_get_message_and_later_ones_do_not(
    hub: ChannelHub,
) -> None:
    task_id, _ = hub.task(title='Compare the offers')
    first = store(hub).publish_reported(
        {'kind': 'doc', 'title': 'Offers', 'path': doc(hub, 'offers.md', '# v1')}, task_id
    )
    [item] = item_messages(hub)
    assert (item.artifact_id, item.task_id, item.project_id) == (
        first.id,
        task_id,
        first.project_id,
    )
    [created] = [e for e in hub.events('message.created') if e['data']['kind'] == 'artifact']
    MESSAGE.validate(created['data'])
    assert created['data']['artifact_id'] == first.id

    for n in (2, 3):
        store(hub).publish_reported(
            {'kind': 'doc', 'title': 'Offers', 'artifact_id': first.id,
             'path': doc(hub, 'offers.md', f'# v{n}'), 'summary': f'Version {n}.'},
            task_id,
        )  # fmt: skip
    assert len(item_messages(hub)) == 1  # still only the first
    assert store(hub).get(first.id).current_version == 3


def test_an_artifact_without_a_task_posts_no_item_message(hub: ChannelHub) -> None:
    store(hub).publish_hub_html('Digest', '<p>hi</p>')
    assert item_messages(hub) == []


def test_a_failed_item_message_never_loses_the_artifact(
    hub: ChannelHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    task_id, _ = hub.task()

    def broken(*_a: Any, **_kw: Any) -> None:
        raise RuntimeError('database is locked')

    monkeypatch.setattr('marcel_hub.artifacts.store.post_message', broken)
    art = store(hub).publish_reported(
        {'kind': 'link', 'title': 'L', 'url': 'https://a.b/'}, task_id
    )
    assert store(hub).get(art.id).title == 'L'
    assert item_messages(hub) == []


# Versions: stored, listed, served. ---------------------------------------------------------


def test_a_new_version_is_stored_announced_and_the_old_one_stays_readable(
    hub: ChannelHub,
) -> None:
    task_id, _ = hub.task(title='Compare the offers')
    first = store(hub).publish_reported(
        {'kind': 'doc', 'title': 'Offers', 'path': doc(hub, 'offers.md', '# one'),
         'summary': 'First look.'},
        task_id,
    )  # fmt: skip
    body = hub.client.get(f'/api/artifacts/{first.id}').json()
    ARTIFACT.validate(body)
    assert body['current_version'] == 1 and body['updated_by'] == 'Compare the offers'
    assert body['updated_at'] == body['created_at']
    [created] = hub.events('artifact.created')[-1:]
    CREATED.validate(created)

    second = store(hub).publish_reported(
        {'kind': 'doc', 'title': 'Offers compared', 'artifact_id': first.id,
         'path': doc(hub, 'offers.html', '<h1>two</h1>'), 'summary': 'Added Engie.'},
        task_id,
    )  # fmt: skip
    assert second.id == first.id and second.current_version == 2
    [updated] = hub.events('artifact.updated')
    UPDATED.validate(updated)
    assert updated['data']['artifact']['current_version'] == 2
    assert updated['data']['artifact']['title'] == 'Offers compared'
    assert updated['data']['version'] == {
        'artifact_id': first.id,
        'version': 2,
        'summary': 'Added Engie.',
        'author': 'Compare the offers',
        'task_id': task_id,
        'content_type': 'text/html',
        'size_bytes': len('<h1>two</h1>'),
        'created_at': updated['data']['version']['created_at'],
    }

    versions = hub.client.get(f'/api/artifacts/{first.id}/versions').json()
    for v in versions:
        VERSION.validate(v)
    assert [(v['version'], v.get('summary')) for v in versions] == [
        (2, 'Added Engie.'),
        (1, 'First look.'),
    ]
    now = hub.client.get(f'/api/artifacts/{first.id}/content')
    assert now.text == '<h1>two</h1>' and now.headers['content-type'].startswith('text/html')
    old = hub.client.get(f'/api/artifacts/{first.id}/content?version=1')
    assert old.text == '# one' and old.headers['content-type'].startswith('text/markdown')
    assert old.headers['content-security-policy'] == 'sandbox'
    missing = hub.client.get(f'/api/artifacts/{first.id}/content?version=3')
    assert missing.status_code == 404 and missing.json()['code'] == 'not_found'
    assert (store(hub).blob_dir / blob_name(first.id, 2)).is_file()
    # The thread shows the new version too.
    assert [e.data_json['title'] for e in hub.task_events(task_id, 'artifact')] == [
        'Offers',
        'Offers compared',
    ]


def test_a_link_gets_a_new_url_and_a_pr_its_new_number(hub: ChannelHub) -> None:
    task_id, _ = hub.task()
    pr = store(hub).publish_reported(
        {'kind': 'pr', 'title': 'Fix', 'url': 'https://github.com/a/b/pull/1'}, task_id
    )
    store(hub).publish_reported(
        {'kind': 'pr', 'title': 'Fix', 'url': 'https://github.com/a/b/pull/2',
         'artifact_id': pr.id},
        task_id,
    )  # fmt: skip
    body = hub.client.get(f'/api/artifacts/{pr.id}').json()
    ARTIFACT.validate(body)
    assert body['url'].endswith('/pull/2') and body['pr']['number'] == 2
    versions = hub.client.get(f'/api/artifacts/{pr.id}/versions').json()
    assert [v['url'][-1] for v in versions] == ['2', '1']


def test_another_task_in_the_project_may_add_a_version(hub: ChannelHub) -> None:
    first_task, _ = hub.task(title='Compare the offers')
    later_task, _ = hub.task(title='Refresh the offers')
    art = store(hub).publish_reported(
        {'kind': 'link', 'title': 'Offers', 'url': 'https://a.b/1'}, first_task
    )
    store(hub).publish_reported(
        {'kind': 'link', 'title': 'Offers', 'url': 'https://a.b/2', 'artifact_id': art.id},
        later_task,
    )
    row = store(hub).get(art.id)
    assert row.task_id == first_task and row.updated_by == 'Refresh the offers'
    assert store(hub).version(art.id, 2).task_id == later_task


def test_the_hub_and_marcel_are_named_as_authors(hub: ChannelHub) -> None:
    art = store(hub).publish_hub_html('Digest', '<p>one</p>')
    assert store(hub).get(art.id).updated_by == 'the hub'
    store(hub).add_version(art.id, Content(data=b'<p>two</p>', content_type='text/html'),
                           author='Marcel')  # fmt: skip
    assert store(hub).get(art.id).updated_by == 'Marcel'
    assert store(hub).content(art.id)[0] == b'<p>two</p>'
    assert store(hub).content(art.id, 1)[0] == b'<p>one</p>'


def test_a_failed_version_write_leaves_no_blob_and_no_version(
    hub: ChannelHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    art = store(hub).publish_hub_html('Digest', '<p>one</p>')
    real = Session.commit

    def broken(self: Session) -> None:
        raise RuntimeError('database is locked')

    monkeypatch.setattr(Session, 'commit', broken)
    with pytest.raises(RuntimeError):
        store(hub).add_version(art.id, Content(data=b'two', content_type='text/html'))
    monkeypatch.setattr(Session, 'commit', real)
    assert not (store(hub).blob_dir / blob_name(art.id, 2)).exists()
    assert store(hub).get(art.id).current_version == 1


def test_versions_of_an_unknown_artifact_are_a_404(hub: ChannelHub) -> None:
    res = hub.client.get('/api/artifacts/art_nope/versions')
    assert res.status_code == 404 and res.json()['code'] == 'artifact_not_found'


def test_the_items_list_puts_the_newest_change_first(hub: ChannelHub) -> None:
    task_id, _ = hub.task()
    old = store(hub).publish_reported(
        {'kind': 'link', 'title': 'Old', 'url': 'https://a.b/'}, task_id
    )
    store(hub).publish_reported({'kind': 'link', 'title': 'New', 'url': 'https://a.b/'}, task_id)
    store(hub).publish_reported(
        {'kind': 'link', 'title': 'Old', 'url': 'https://a.b/2', 'artifact_id': old.id}, task_id
    )
    items = hub.client.get('/api/artifacts').json()['items']
    assert [a['title'] for a in items] == ['Old', 'New']


# Refusals: unknown ids, other projects, boards. --------------------------------------------


def test_a_version_of_an_unknown_artifact_is_refused(hub: ChannelHub) -> None:
    task_id, _ = hub.task()
    with pytest.raises(UnknownArtifact, match='no artifact art_nope'):
        store(hub).publish_reported(
            {'kind': 'link', 'title': 'L', 'url': 'https://a.b/', 'artifact_id': 'art_nope'},
            task_id,
        )


def test_a_version_of_another_projects_artifact_is_refused(hub: ChannelHub) -> None:
    energy = hub.app.state.projects.create(hub.agent_id, 'Energy')
    theirs, _ = hub.task(project_id=energy.id)
    mine, _ = hub.task()
    art = store(hub).publish_reported({'kind': 'link', 'title': 'L', 'url': 'https://a.b/'}, theirs)
    with pytest.raises(UnknownArtifact, match='another project'):
        store(hub).publish_reported(
            {'kind': 'link', 'title': 'L', 'url': 'https://a.b/2', 'artifact_id': art.id}, mine
        )
    assert store(hub).get(art.id).current_version == 1


def test_a_worker_cannot_write_a_board(hub: ChannelHub) -> None:
    task_id, _ = hub.task()
    with Session(hub.engine) as s:
        board_id = s.exec(select(Project.board_artifact_id)).one()
    with pytest.raises(UnknownArtifact, match='project board'):
        store(hub).publish_reported(
            {'kind': 'link', 'title': 'L', 'url': 'https://a.b/', 'artifact_id': board_id}, task_id
        )


def test_a_version_must_keep_its_kind(hub: ChannelHub) -> None:
    task_id, _ = hub.task()
    art = store(hub).publish_reported(
        {'kind': 'link', 'title': 'L', 'url': 'https://a.b/'}, task_id
    )
    with pytest.raises(ArtifactRefused, match='is a link') as refused:
        store(hub).publish_reported(
            {'kind': 'doc', 'title': 'D', 'path': doc(hub, 'd.md', 'x'), 'artifact_id': art.id},
            task_id,
        )
    assert not isinstance(refused.value, UnknownArtifact)


# The channel: an unknown id is refused with an error ack, and nothing is stored. -----------


def test_a_report_with_an_unknown_artifact_id_gets_an_error_ack_and_stores_nothing(
    hub: ChannelHub,
) -> None:
    task_id, sid = hub.task()
    with hub.connect() as ch:
        ch.hello(sid)
        answer = check(
            ch.call(
                'report',
                call_id='c_bad',
                kind='done',
                summary='Updated the offers.',
                artifacts=[
                    {'kind': 'link', 'title': 'New', 'url': 'https://a.b/'},
                    {'kind': 'link', 'title': 'L', 'url': 'https://a.b/', 'artifact_id': 'art_x'},
                ],
            )
        )
    assert answer['call_id'] == 'c_bad'
    assert answer['error']['code'] == 'unknown_artifact'
    assert 'no artifact art_x' in answer['error']['message']
    assert hub.registry.get(task_id).state == TaskState.working  # the report was not stored
    with Session(hub.engine) as s:
        assert s.exec(select(Artifact).where(Artifact.kind != ArtifactKind.board)).all() == []
        assert s.get(ChannelCall, 'c_bad') is None
    assert [m.kind for m in hub.messages()] == []


def test_a_report_with_a_known_artifact_id_adds_a_version(hub: ChannelHub) -> None:
    task_id, sid = hub.task()
    art = store(hub).publish_reported(
        {'kind': 'link', 'title': 'L', 'url': 'https://a.b/1'}, task_id
    )
    with hub.connect() as ch:
        ch.hello(sid)
        answer = check(
            ch.call(
                'report',
                kind='artifact',
                artifact={'kind': 'link', 'title': 'L', 'url': 'https://a.b/2',
                          'artifact_id': art.id, 'summary': 'Moved.'},
            )
        )  # fmt: skip
    assert 'error' not in answer
    assert store(hub).get(art.id).current_version == 2
    assert store(hub).version(art.id, 2).summary == 'Moved.'
    assert len(item_messages(hub)) == 1


def test_a_hub_without_a_store_refuses_a_version_cleanly(hub: ChannelHub) -> None:
    task_id, sid = hub.task()
    hub.gateway.artifacts = None
    with hub.connect() as ch:
        ch.hello(sid)
        answer = check(
            ch.call('report', kind='artifact',
                    artifact={'kind': 'link', 'title': 'L', 'url': 'https://a.b/',
                              'artifact_id': 'art_x'})
        )  # fmt: skip
    assert answer['error']['code'] == 'unknown_artifact'


# The migration. ----------------------------------------------------------------------------


def test_the_migration_versions_old_artifacts_and_gives_every_project_a_board(
    tmp_path: Path,
) -> None:
    engine = db.make_engine(tmp_path / 'old.db')
    db.upgrade(engine, '0005')
    now = "'2026-10-12 00:00:00.000000'"
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO agent (id, owner_user_id, name, animal, palette, seed, "
                          "model_brain, model_worker) VALUES ('agt_old', 'usr_owner', 'Marcel', "
                          "'giraffe', 'pink', '1', 'opus', 'sonnet')"))  # fmt: skip
        conn.execute(text("INSERT INTO project (id, agent_id, slug, name, board_artifact_id, "
                          f"archived, created_at, updated_at) VALUES ('prj_old', 'agt_old', "
                          f"'garden', 'Garden', 'art_board', 0, {now}, {now})"))  # fmt: skip
        conn.execute(text("INSERT INTO task (id, agent_id, project_id, title, location, state, "
                          "model, origin, created_at, updated_at) VALUES ('tsk_old', 'agt_old', "
                          f"'prj_old', 'Old task', 'nuc', 'done', 'sonnet', 'marcel', {now}, "
                          f"{now})"))  # fmt: skip
        conn.execute(text("INSERT INTO artifact (id, task_id, project_id, kind, title, url, "
                          "trusted, self_change, meta_json, created_at) VALUES ('art_t', "
                          f"'tsk_old', 'prj_old', 'link', 'L', 'https://a.b/', 0, 0, '{{}}', "
                          f"{now})"))  # fmt: skip
    db.upgrade(engine)
    with Session(engine) as s:
        art = s.get(Artifact, 'art_t')
        assert art is not None
        assert (art.current_version, art.updated_by) == (1, 'Old task')
        assert art.updated_at == art.created_at
        first = s.get(ArtifactVersion, ('art_t', 1))
        assert first is not None and first.url == 'https://a.b/' and first.author == 'Old task'
        board = s.get(Artifact, 'art_board')
        assert board is not None and board.kind is ArtifactKind.board
        assert (board.project_id, board.title) == ('prj_old', 'Garden — board')
        assert s.get(ArtifactVersion, ('art_board', 1)) is not None


def test_a_downgrade_with_versions_boards_and_item_messages_keeps_the_rest(
    hub: ChannelHub,
) -> None:
    task_id, _ = hub.task()
    art = store(hub).publish_reported(
        {'kind': 'link', 'title': 'L', 'url': 'https://a.b/1'}, task_id
    )
    store(hub).publish_reported(
        {'kind': 'link', 'title': 'L', 'url': 'https://a.b/2', 'artifact_id': art.id}, task_id
    )
    engine = hub.engine
    db.downgrade(engine, '0005')
    with engine.connect() as conn:
        assert conn.execute(text('PRAGMA foreign_key_check')).all() == []
        assert conn.execute(text('PRAGMA foreign_keys')).scalar_one() == 1
        kinds = conn.execute(text('SELECT kind FROM artifact')).scalars().all()
        assert kinds == ['link']
        assert (
            conn.execute(text("SELECT count(*) FROM message WHERE kind = 'artifact'")).scalar_one()
            == 0
        )
        names = {r[0] for r in conn.execute(text('SELECT name FROM sqlite_master'))}
        assert 'artifact_version' not in names
    db.upgrade(engine)
    with Session(engine) as s:
        boards = s.exec(select(Artifact).where(Artifact.kind == ArtifactKind.board)).all()
        projects = s.exec(select(Project)).all()
        assert {b.id for b in boards} == {p.board_artifact_id for p in projects}
