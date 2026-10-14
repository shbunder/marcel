"""Telling the owner how a redeploy went (S-11.2): the host's `deploy/redeploy.sh` writes
`redeploy.result` next to the flag; the hub posts it once in the owner's main conversation and
pushes all but a plain success."""

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlmodel import Session, select
from test_schedule import Pushes
from test_tasks_fakes import add_agent

from marcel_hub import db, watchdog
from marcel_hub.app import Settings, create_app
from marcel_hub.auth.tokens import OWNER_USER_ID
from marcel_hub.models import Agent, Message, MessageRole
from marcel_hub.watchdog import RESULT, ResultWatch, describe
from marcel_hub.ws import EventLog

SHA = 'c' * 40
TOLD = f'{RESULT}.told'


def said(engine: Engine) -> list[tuple[str, str]]:
    """(conversation, text) of every system message."""
    with Session(engine) as s:
        rows = s.exec(select(Message).where(Message.role == MessageRole.system))
        return [(m.conversation_id, m.body_json['text']) for m in rows]


def write(state: Path, **fields: Any) -> None:
    state.mkdir(parents=True, exist_ok=True)
    (state / RESULT).write_text(json.dumps({'sha': SHA, 'subject': 'Hub: answer faster', **fields}))


@pytest.fixture
def owner(engine: Engine) -> Engine:
    add_agent(engine, 'agt_b', owner=OWNER_USER_ID)
    add_agent(
        engine, 'agt_a_emma', owner='usr_emma'
    )  # sorts first, but someone else's agent is never told
    return engine


def main_of(engine: Engine, agent_id: str) -> str | None:
    with Session(engine) as s:
        agent = s.get(Agent, agent_id)
        assert agent is not None
        return agent.main_conversation_id


@pytest.mark.parametrize(
    ('outcome', 'pushed', 'words'),
    [
        ('deployed', False, 'Marcel is updated with *Hub: answer faster* (ccccccc)'),
        ('version_drift', True, 'Claude Code'),
        ('rolled_back', True, 'went back to the version before it (revert ddddddd)'),
        ('rollback_failed', True, 'Check the NUC'),
        ('refused', True, 'is not on main'),
    ],
)
def test_each_outcome_is_said_once_and_pushed_unless_it_simply_went_in(
    owner: Engine, tmp_path: Path, outcome: str, pushed: bool, words: str
) -> None:
    reasons = {
        'version_drift': 'Claude Code is not the version Marcel is tested with.',
        'rollback_failed': 'Marcel is still not healthy. Check the NUC: make -C deploy health.',
        'refused': 'Commit ccccccc is not on main on GitHub, so nothing was changed.',
    }
    write(tmp_path, outcome=outcome, reason=reasons.get(outcome, ''), revert='d' * 40)
    pushes = Pushes()
    watch = ResultWatch(owner, EventLog(owner), tmp_path, pusher=pushes)

    assert watch.check() is True
    ((conversation, text),) = said(owner)
    assert conversation == main_of(owner, 'agt_b')
    assert words in text
    assert [(n.collapse_key, user) for n, user in pushes.sent] == (
        [('redeploy', OWNER_USER_ID)] if pushed else []
    )
    assert not (tmp_path / RESULT).exists() and (tmp_path / TOLD).exists()

    assert watch.check() is False  # told once
    assert len(said(owner)) == 1 and len(pushes.sent) == (1 if pushed else 0)


def test_a_new_result_after_one_was_told_is_told_too(owner: Engine, tmp_path: Path) -> None:
    watch = ResultWatch(owner, EventLog(owner), tmp_path, pusher=Pushes())
    write(tmp_path, outcome='deployed')
    watch.check()
    write(tmp_path, outcome='rolled_back', reason='')
    assert watch.check() is True
    assert len(said(owner)) == 2


def test_an_unreadable_result_is_still_said(owner: Engine, tmp_path: Path) -> None:
    (tmp_path / RESULT).write_text('{"outcome": "deployed", "sha": ')  # cut short
    pushes = Pushes()
    assert ResultWatch(owner, EventLog(owner), tmp_path, pusher=pushes).check() is True
    ((_, text),) = said(owner)
    assert 'could not be read' in text and len(pushes.sent) == 1


