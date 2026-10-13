"""`POST /api/session/report` (S-07.2): a cloud worker reports with its task's token.

Each control here has a test that fails without it: the token check (wrong task, expired, spent by
done / failed / stop), the hidden 404 for other methods, the body cap, the wrong-token limiter per
caller and overall, the per-task report limit, and taking a retried report once. Nothing here
reaches a runner, `claude` or the network: `FakeCloud` stands in for the runner's cloud routes.
"""

import asyncio
import logging
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml
from jsonschema import Draft202012Validator
from sqlmodel import Session, select
from test_channel_fakes import channel_hub
from test_cloud import PR_URL, CloudHub
from test_tasks_fakes import run

from marcel_hub import report_api
from marcel_hub.models import Artifact, ArtifactKind, Task, TaskState, utcnow
from marcel_hub.report_api import MAX_REPORTS, MAX_WRONG_TOKENS, REPEAT_WINDOW, ReportGate

CONTRACTS = Path(__file__).resolve().parents[2] / 'contracts'
SESSION_API = yaml.safe_load((CONTRACTS / 'session-api.yaml').read_text())
REPORT_OP = SESSION_API['paths']['/session/report']['post']
ACCEPTED = Draft202012Validator(
    REPORT_OP['responses']['202']['content']['application/json']['schema']
)
ERROR = Draft202012Validator(SESSION_API['components']['schemas']['Error'])
URL = '/api/session/report'
HIDDEN = {'code': 'not_found', 'message': 'There is nothing at that address.'}
FACT = 'The Luminus contract ends on 1 March 2027.'


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class ReportHub(CloudHub):
    def __init__(self, *args: Any) -> None:
        super().__init__(*args)
        self.gate: ReportGate = self.hub.app.state.report_gate
        self.clock = Clock()
        self.gate.clock = self.clock

    def post(self, token: str | None, body: Any = None, caller: str = '203.0.113.7', **kw: Any):  # noqa: ANN201
        headers = {'cf-connecting-ip': caller, **kw.pop('headers', {})}
        if token is not None:
            headers['authorization'] = f'Bearer {token}'
        if 'content' in kw:
            return self.hub.client.post(URL, headers=headers, content=kw.pop('content'))
        return self.hub.client.post(URL, headers=headers, json=body)

    def events(self, task_id: str) -> list[str]:
        return [e.type.value for e in self.hub.task_events(task_id, 'progress', 'artifact')]

    def milestones(self) -> list[str]:
        return [
            m.body_json['milestone']['event']
            for m in self.hub.messages()
            if m.kind.value == 'milestone'
        ]


@pytest.fixture
def rh(tmp_path: Path) -> Iterator[ReportHub]:
    with channel_hub(tmp_path) as hub:
        yield ReportHub(hub)


def unknown_address(rh: ReportHub) -> dict[str, Any]:
    res = rh.hub.client.post('/api/session/nothing-here', json={})
    assert res.status_code == 404
    return res.json()


def is_hidden(rh: ReportHub, res: Any) -> bool:
    return res.status_code == 404 and res.json() == HIDDEN == unknown_address(rh)


# Reports land as a channel `report` would ------------------------------------------------------


def test_reports_land_on_the_tokens_task_like_a_channel_report(rh: ReportHub) -> None:
    task = rh.spawn()
    token = rh.token()
    res = rh.post(token, {'kind': 'started', 'text': 'Cloning the repo.'})
    assert res.status_code == 202 and res.json() == {'task_id': task.id}
    ACCEPTED.validate(res.json())
    assert rh.registry.get(task.id).state == TaskState.working

    res = rh.post(
        token, {'kind': 'progress', 'text': 'Found it.', 'next': ['Fix the wait', 'Open a PR']}
    )
    assert res.status_code == 202
    progress = rh.hub.task_events(task.id, 'progress')[-1]
    assert progress.data_json == {'text': 'Found it.', 'next': ['Fix the wait', 'Open a PR']}

    branch = {'kind': 'branch', 'title': 'claude/fix-login', 'url': 'https://github.com/x/y'}
    assert rh.post(token, {'kind': 'artifact', 'artifact': branch}).status_code == 202

    res = rh.post(
        token,
        {
            'kind': 'done',
            'summary': 'Fixed the flaky login test.',
            'artifacts': [{'kind': 'pr', 'title': 'Fix flaky login test', 'url': PR_URL}],
        },
    )
    assert res.status_code == 202
    done = rh.registry.get(task.id)
    assert (done.state, done.summary) == (TaskState.done, 'Fixed the flaky login test.')
    with Session(rh.hub.engine) as s:
        [pr] = s.exec(select(Artifact).where(Artifact.kind == ArtifactKind.pr)).all()
    assert (pr.task_id, pr.url) == (task.id, PR_URL)
    assert rh.milestones() == ['started', 'done']


