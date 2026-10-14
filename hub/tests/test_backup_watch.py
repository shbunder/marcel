"""A failed nightly backup reaches the owner's phone (S-14.2 follow-up): `deploy/backup.sh` writes
`backup.result` only when it fails; the result watch (S-11.2) posts it once and pushes a note."""

import json
from pathlib import Path

import pytest
from sqlalchemy.engine import Engine
from sqlmodel import Session, select
from test_schedule import Pushes
from test_tasks_fakes import add_agent

from marcel_hub.auth.tokens import OWNER_USER_ID
from marcel_hub.models import Message, MessageRole
from marcel_hub.watchdog import BACKUP_RESULT, RESULTS, ResultWatch, describe_backup
from marcel_hub.ws import EventLog


def said(engine: Engine) -> list[str]:
    with Session(engine) as s:
        rows = s.exec(select(Message).where(Message.role == MessageRole.system))
        return [m.body_json['text'] for m in rows]


@pytest.fixture
def owner(engine: Engine) -> Engine:
    add_agent(engine, 'agt_b', owner=OWNER_USER_ID)
    return engine


def failed(state: Path, reason: str) -> None:
    state.mkdir(parents=True, exist_ok=True)
    body = {'outcome': 'failed', 'reason': reason, 'at': '2026-10-14T03:30:12Z'}
    (state / BACKUP_RESULT).write_text(json.dumps(body))


def test_the_watch_knows_the_backup_file() -> None:
    assert RESULTS[BACKUP_RESULT] is describe_backup


def test_a_failed_backup_is_said_once_with_a_note_push(owner: Engine, tmp_path: Path) -> None:
    failed(tmp_path, 'no database at /home/marcel/marcel/data/marcel.db.')
    pushes = Pushes()
    watch = ResultWatch(owner, EventLog(owner), tmp_path, pusher=pushes)
    assert watch.check() is True
    assert said(owner) == [
        'The nightly backup failed: no database at /home/marcel/marcel/data/marcel.db. '
        'Look at: journalctl --user -u marcel-backup on the NUC.'
    ]
    assert [(n.title, n.category, n.level, n.collapse_key, user) for n, user in pushes.sent] == [
        ('The backup failed', 'note', 'normal', 'backup', OWNER_USER_ID)
    ]
    assert not (tmp_path / BACKUP_RESULT).exists() and (tmp_path / f'{BACKUP_RESULT}.told').exists()
    assert watch.check() is False  # told once
    assert len(said(owner)) == 1 and len(pushes.sent) == 1


def test_the_next_nights_failure_is_said_again(owner: Engine, tmp_path: Path) -> None:
    watch = ResultWatch(owner, EventLog(owner), tmp_path, pusher=Pushes())
    failed(tmp_path, 'another backup is running.')
    watch.check()
    failed(tmp_path, 'the backup stopped while copying the database (exit 1).')
    assert watch.check() is True
    assert 'copying the database' in said(owner)[-1]


@pytest.mark.parametrize('result', [None, {}, {'outcome': 'failed'}, {'reason': '  '}])
def test_a_result_without_a_reason_still_says_it_failed(result: dict | None) -> None:
    title, text, push = describe_backup(result)
    assert title == 'The backup failed' and push == 'note'
    assert 'its result could not be read' in text and 'journalctl' in text


def test_an_unreadable_file_is_still_said(owner: Engine, tmp_path: Path) -> None:
    (tmp_path / BACKUP_RESULT).write_text('{"outcome": "failed", "reas')  # cut short
    pushes = Pushes()
    assert ResultWatch(owner, EventLog(owner), tmp_path, pusher=pushes).check() is True
    assert 'its result could not be read' in said(owner)[0] and len(pushes.sent) == 1
