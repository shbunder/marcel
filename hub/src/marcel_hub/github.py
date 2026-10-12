"""PR status from GitHub (S-04.12, ADR-002): a read-only client and the loop that keeps PR cards
current.

`GitHubPrStatus` is the store's `PrStatusSource`. It reads a PR, its head commit's check runs and
its reviews from GitHub's REST API with `MARCEL_GITHUB_TOKEN` and maps them with
`pr_status_from_gh`. It only ever calls `https://api.github.com`: a PR URL comes from a worker and
is untrusted, so it must be exactly `https://github.com/<owner>/<repo>/pull/<n>`; anything else is
refused in plain words and nothing is sent.

The token is a secret. It is sent only in the `Authorization` header (never in a URL, which httpx
logs), never logged, never put in an error message or an artifact, and hidden from `repr`.

`PrRefresher` refreshes each open PR artifact when it is made, then every `REFRESH_S` while the PR
is open. The refresh that sees it merged or closed is its last. At start it finds every open PR
artifact again, so a hub restart loses none. A rate-limit answer pauses all GitHub calls (one
token, one limit) until GitHub says, at most `MAX_BACKOFF_S`. When a refresh fails, the last known
status stays and the reason is kept on the artifact as `meta_json['pr_note']`; the app hears of a
PR only when its state, checks or review really change (`ArtifactStore.refresh_pr`).
"""

import asyncio
import contextlib
import email.utils
import logging
import re
import time
from collections.abc import Callable, Generator
from typing import Any
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI
from sqlmodel import Session, select

from marcel_hub.artifacts import (
    ArtifactNotFound,
    ArtifactRefused,
    ArtifactStore,
    PrStatusUnavailable,
    pr_status_from_gh,
)
from marcel_hub.models import Artifact, ArtifactKind

log = logging.getLogger(__name__)

API = 'https://api.github.com'
REFRESH_S = 5 * 60  # while a PR is open (ADR-002)
TICK_S = 5.0  # how often the loop looks for due PRs
TIMEOUT_S = 10.0
DEFAULT_BACKOFF_S = 60.0  # no hint when to come back; also the shortest wait
MAX_BACKOFF_S = 60 * 60  # GitHub's primary limit resets within the hour
OPEN_STATES = {'open', 'draft'}

# GitHub's own rules: an owner is 1-39 letters, digits or single hyphens, not starting with one;
# a repository is letters, digits, '.', '_' and '-' (but never '.' or '..').
OWNER = re.compile(r'[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}')
REPO = re.compile(r'[A-Za-z0-9._-]{1,100}')
NUMBER = re.compile(r'[1-9][0-9]{0,9}')

NO_TOKEN = (
    'The hub has no GitHub token (MARCEL_GITHUB_TOKEN is not set), so it cannot check this '
    "PR's checks and reviews."
)
NETWORK_DOWN = (
    'The hub cannot reach GitHub right now (no network, or GitHub is down). '
    'It tries again in a few minutes.'
)


class RateLimited(PrStatusUnavailable):
    """GitHub asked the hub to slow down. `wait` is seconds until it may ask again."""

    def __init__(self, wait: float) -> None:
        self.wait = wait
        minutes = max(1, round(wait / 60))
        super().__init__(
            'GitHub asked the hub to wait (too many requests), so this PR is checked again in '
            f'about {minutes} minute{"s" if minutes != 1 else ""}.'
        )