def test_a_note_goes_to_the_projects_notes_and_posts_nothing(rh: ReportHub) -> None:
    task = rh.spawn()
    res = rh.post(rh.token(), {'kind': 'note', 'text': FACT})
    assert res.status_code == 202, res.text
    inbox = rh.hub.app.state.memory.root / 'projects' / 'inbox.md'
    assert inbox.read_text().endswith(f'{FACT} (from Fix flaky login test)\n')
    assert rh.milestones() == []
    assert rh.registry.get(task.id).state == TaskState.starting


@pytest.mark.parametrize(
    ('code', 'status'),
    [('too_many_notes', 409), ('notes_full', 409), ('memory_unavailable', 503)],
)
def test_a_note_the_hub_cannot_keep_answers_its_code(
    rh: ReportHub, monkeypatch: pytest.MonkeyPatch, code: str, status: int
) -> None:
    rh.spawn()

    async def refuse(_peer: Any, frame: dict[str, Any]) -> list[dict[str, Any]]:
        error = {'code': code, 'message': 'Put the fact in your done summary instead.'}
        return [{'type': 'call_ack', 'call_id': frame['call_id'], 'error': error}]

    monkeypatch.setattr(rh.hub.gateway, 'handle', refuse)
    res = rh.post(rh.token(), {'kind': 'note', 'text': FACT})
    assert res.status_code == status and res.json()['code'] == code
    ERROR.validate(res.json())
    assert 'done summary' in res.json()['message']


@pytest.mark.parametrize(
    ('body', 'code', 'said'),
    [
        ({'kind': 'note', 'text': 'x' * 1001}, 'bad_note', 'done summary instead'),
        ({'kind': 'note', 'text': '   '}, 'bad_note', 'done summary instead'),
        ({'kind': 'note'}, 'bad_note', 'done summary instead'),
        ({'kind': 'progress', 'text': 'x', 'next': ['s'] * 21}, 'bad_next', 'next'),
        ({'kind': 'progress', 'text': 'x', 'next': ['']}, 'bad_next', 'next'),
        ({'kind': 'progress'}, 'bad_report', 'needs "text"'),
        ({'kind': 'done'}, 'bad_report', 'needs "summary"'),
        ({'kind': 'done', 'summary': 's', 'task_id': 'tsk_other'}, 'bad_report', 'task_id'),
        (['not', 'a', 'report'], 'bad_report', 'Check it'),
    ],
)
def test_a_report_that_does_not_match_says_what_to_change_and_stores_nothing(
    rh: ReportHub, body: Any, code: str, said: str
) -> None:
    task = rh.spawn()
    res = rh.post(rh.token(), body)
    assert res.status_code == 400 and res.json()['code'] == code, res.text
    assert said in res.json()['message']
    ERROR.validate(res.json())
    assert rh.registry.get(task.id).state == TaskState.starting
    assert rh.events(task.id) == []


def test_a_body_that_is_not_json_or_too_big_is_refused(rh: ReportHub) -> None:
    task = rh.spawn()
    res = rh.post(rh.token(), content=b'{not json')
    assert res.status_code == 400 and res.json()['code'] == 'bad_report'
    huge = {'kind': 'progress', 'text': 'x' * (report_api.MAX_BODY + 1)}
    res = rh.post(rh.token(), huge)
    assert res.status_code == 400 and 'too big' in res.json()['message']
    assert rh.events(task.id) == []


# The token: wrong task, expired, spent ---------------------------------------------------------


@pytest.mark.parametrize(
    'authorization',
    [None, 'Bearer', 'Bearer mrt_' + '0' * 64, 'Basic mrt_' + '0' * 64, 'Bearer nope'],
)
def test_no_good_token_is_the_hidden_404_even_with_a_broken_body(
    rh: ReportHub, authorization: str | None
) -> None:
    task = rh.spawn()
    headers = {'authorization': authorization} if authorization else {}
    res = rh.post(None, content=b'{broken', headers=headers)
    assert is_hidden(rh, res)
    assert rh.events(task.id) == []


def test_a_token_reports_only_for_its_own_task(rh: ReportHub) -> None:
    mine = rh.spawn()
    other = rh.spawn()
    res = rh.post(rh.token(0), {'kind': 'progress', 'text': 'Working on mine.'})
    assert res.json() == {'task_id': mine.id}
    assert rh.events(mine.id) == ['progress'] and rh.events(other.id) == []
    # A token minted for a task that is not there is refused.
    assert is_hidden(rh, rh.post(rh.cloud.tokens.mint('tsk_gone'), {'kind': 'started'}))


