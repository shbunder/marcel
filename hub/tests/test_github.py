"""PR status refresh (S-04.12, ADR-002): the GitHub client and the loop that keeps PR cards current.

GitHub is never reached: `FakeGitHub` answers through `httpx.MockTransport`, like the hub's other
HTTP tests. Events are checked against contracts/events.schema.json.
"""

import asyncio
import logging
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from test_tasks_fakes import RecordingPublisher, add_agent, run, validator

from marcel_hub import github
from marcel_hub.app import Settings, create_app, load_settings
from marcel_hub.artifacts import ArtifactStore, Content, NoPrStatus, PrStatusUnavailable
from marcel_hub.github import (
    MAX_BACKOFF_S,
    GitHubPrStatus,
    PrRefresher,
    RateLimited,
    parse_pr_url,
)

UPDATED = validator('events.schema.json#/$defs/ArtifactUpdated')
TOKEN = 'github_pat_11SECRETSECRETSECRET_do_not_leak_0123456789'
PR = 'https://github.com/shbunder/marcel/pull/14'
SHA = 'a' * 40
NOW = 1_800_000_000.0  # the wall clock GitHub's reset times are compared with


class FakeGitHub:
    """GitHub's REST API for one PR. Change `pull`, `runs` and `reviews` between refreshes;
    `answer` (a status, 'down' or 'timeout') and `headers` make every request fail that way."""

    def __init__(self) -> None:
        self.pull: dict[str, Any] = {'state': 'open', 'draft': False, 'merged_at': None,
                                     'head': {'sha': SHA}, 'requested_reviewers': []}  # fmt: skip
        self.runs: list[dict[str, Any]] = []
        self.reviews: list[dict[str, Any]] = []
        self.answer: int | str = 200
        self.headers: dict[str, str] = {}
        self.requests: list[httpx.Request] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.answer == 'down':
            raise httpx.ConnectError('Connection refused', request=request)
        if self.answer == 'timeout':
            raise httpx.ReadTimeout('timed out', request=request)
        if self.answer != 200:
            assert isinstance(self.answer, int)
            return httpx.Response(self.answer, headers=self.headers, json={'message': 'no'})
        path = request.url.path
        if path == '/repos/shbunder/marcel/pulls/14':
            return httpx.Response(200, json=self.pull)
        if path == f'/repos/shbunder/marcel/commits/{SHA}/check-runs':
            return httpx.Response(
                200, json={'total_count': len(self.runs), 'check_runs': self.runs}
            )
        if path == '/repos/shbunder/marcel/pulls/14/reviews':
            return httpx.Response(200, json=self.reviews)
        return httpx.Response(404, json={'message': 'Not Found'})

    def source(self, token: str | None = TOKEN) -> GitHubPrStatus:
        client = httpx.AsyncClient(transport=httpx.MockTransport(self.handle))
        return GitHubPrStatus(token, client, wall=lambda: NOW)


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class Watched(RecordingPublisher):
    """The app's events, also handed to the refresher as the event log's observers are."""

    def __init__(self) -> None:
        super().__init__()
        self.refresher: PrRefresher | None = None

    def publish(self, type: str, data: dict[str, Any]) -> None:
        super().publish(type, data)
        if self.refresher is not None:
            self.refresher.observe('usr_1', type, data)

    def updates(self) -> list[dict[str, Any]]:
        found = [d for t, d in self.sent if t == 'artifact.updated']
        for data in found:
            UPDATED.validate({'id': 1, 'type': 'artifact.updated', 'at': '2026-10-12T08:00:00Z',
                              'data': data})  # fmt: skip
        return found