def parse_pr_url(url: str) -> tuple[str, str, int]:
    """`https://github.com/<owner>/<repo>/pull/<n>` → (owner, repo, n). Raises
    `PrStatusUnavailable` for anything else, so only GitHub's API host is ever asked."""
    refused = PrStatusUnavailable(
        'This PR link is not a GitHub pull request address the hub can read (it must look '
        'like https://github.com/owner/repo/pull/12), so its checks are not refreshed.'
    )
    try:
        parts = urlsplit(url)
    except ValueError:
        parts = None
    if parts is None:  # raised out here, so the error has no context
        raise refused
    if (
        parts.scheme != 'https'
        or parts.netloc.lower() != 'github.com'  # exactly: no other host, user, password or port
        or parts.query
        or parts.fragment
    ):
        raise refused
    segments = parts.path.removesuffix('/').split('/')
    if len(segments) != 5 or segments[0] != '' or segments[3] != 'pull':
        raise refused
    owner, repo, number = segments[1], segments[2], segments[4]
    if (
        not OWNER.fullmatch(owner)
        or not REPO.fullmatch(repo)
        or repo in ('.', '..')
        or not NUMBER.fullmatch(number)
    ):
        raise refused
    return owner, repo, int(number)


class _Bearer(httpx.Auth):
    """Adds the token to each request. Its repr never shows the token."""

    def __init__(self, token: str) -> None:
        self._token = token

    def auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response, None]:
        request.headers['Authorization'] = f'Bearer {self._token}'
        yield request

    def __repr__(self) -> str:
        return '<GitHub token hidden>'


class GitHubPrStatus:
    """`PrStatusSource` backed by GitHub's REST API, read-only. `wall` is the clock GitHub's
    `X-RateLimit-Reset` is compared with (seconds since the epoch)."""

    def __init__(
        self,
        token: str | None,
        client: httpx.AsyncClient | None = None,
        wall: Callable[[], float] = time.time,
    ) -> None:
        self._auth = _Bearer(token) if token else None
        self.client = client or httpx.AsyncClient(timeout=TIMEOUT_S)
        self.wall = wall

    def __repr__(self) -> str:
        return f'<GitHubPrStatus token={"set" if self._auth else "missing"}>'

    async def fetch(self, url: str) -> dict[str, Any]:
        owner, repo, number = parse_pr_url(url)
        if self._auth is None:
            raise PrStatusUnavailable(NO_TOKEN)
        where = f'{owner}/{repo}'
        pull = await self._get(f'/repos/{owner}/{repo}/pulls/{number}', where, number)
        if not isinstance(pull, dict):
            raise PrStatusUnavailable('GitHub sent an answer about this PR the hub cannot read.')
        sha = (pull.get('head') or {}).get('sha')
        runs: Any = {}
        if isinstance(sha, str) and re.fullmatch(r'[0-9a-f]{40}', sha):
            runs = await self._get(
                f'/repos/{owner}/{repo}/commits/{sha}/check-runs?per_page=100', where, number
            )
        reviews = await self._get(
            f'/repos/{owner}/{repo}/pulls/{number}/reviews?per_page=100', where, number
        )
        if not isinstance(runs, dict) or not isinstance(reviews, list):
            raise PrStatusUnavailable('GitHub sent an answer about this PR the hub cannot read.')
        return pr_status_from_gh(pull, runs, reviews)

    async def _get(self, path: str, where: str, number: int) -> Any:
        headers = {
            'Accept': 'application/vnd.github+json',
            'X-GitHub-Api-Version': '2022-11-28',
            'User-Agent': 'marcel-hub',
        }
        # The httpx error holds the request, and the request holds the token: it is dropped, and
        # the hub's own error is raised outside the `except`, so it has no `__context__` at all.
        res: httpx.Response | None = None
        with contextlib.suppress(httpx.HTTPError):
            res = await self.client.get(API + path, headers=headers, auth=self._auth)
        if res is None:
            raise PrStatusUnavailable(NETWORK_DOWN)
        code = res.status_code
        if code == 429 or (code == 403 and self._rate_limited(res)):
            raise RateLimited(self.backoff(res))
        if code == 401:
            raise PrStatusUnavailable(
                "GitHub refused the hub's token: it was revoked or has expired. Make a new "
                'read-only token and put it in MARCEL_GITHUB_TOKEN.'
            )
        if code == 403:
            raise PrStatusUnavailable(
                f"GitHub refused the hub's token for {where}: it may not be allowed to read pull "
                'requests and checks there, or it was revoked.'
            )
        if code == 404:
            raise PrStatusUnavailable(
                f"GitHub cannot find PR #{number} in {where} with the hub's token: the repository "
                'may be private and the token not allowed to read it, or the PR is gone.'
            )
        if code >= 500:
            raise PrStatusUnavailable(
                f'GitHub is having trouble (it answered {code}). '
                'The hub tries again in a few minutes.'
            )
        if code >= 300:
            raise PrStatusUnavailable(f'GitHub did not answer about this PR (it answered {code}).')
        unread = object()
        body: Any = unread
        with contextlib.suppress(ValueError):
            body = res.json()
        if body is unread:
            raise PrStatusUnavailable('GitHub sent an answer about this PR the hub cannot read.')
        return body

    @staticmethod
    def _rate_limited(res: httpx.Response) -> bool:
        """A 403 that is GitHub's primary or secondary rate limit, not a refusal of the token."""
        if 'retry-after' in res.headers or res.headers.get('x-ratelimit-remaining') == '0':
            return True
        return 'rate limit' in res.text[:2000].lower()

    def backoff(self, res: httpx.Response) -> float:
        """Seconds to wait, from `Retry-After` (seconds or a date), else `X-RateLimit-Reset`,
        else `DEFAULT_BACKOFF_S`; never more than `MAX_BACKOFF_S`, and never less than
        `DEFAULT_BACKOFF_S` (a reset time in the past, from clock skew, must not mean a retry on
        every tick)."""
        wait: float | None = None
        retry_after = res.headers.get('retry-after', '').strip()
        if retry_after.isdigit():
            wait = float(retry_after)
        elif retry_after:
            with contextlib.suppress(TypeError, ValueError, IndexError):
                wait = email.utils.parsedate_to_datetime(retry_after).timestamp() - self.wall()
        if wait is None:
            reset = res.headers.get('x-ratelimit-reset', '').strip()
            if reset.isdigit():
                wait = float(reset) - self.wall()
        if wait is None:
            wait = DEFAULT_BACKOFF_S
        return min(max(wait, DEFAULT_BACKOFF_S), MAX_BACKOFF_S)

    async def aclose(self) -> None:
        await self.client.aclose()