def test_an_expired_token_is_refused(rh: ReportHub) -> None:
    task = rh.spawn()
    old = rh.cloud.tokens.mint(task.id, utcnow() - timedelta(hours=48, seconds=1))
    assert is_hidden(rh, rh.post(old, {'kind': 'progress', 'text': 'Late.'}))
    assert rh.events(task.id) == []
    fresh = rh.cloud.tokens.mint(task.id, utcnow() - timedelta(hours=47))
    assert rh.post(fresh, {'kind': 'progress', 'text': 'In time.'}).status_code == 202


@pytest.mark.parametrize('end', ['done', 'failed'])
def test_a_replay_after_done_or_failed_is_refused(rh: ReportHub, end: str) -> None:
    task = rh.spawn()
    token = rh.token()
    end_report = {'kind': end, 'summary': 'Over.'}
    assert rh.post(token, end_report).status_code == 202
    assert is_hidden(rh, rh.post(token, end_report))  # the same report, retried
    assert is_hidden(rh, rh.post(token, {'kind': 'progress', 'text': 'One more thing.'}))
    assert rh.events(task.id) == []
    assert rh.milestones() == [end]


def test_a_report_after_a_stop_is_refused(rh: ReportHub) -> None:
    task = rh.spawn()
    token = rh.token()
    run(rh.registry.stop(task.id))
    assert rh.registry.get(task.id).state == TaskState.stopped
    assert is_hidden(rh, rh.post(token, {'kind': 'progress', 'text': 'Still here.'}))


def test_earlier_tokens_work_until_the_task_finishes(rh: ReportHub) -> None:
    task = rh.spawn()
    first = rh.token()
    second = rh.cloud.tokens.mint(task.id)  # as a follow-up would
    assert rh.post(first, {'kind': 'progress', 'text': 'One.'}).status_code == 202
    assert rh.post(second, {'kind': 'done', 'summary': 'Done.'}).status_code == 202
    assert is_hidden(rh, rh.post(first, {'kind': 'progress', 'text': 'Two.'}))


def test_a_finished_task_refuses_a_token_it_never_spent(rh: ReportHub) -> None:
    task = rh.spawn()
    token = rh.token()
    with Session(rh.hub.engine) as s:
        row = s.get(Task, task.id)
        assert row is not None
        row.state = TaskState.done
        s.add(row)
        s.commit()
    assert is_hidden(rh, rh.post(token, {'kind': 'progress', 'text': 'Hm.'}))


@pytest.mark.parametrize('method', ['GET', 'PUT', 'DELETE', 'PATCH'])
def test_other_methods_and_a_trailing_slash_are_the_hidden_404(rh: ReportHub, method: str) -> None:
    rh.spawn()
    res = rh.hub.client.request(method, URL, headers={'authorization': f'Bearer {rh.token()}'})
    assert is_hidden(rh, res)
    slash = rh.hub.client.post(URL + '/', headers={'authorization': f'Bearer {rh.token()}'})
    assert is_hidden(rh, slash)


# Limits ----------------------------------------------------------------------------------------


def test_wrong_tokens_are_limited_per_caller_and_a_good_token_still_gets_in(
    rh: ReportHub, caplog: pytest.LogCaptureFixture
) -> None:
    rh.spawn()
    wrong = 'mrt_' + 'f' * 64
    for _ in range(MAX_WRONG_TOKENS):
        assert rh.post(wrong, {}).status_code == 404
    with caplog.at_level(logging.WARNING, logger='marcel_hub.report_api'):
        locked = rh.post(wrong, {})
        assert rh.post(wrong, {}).status_code == 429
    assert locked.status_code == 429 and locked.json()['code'] == 'too_many_requests'
    assert locked.headers['retry-after'] == '60'
    ERROR.validate(locked.json())
    assert [r.message for r in caplog.records].count(caplog.records[0].message) == 1
    assert 'Too many wrong report tokens from 203.0.113.7' in caplog.records[0].message
    # Another caller is not locked out, and the right token always gets in.
    assert rh.post(wrong, {}, caller='198.51.100.1').status_code == 404
    assert rh.post(rh.token(), {'kind': 'started'}).status_code == 202
    # A minute later the caller may try again.
    rh.clock.now += 61
    assert rh.post(wrong, {}).status_code == 404
    assert '203.0.113.7' in rh.gate.failures