class Rig:
    def __init__(self, engine: Engine, tmp_path: Path, gh: FakeGitHub, token: str | None) -> None:
        add_agent(engine)
        self.gh = gh
        self.events = Watched()
        self.store = ArtifactStore(engine, tmp_path / 'artifacts', publisher=self.events)
        if token is not None:
            self.store.pr_source = gh.source(token)
        self.clock = Clock()
        self.refresher = PrRefresher(self.store, clock=self.clock)
        self.events.refresher = self.refresher

    def add_pr(self, url: str = PR) -> str:
        return self.store.publish_reported({'kind': 'pr', 'title': 'Fix', 'url': url}).id

    def pr(self, artifact_id: str) -> dict[str, Any]:
        return self.store.to_api(self.store.get(artifact_id))['pr']

    def note(self, artifact_id: str) -> str | None:
        return self.store.get(artifact_id).meta_json.get('pr_note')

    def later(self, seconds: float = github.REFRESH_S) -> list[str]:
        self.clock.now += seconds
        return run(self.refresher.flush())


@pytest.fixture
def gh() -> FakeGitHub:
    return FakeGitHub()


@pytest.fixture
def rig(engine: Engine, tmp_path: Path, gh: FakeGitHub) -> Rig:
    return Rig(engine, tmp_path, gh, TOKEN)


# Done when: open → checks pending → passing → merged updates the card each time. -----------


def test_a_pr_card_follows_github_from_open_to_merged(rig: Rig, gh: FakeGitHub) -> None:
    gh.runs = [{'status': 'in_progress', 'conclusion': None}]
    gh.pull['requested_reviewers'] = [{'login': 'shaun'}]
    art = rig.add_pr()

    assert rig.later(0) == [art]  # refreshed as soon as it is made
    assert rig.pr(art) == {'number': 14, 'state': 'open', 'checks': 'pending',
                           'review': 'requested'}  # fmt: skip
    assert len(rig.events.updates()) == 1

    assert rig.later(60) == []  # not due yet: every 5 minutes
    assert rig.later() == [art]  # nothing changed on GitHub: no event
    assert len(rig.events.updates()) == 1

    gh.runs = [{'status': 'completed', 'conclusion': 'success'}]
    gh.reviews = [{'state': 'APPROVED', 'user': {'login': 'shaun'}}]
    rig.later()
    assert rig.pr(art)['checks'] == 'passing'
    assert rig.pr(art)['review'] == 'approved'
    assert len(rig.events.updates()) == 2

    gh.pull.update(state='closed', merged_at='2026-10-12T09:00:00Z')
    rig.later()
    assert rig.pr(art)['state'] == 'merged'
    updates = rig.events.updates()
    assert len(updates) == 3
    assert [u['artifact']['pr']['checks'] for u in updates] == ['pending', 'passing', 'passing']
    assert updates[-1]['artifact']['pr']['state'] == 'merged'
    assert all(u['version']['version'] == 1 for u in updates)  # a refresh is not a new version

    asked = len(gh.requests)
    assert rig.later() == [] and rig.later(3600) == []  # that refresh was its last
    assert len(gh.requests) == asked
    assert rig.refresher.due() == {}


def test_a_closed_pr_gets_one_last_refresh(rig: Rig, gh: FakeGitHub) -> None:
    art = rig.add_pr()
    rig.later(0)
    gh.pull['state'] = 'closed'
    rig.later()
    assert rig.pr(art)['state'] == 'closed'
    assert rig.refresher.due() == {}


def test_a_draft_pr_is_still_refreshed(rig: Rig, gh: FakeGitHub) -> None:
    gh.pull['draft'] = True
    art = rig.add_pr()
    rig.later(0)
    assert rig.pr(art)['state'] == 'draft'
    assert art in rig.refresher.due()


def test_the_client_asks_for_the_pull_its_checks_and_its_reviews(rig: Rig, gh: FakeGitHub) -> None:
    rig.add_pr()
    rig.later(0)
    assert [r.url.path for r in gh.requests] == [
        '/repos/shbunder/marcel/pulls/14',
        f'/repos/shbunder/marcel/commits/{SHA}/check-runs',
        '/repos/shbunder/marcel/pulls/14/reviews',
    ]
    for r in gh.requests:
        assert r.method == 'GET'  # read-only
        assert r.url.host == 'api.github.com' and r.url.scheme == 'https'
        assert r.headers['authorization'] == f'Bearer {TOKEN}'
        assert TOKEN not in str(r.url)