def test_no_result_says_nothing(owner: Engine, tmp_path: Path) -> None:
    assert ResultWatch(owner, EventLog(owner), tmp_path / 'missing').check() is False
    assert said(owner) == []


def test_an_archived_agent_is_skipped(engine: Engine, tmp_path: Path) -> None:
    from datetime import UTC, datetime

    add_agent(engine, 'agt_a', owner=OWNER_USER_ID)
    add_agent(engine, 'agt_b', owner=OWNER_USER_ID)
    with Session(engine) as s:
        old = s.get(Agent, 'agt_a')
        assert old is not None
        old.archived_at = datetime.now(UTC)
        s.add(old)
        s.commit()
    write(tmp_path, outcome='deployed')
    ResultWatch(engine, EventLog(engine), tmp_path).check()
    ((conversation, _),) = said(engine)
    assert conversation == main_of(engine, 'agt_b') and main_of(engine, 'agt_a') is None


def test_a_failed_post_keeps_the_result_for_the_next_look(
    owner: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write(tmp_path, outcome='rolled_back', reason='')
    watch = ResultWatch(owner, EventLog(owner), tmp_path, pusher=Pushes())

    def down(*_a: Any, **_k: Any) -> None:
        raise RuntimeError('database is locked')

    monkeypatch.setattr(watchdog, 'post_message', down)
    with pytest.raises(RuntimeError):
        watch.check()
    assert (tmp_path / RESULT).exists()
    monkeypatch.undo()
    assert watch.check() is True and len(said(owner)) == 1


def test_a_failed_push_still_tells_in_the_app(owner: Engine, tmp_path: Path) -> None:
    class Down:
        def push(self, *_a: Any) -> bool:
            raise RuntimeError('ntfy down')

    write(tmp_path, outcome='rollback_failed', reason='x')
    assert ResultWatch(owner, EventLog(owner), tmp_path, pusher=Down()).check() is True
    assert len(said(owner)) == 1 and not (tmp_path / RESULT).exists()


def test_without_an_agent_the_result_is_logged_and_set_aside(
    engine: Engine, tmp_path: Path
) -> None:
    write(tmp_path, outcome='deployed')
    assert ResultWatch(engine, EventLog(engine), tmp_path).check() is True
    assert said(engine) == [] and (tmp_path / TOLD).exists()


def test_the_loop_survives_a_failed_look(
    owner: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    looks: list[int] = []
    watch = ResultWatch(owner, EventLog(owner), tmp_path, tick=0.01)

    def check() -> bool:
        looks.append(1)
        if len(looks) == 1:
            raise RuntimeError('first look fails')
        return False

    monkeypatch.setattr(watch, 'check', check)

    async def run() -> None:
        watch.start()
        await asyncio.sleep(0.1)
        await watch.stop()

    asyncio.run(run())
    assert len(looks) > 1


def test_describe_leads_with_the_outcome() -> None:
    title, text, push = describe(
        {'outcome': 'rolled_back', 'sha': SHA, 'subject': 'Hub: x', 'reason': ''}
    )
    assert title == 'An update was rolled back' and text.startswith(
        '*Hub: x* (ccccccc) did not come up healthy'
    )
    assert push


def test_the_app_tells_a_result_that_waited_for_its_start(tmp_path: Path) -> None:
    """The production path: a redeploy restarts the hub; its first look tells the result."""
    state = tmp_path / 'state'
    write(state, outcome='rolled_back', reason='The new version was not healthy within 120 s.')
    app: Any = create_app(
        Settings(
            data_dir=tmp_path, db_path=tmp_path / 'm.db', redeploy_flag=state / 'redeploy.flag'
        )
    )
    pushes = Pushes()
    app.state.result_watch.pusher = pushes
    db.upgrade(app.state.engine)
    add_agent(
        app.state.engine, 'agt_b', owner=OWNER_USER_ID
    )  # the owner's agent, there before the redeploy
    with TestClient(app):
        for _ in range(100):
            if said(app.state.engine):
                break
            asyncio.run(asyncio.sleep(0.05))
        assert app.state.result_watch._task is not None
    ((_, text),) = said(app.state.engine)
    assert 'went back to the version before it' in text
    assert len(pushes.sent) == 1 and (state / TOLD).exists()