def test_wrong_tokens_from_everyone_together_have_a_ceiling(
    rh: ReportHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(report_api, 'WRONG_TOKEN_CEILING', 3)
    wrong = 'mrt_' + 'e' * 64
    for n in range(3):
        assert rh.post(wrong, {}, caller=f'192.0.2.{n}').status_code == 404
    assert rh.post(wrong, {}, caller='192.0.2.99').status_code == 429
    rh.spawn()
    assert rh.post(rh.token(), {'kind': 'started'}, caller='192.0.2.99').status_code == 202


def test_the_callers_remembered_are_bounded_and_quiet_ones_are_forgotten(
    rh: ReportHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(report_api, 'MAX_CALLERS', 2)
    wrong = 'mrt_' + 'd' * 64
    for n in range(4):
        rh.post(wrong, {}, caller=f'192.0.2.{n}')
    assert set(rh.gate.failures) == {'192.0.2.0', '192.0.2.1'}
    rh.clock.now += 61
    rh.post(wrong, {}, caller='192.0.2.9')
    assert set(rh.gate.failures) == {'192.0.2.9'}


def test_a_task_that_reports_too_often_is_told_to_wait(rh: ReportHub) -> None:
    task = rh.spawn()
    token = rh.token()
    for n in range(MAX_REPORTS):
        assert rh.post(token, {'kind': 'progress', 'text': f'Step {n}.'}).status_code == 202
    res = rh.post(token, {'kind': 'progress', 'text': 'One too many.'})
    assert res.status_code == 429 and res.json()['code'] == 'too_many_reports'
    assert len(rh.events(task.id)) == MAX_REPORTS
    rh.clock.now += 61
    assert rh.post(token, {'kind': 'progress', 'text': 'Later.'}).status_code == 202


# A retried report is taken once ----------------------------------------------------------------


def test_the_same_report_sent_twice_is_taken_once(rh: ReportHub) -> None:
    task = rh.spawn()
    token = rh.token()
    started = {'kind': 'started', 'text': 'Cloning.'}
    assert rh.post(token, started).status_code == 202
    again = rh.post(token, {'text': 'Cloning.', 'kind': 'started'})  # key order aside
    assert again.status_code == 202 and again.json() == {'task_id': task.id}
    assert rh.milestones() == ['started']
    assert len(rh.hub.task_events(task.id, 'progress')) == 1
    # A different report is new; the same one after the window is new too.
    assert rh.post(token, {'kind': 'progress', 'text': 'Next.'}).status_code == 202
    rh.clock.now += REPEAT_WINDOW + 1
    assert rh.post(token, started).status_code == 202
    assert rh.milestones() == ['started', 'started']


def test_a_refused_report_can_be_sent_again(rh: ReportHub, monkeypatch: pytest.MonkeyPatch) -> None:
    task = rh.spawn()
    token = rh.token()
    real = rh.hub.gateway.handle

    async def busy(_peer: Any, frame: dict[str, Any]) -> list[dict[str, Any]]:
        error = {'code': 'memory_unavailable', 'message': 'Not now.'}
        return [{'type': 'call_ack', 'call_id': frame['call_id'], 'error': error}]

    monkeypatch.setattr(rh.hub.gateway, 'handle', busy)
    assert rh.post(token, {'kind': 'note', 'text': FACT}).status_code == 503
    monkeypatch.setattr(rh.hub.gateway, 'handle', real)
    assert rh.post(token, {'kind': 'note', 'text': FACT}).status_code == 202
    assert rh.registry.get(task.id).state == TaskState.starting


def test_the_reports_remembered_per_task_are_bounded(
    rh: ReportHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(report_api, 'MAX_REMEMBERED', 3)
    task = rh.spawn()
    token = rh.token()
    for n in range(5):
        rh.clock.now += 1
        rh.post(token, {'kind': 'progress', 'text': f'Step {n}.'})
    assert len(rh.gate.seen[task.id]) == 3


def test_the_same_report_twice_at_once_is_taken_once(
    rh: ReportHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = rh.spawn()
    started = {'kind': 'started', 'text': 'Cloning.'}
    real = rh.hub.gateway.handle

    async def slow(peer: Any, frame: dict[str, Any]) -> list[dict[str, Any]]:
        await asyncio.sleep(0.01)  # the second report arrives while the first is stored
        return await real(peer, frame)

    monkeypatch.setattr(rh.hub.gateway, 'handle', slow)

    async def both() -> None:
        await asyncio.gather(rh.gate.take(task.id, started), rh.gate.take(task.id, dict(started)))

    run(both())
    assert rh.milestones() == ['started']