def test_a_head_without_a_sha_has_no_checks(rig: Rig, gh: FakeGitHub) -> None:
    gh.pull['head'] = {'sha': '../../etc'}
    art = rig.add_pr()
    rig.later(0)
    assert rig.pr(art)['checks'] == 'none'
    assert len(gh.requests) == 2  # never a check-runs call built from a strange sha


# A restart finds every open PR again; shutdown is clean. ------------------------------------


def test_after_a_restart_open_prs_are_found_again(
    engine: Engine, tmp_path: Path, gh: FakeGitHub
) -> None:
    first = Rig(engine, tmp_path, gh, TOKEN)
    open_pr = first.add_pr()
    merged = first.add_pr('https://github.com/shbunder/marcel/pull/15')
    first.store.set_meta(merged, pr={'number': 15, 'state': 'merged'})

    restarted = PrRefresher(first.store, clock=first.clock)  # a new hub: nothing in memory
    assert restarted.discover() == [open_pr]
    assert run(restarted.flush()) == [open_pr]
    assert first.pr(open_pr)['checks'] == 'none'


def test_the_loop_runs_and_stops_cleanly(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    art = rig.add_pr()
    rig.refresher.clock = lambda: 10.0**9  # everything is due

    async def go() -> None:
        rig.refresher.start(tick=0.01)
        for _ in range(200):
            if rig.store.get(art).meta_json['pr'].get('checks'):
                break
            await asyncio.sleep(0.01)
        await rig.refresher.stop()

    run(go())
    assert rig.pr(art)['checks'] == 'none'
    assert rig.refresher._task is None
    assert rig.store.pr_source.client.is_closed  # type: ignore[attr-defined]


def test_a_broken_pass_does_not_stop_the_loop(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    calls: list[int] = []

    async def broken() -> list[str]:
        calls.append(1)
        raise RuntimeError('boom')

    monkeypatch.setattr(rig.refresher, 'flush', broken)

    async def go() -> None:
        rig.refresher.start(tick=0.01)
        while len(calls) < 3:
            await asyncio.sleep(0.01)
        await rig.refresher.stop()

    run(go())
    assert 'A PR refresh pass failed' in caplog.text


def test_an_unexpected_error_on_one_pr_is_logged_and_retried(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    art = rig.add_pr()

    async def explode(_id: str) -> Any:
        raise RuntimeError('database locked')

    monkeypatch.setattr(rig.store, 'refresh_pr', explode)
    rig.later(0)
    assert 'Could not refresh PR artifact' in caplog.text
    assert art in rig.refresher.due()


def test_an_artifact_that_is_gone_is_forgotten(rig: Rig) -> None:
    rig.refresher.observe('usr_1', 'artifact.created', {'id': 'art_gone', 'kind': 'pr'})
    rig.later(0)
    assert rig.refresher.due() == {}


def test_only_pr_artifacts_and_their_new_versions_are_watched(rig: Rig, gh: FakeGitHub) -> None:
    rig.refresher.observe('usr_1', 'task.updated', {'id': 'tsk_1'})
    rig.refresher.observe('usr_1', 'artifact.created', {'id': 'art_l', 'kind': 'link'})
    assert rig.refresher.due() == {}
    art = rig.add_pr()
    rig.later(0)
    due = rig.refresher.due()[art]
    # A worker hands in a new version of the PR (e.g. a new URL): refreshed at once.
    rig.store.add_version(art, Content(url=PR))
    assert rig.refresher.due()[art] < due


# Done when: a rate-limit answer backs off. -------------------------------------------------


@pytest.mark.parametrize(
    ('status', 'headers', 'wait'),
    [
        (429, {'retry-after': '120'}, 120),
        (403, {'retry-after': '30'}, 30),
        (403, {'x-ratelimit-remaining': '0', 'x-ratelimit-reset': str(int(NOW) + 900)}, 900),
        (429, {}, github.DEFAULT_BACKOFF_S),
        (429, {'retry-after': '999999'}, MAX_BACKOFF_S),  # capped
        (
            403,
            {'x-ratelimit-remaining': '0', 'x-ratelimit-reset': str(int(NOW) + 10**7)},
            MAX_BACKOFF_S,
        ),  # fmt: skip
        (429, {'retry-after': 'Fri, 15 Jan 2027 08:20:00 GMT'}, 1200),  # a date
        (429, {'x-ratelimit-reset': str(int(NOW) - 50)}, 1),  # already past: a moment
        (429, {'retry-after': 'soon'}, github.DEFAULT_BACKOFF_S),
    ],
)
def test_a_rate_limit_answer_backs_off(
    rig: Rig, gh: FakeGitHub, status: int, headers: dict[str, str], wait: float
) -> None:
    art = rig.add_pr()
    other = rig.add_pr('https://github.com/shbunder/marcel/pull/15')
    gh.answer, gh.headers = status, headers

    rig.later(0)
    assert len(gh.requests) == 1  # the second PR was not asked: one token, one limit
    assert rig.refresher.paused_until == pytest.approx(rig.clock.now + wait)
    asked = min(art, other)  # due PRs go in id order
    assert 'GitHub asked the hub to wait' in (rig.note(asked) or '')
    assert rig.pr(asked)['state'] == 'open' and 'checks' not in rig.pr(asked)  # last known stays

    gh.answer, gh.headers = 200, {}
    if wait > 1:
        rig.later(wait - 1)
        assert len(gh.requests) == 1  # still waiting
    rig.clock.now = rig.refresher.paused_until
    assert set(run(rig.refresher.flush())) == {art, other}
    assert rig.note(asked) is None  # the card no longer says it is stale
    assert rig.pr(art)['checks'] == 'none'


def test_a_rate_limit_message_says_when() -> None:
    assert 'about 2 minutes' in str(RateLimited(120))
    assert 'about 1 minute.' in str(RateLimited(5))


# Done when: a missing token gives the plain message; each failure says what is wrong. ------


def test_without_a_token_the_card_says_so_and_github_is_not_asked(
    engine: Engine, tmp_path: Path, gh: FakeGitHub
) -> None:
    rig = Rig(engine, tmp_path, gh, None)
    assert isinstance(rig.store.pr_source, NoPrStatus)
    art = rig.add_pr()
    rig.later(0)
    assert rig.note(art) == (
        'The hub has no GitHub token (MARCEL_GITHUB_TOKEN is not set), so it cannot check '
        "this PR's checks and reviews."
    )
    assert gh.requests == []
    assert rig.events.updates() == []  # nothing changed on the card's status


def test_a_client_made_without_a_token_says_so(gh: FakeGitHub) -> None:
    with pytest.raises(PrStatusUnavailable, match='MARCEL_GITHUB_TOKEN is not set'):
        run(gh.source(None).fetch(PR))
    assert gh.requests == []


@pytest.mark.parametrize(
    ('answer', 'says'),
    [
        (401, 'revoked or has expired'),
        (403, 'not be allowed to read pull requests and checks there'),
        (404, 'may be private and the token not allowed to read it'),
        (500, 'GitHub is having trouble (it answered 500)'),
        (503, 'GitHub is having trouble (it answered 503)'),
        ('down', 'cannot reach GitHub right now'),
        ('timeout', 'cannot reach GitHub right now'),
        (302, 'it answered 302'),
    ],
)
def test_each_failure_gets_its_own_plain_message(
    rig: Rig, gh: FakeGitHub, answer: int | str, says: str
) -> None:
    art = rig.add_pr()
    rig.later(0)  # first a good refresh
    gh.answer = answer
    rig.later()
    note = rig.note(art) or ''
    assert says in note
    assert rig.pr(art)['checks'] == 'none'  # the last known status stays
    assert len(rig.events.updates()) == 1  # a failure is not a change
    assert art in rig.refresher.due()  # tried again in 5 minutes
    assert rig.refresher.paused_until == 0.0  # only a rate limit pauses everything


def test_an_unreadable_answer_says_so(rig: Rig, gh: FakeGitHub, monkeypatch: Any) -> None:
    def garbage(request: httpx.Request) -> httpx.Response:
        gh.requests.append(request)
        return httpx.Response(200, content=b'<html>')

    monkeypatch.setattr(gh, 'handle', garbage)
    with pytest.raises(PrStatusUnavailable, match='cannot read'):
        run(gh.source().fetch(PR))


@pytest.mark.parametrize('body', [[], {'check_runs': []}])
def test_an_answer_of_the_wrong_shape_says_so(gh: FakeGitHub, body: Any) -> None:
    def wrong(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith('/14'):
            return httpx.Response(200, json=body if isinstance(body, list) else gh.pull)
        return httpx.Response(200, json=body)

    gh.handle = wrong  # type: ignore[method-assign]
    with pytest.raises(PrStatusUnavailable, match='cannot read'):
        run(gh.source().fetch(PR))


def test_a_note_is_logged_once_and_cleared_when_github_answers(
    rig: Rig, gh: FakeGitHub, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger='marcel_hub.github')
    art = rig.add_pr()
    gh.answer = 502
    rig.later(0)
    rig.later()
    assert caplog.text.count('was not refreshed') == 1
    gh.answer = 200
    rig.later()
    assert rig.note(art) is None
    assert 'up to date with GitHub again' in caplog.text


# Only GitHub's API host is ever called: PR URLs are untrusted. -----------------------------


@pytest.mark.parametrize(
    'url',
    [
        'https://evil.example/shbunder/marcel/pull/14',
        'http://github.com/shbunder/marcel/pull/14',
        'https://github.com.evil.example/shbunder/marcel/pull/14',
        'https://user:pw@github.com/shbunder/marcel/pull/14',
        'https://github.com:8443/shbunder/marcel/pull/14',
        'https://api.github.com/repos/shbunder/marcel/pulls/14',
        'https://github.com/shbunder/marcel/pull/14?x=1',
        'https://github.com/shbunder/marcel/pull/14#top',
        'https://github.com/shbunder/../pull/14',
        'https://github.com/shbunder/marcel/pull/0',
        'https://github.com/shbunder/marcel/pull/-1',
        'https://github.com/-bad/marcel/pull/14',
        'https://github.com/shbunder/mar%2Fcel/pull/14',
        'https://github.com/a/b/c/pull/14',
        'https://github.com/shbunder/marcel/issues/14',
        'https://github.com/shbunder/marcel/pull/14/files',
        'https://[::1/shbunder/marcel/pull/14',
        'https://github.com/shbunder/marcel/pull/99999999999',
    ],
)
def test_anything_but_a_github_pr_address_is_refused_without_a_request(
    gh: FakeGitHub, url: str
) -> None:
    with pytest.raises(PrStatusUnavailable, match='not a GitHub pull request address'):
        run(gh.source().fetch(url))
    assert gh.requests == []


def test_a_pr_address_is_read_strictly() -> None:
    assert parse_pr_url('https://github.com/shbunder/marcel/pull/14') == ('shbunder', 'marcel', 14)
    assert parse_pr_url('https://GitHub.com/a-b/c.d_e/pull/7/') == ('a-b', 'c.d_e', 7)


def test_a_refused_address_shows_on_the_card(rig: Rig, gh: FakeGitHub) -> None:
    art = rig.add_pr('https://evil.example/a/b/pull/1')
    rig.later(0)
    assert 'not a GitHub pull request address' in (rig.note(art) or '')
    assert gh.requests == []


# The token is a secret: never in a log line, an error, a repr or an artifact. ---------------


@pytest.mark.parametrize('answer', [200, 401, 403, 404, 429, 500, 'down', 'timeout'])
def test_the_token_never_reaches_a_log_an_error_or_an_artifact(
    rig: Rig, gh: FakeGitHub, caplog: pytest.LogCaptureFixture, answer: int | str
) -> None:
    caplog.set_level(logging.DEBUG)  # every logger, httpx and httpcore included
    art = rig.add_pr()
    gh.answer = answer
    errors: list[BaseException] = []
    try:
        run(rig.store.pr_source.fetch(PR))
    except PrStatusUnavailable as exc:
        errors.append(exc)
    rig.later(0)

    assert gh.requests, 'GitHub was asked'
    assert caplog.records, 'something was logged'
    for record in caplog.records:
        assert TOKEN not in record.getMessage()
        assert TOKEN not in str(record.args)
        assert TOKEN not in str(record.exc_info)
    assert TOKEN not in caplog.text
    for exc in errors:
        assert TOKEN not in str(exc) and TOKEN not in repr(exc)
        assert exc.__cause__ is None  # no httpx error chained
        assert exc.__context__ is None or exc.__suppress_context__
    assert TOKEN not in repr(rig.store.pr_source)
    assert TOKEN not in repr(rig.store.pr_source._auth)  # type: ignore[attr-defined]
    stored = rig.store.get(art)
    assert TOKEN not in str(stored.meta_json) and TOKEN not in str(rig.store.to_api(stored))
    assert all(TOKEN not in str(data) for _, data in rig.events.sent)


def test_an_httpx_error_does_not_carry_the_authorization_header(gh: FakeGitHub) -> None:
    gh.answer = 'down'
    with pytest.raises(PrStatusUnavailable) as err:
        run(gh.source().fetch(PR))
    assert err.value.__context__ is not None  # what httpx raised, hidden by `from None`
    assert TOKEN not in str(err.value.__context__) and TOKEN not in repr(err.value.__context__)
    assert gh.requests[0].headers['authorization'] == f'Bearer {TOKEN}'
    assert TOKEN not in repr(gh.requests[0].headers)  # httpx masks it


def test_the_token_is_a_setting_that_never_shows(tmp_path: Path) -> None:
    settings = load_settings({'MARCEL_CONFIG': str(tmp_path / 'none.toml'),
                              'MARCEL_GITHUB_TOKEN': f' {TOKEN} '})  # fmt: skip
    assert settings.github_token == TOKEN
    assert TOKEN not in repr(settings)
    assert load_settings({'MARCEL_CONFIG': str(tmp_path / 'none.toml')}).github_token is None


# Wiring: the app hands new PR artifacts to the refresher. ----------------------------------


def test_the_app_wires_the_refresher(tmp_path: Path) -> None:
    data = tmp_path / 'data'
    with TestClient(create_app(Settings(data_dir=data, db_path=data / 'marcel.db'))) as client:
        app: Any = client.app
        assert isinstance(app.state.artifacts.pr_source, NoPrStatus)
        add_agent(app.state.engine)
        art = app.state.artifacts.publish_reported({'kind': 'pr', 'title': 'Fix', 'url': PR})
        assert art.id in app.state.pr_refresher.due()
        assert app.state.pr_refresher._task is not None
    assert app.state.pr_refresher._task is None  # stopped with the app

    app = create_app(Settings(data_dir=data, db_path=data / 'marcel.db', github_token=TOKEN))
    assert isinstance(app.state.artifacts.pr_source, GitHubPrStatus)