class PrRefresher:
    """Keeps PR artifacts current. `clock` is seconds (monotonic)."""

    def __init__(
        self,
        store: ArtifactStore,
        clock: Callable[[], float] = time.monotonic,
        interval: float = REFRESH_S,
    ) -> None:
        self.store = store
        self.clock = clock
        self.interval = interval
        self.paused_until = 0.0  # set by a rate-limit answer; no GitHub call before it
        self._due: dict[str, float] = {}  # artifact id → when to refresh it next
        self._seen: dict[str, int] = {}  # artifact id → the version last refreshed
        self._task: asyncio.Task[None] | None = None

    # Which PRs ------------------------------------------------------------------------------

    def discover(self) -> list[str]:
        """At start: every PR artifact still open (or never refreshed), due now."""
        with Session(self.store.engine) as s:
            rows = s.exec(select(Artifact).where(Artifact.kind == ArtifactKind.pr)).all()
        found = [a.id for a in rows if a.meta_json.get('pr', {}).get('state') in OPEN_STATES]
        for artifact_id in found:
            self._due[artifact_id] = self.clock()
        return found

    def due(self) -> dict[str, float]:
        return dict(self._due)

    def observe(self, _user_id: str, type_: str, data: dict[str, Any]) -> None:
        """`EventLog` observer: a new PR artifact, or a new version of one (its URL may have
        changed), is refreshed at once. The hub's own refresh keeps the version, so it never
        marks itself again."""
        if type_ == 'artifact.created':
            art = data
        elif type_ == 'artifact.updated':
            art = data['artifact']
        else:
            return
        if art.get('kind') != ArtifactKind.pr.value:
            return
        artifact_id, version = art['id'], art.get('current_version', 1)
        if self._seen.get(artifact_id) != version:
            self._due[artifact_id] = self.clock()

    # Refreshing -----------------------------------------------------------------------------

    async def flush(self) -> list[str]:
        """Refresh every PR that is due, unless GitHub asked the hub to wait. Returns their ids."""
        done: list[str] = []
        now = self.clock()
        for artifact_id in sorted(a for a, at in self._due.items() if at <= now):
            if self.clock() < self.paused_until:
                break
            await self.refresh(artifact_id)
            done.append(artifact_id)
        return done

    async def refresh(self, artifact_id: str) -> None:
        """Refresh one PR and decide when to look again: in `interval` while it is open, never
        after it merged or closed."""
        try:
            art = self.store.get(artifact_id)
            self._seen[artifact_id] = art.current_version
            row = await self.store.refresh_pr(artifact_id)
        except (ArtifactNotFound, ArtifactRefused):
            self._forget(artifact_id)
            return
        except RateLimited as exc:
            self.paused_until = self.clock() + exc.wait
            self._due[artifact_id] = self.paused_until
            self._note(artifact_id, str(exc))
            return
        except PrStatusUnavailable as exc:
            self._due[artifact_id] = self.clock() + self.interval
            self._note(artifact_id, str(exc))
            return
        except Exception:  # one broken PR must not stop the others
            log.exception('Could not refresh PR artifact %s; trying again later.', artifact_id)
            self._due[artifact_id] = self.clock() + self.interval
            return
        self._note(artifact_id, None)
        if row.meta_json.get('pr', {}).get('state') in OPEN_STATES:
            self._due[artifact_id] = self.clock() + self.interval
        else:
            self._forget(artifact_id)  # that was the refresh that saw it close: the last one

    def _forget(self, artifact_id: str) -> None:
        self._due.pop(artifact_id, None)
        self._seen.pop(artifact_id, None)

    def _note(self, artifact_id: str, text: str | None) -> None:
        """Keep why the status is stale on the artifact; log it when it changes. The texts are
        the client's own and never hold the token."""
        try:
            art = self.store.get(artifact_id)
            if art.meta_json.get('pr_note') == text:
                return
            self.store.set_meta(artifact_id, pr_note=text)
        except ArtifactNotFound:
            return
        if text is None:
            log.info('PR artifact %s is up to date with GitHub again.', artifact_id)
        else:
            log.warning('PR artifact %s was not refreshed: %s', artifact_id, text)

    # Running --------------------------------------------------------------------------------

    def start(self, tick: float | None = None) -> None:
        """Find the open PRs, then refresh due ones every `tick` seconds (`TICK_S`) on the
        running loop."""
        self.discover()
        self._task = asyncio.get_running_loop().create_task(self._run(tick or TICK_S))

    async def _run(self, tick: float) -> None:
        while True:
            await asyncio.sleep(tick)
            try:
                await self.flush()
            except Exception:  # the loop must outlive any one bad pass
                log.exception('A PR refresh pass failed; the next one runs in %s s.', tick)

    async def stop(self) -> None:
        """Stop the loop (a refresh in flight is cancelled; its PR is found again at the next
        start) and close the GitHub client."""
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        if isinstance(self.store.pr_source, GitHubPrStatus):
            await self.store.pr_source.aclose()


def install(app: FastAPI, token: str | None) -> PrRefresher:
    """Give the artifact store a GitHub client when there is a token (without one it keeps
    `NoPrStatus`, whose answer says the token is missing) and watch the event log for new PR
    artifacts. Call after `artifacts.install`; the app's lifespan starts and stops the refresher."""
    store: ArtifactStore = app.state.artifacts
    if token:
        store.pr_source = GitHubPrStatus(token)
    refresher = PrRefresher(store)
    app.state.events.observers.append(refresher.observe)
    app.state.pr_refresher = refresher
    return refresher
