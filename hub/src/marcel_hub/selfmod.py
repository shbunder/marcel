"""Self-changes (S-11.1, B-24, ADR-005): Marcel changing its own code, only through a PR that the
owner approves in the app and merges on GitHub.

1. A worker works on `shbunder/marcel` and hands in a PR artifact. The store flags it
   `self_change` from its URL alone (`artifacts.is_self_change_url`): a worker can neither claim
   nor avoid the flag.
2. At the PR's first good refresh (`github.PrRefresher.after_refresh`) the hub reads the PR from
   GitHub and opens an approval card: "Marcel wants to change itself: <PR title>", with "Approve
   change <head sha, 7 characters> into <target branch>" and the diff stats. **An approval covers
   exactly one commit**, the PR's head when the card opened: a push while the card is open replaces
   the card, and a push after approval opens a fresh one. The card outlives the task.
3. **Approve** records the owner's go-ahead and tells them, in plain words, to merge the PR on
   GitHub. The hub never merges: its token is read-only (ADR-002), and a PR merged by Marcel
   itself is forbidden (`.claude/rules/self-modification.md`).
   **Deny** records the no, tells the owner they may close the PR on GitHub, and sends the worker
   that made it a follow-up to close it with its own git credentials if it can.
4. When a refresh sees the PR **merged**, the hub calls `request_redeploy` with the merge commit,
   but only if the card was approved in the app, for this very PR URL, the merged PR's head is the
   commit that was approved, and the PR went into `main` of `shbunder/marcel`. The SHA comes only
   from GitHub's answer for the merged PR (`merge_commit_sha`), and must be 40 hex characters;
   nothing a worker or the owner typed is ever used. Merged without that approval, or into another
   branch: no redeploy and one plain alert. Merged with commits added after the approval: no
   redeploy, one alert, and a fresh card for the merged head (approving it redeploys). Closed
   without merging: the open card expires.

What happened to a self-change is kept on its artifact as `meta_json['self_change']`
(`approval_id`, `url`, `head`, `decision`, `after_merge`, `outcome`, `sha`); once `outcome` is set,
nothing more happens.

`request_redeploy` only writes the flag file. The host's `marcel-redeploy.path` unit does the
rest (S-11.2): pull, rebuild, health check, `git revert` on failure. The hub never restarts
anything itself.
"""

import contextlib
import logging
import os
import re
import secrets
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from fastapi import FastAPI
from sqlmodel import Session, col, select

from marcel_hub.artifacts import SELF_REPO, ArtifactNotFound, ArtifactStore, PrStatusUnavailable
from marcel_hub.auth.tokens import OWNER_USER_ID
from marcel_hub.channel.approvals import ApprovalBook
from marcel_hub.channel.ports import Push
from marcel_hub.conversations import agent_of_artifact, ensure_main_conversation, post_message
from marcel_hub.github import GitHubPrStatus
from marcel_hub.models import (
    Agent,
    AnsweredVia,
    Approval,
    ApprovalState,
    Artifact,
    ArtifactKind,
    MessageKind,
    MessageRole,
    Task,
)
from marcel_hub.notify.message import clip
from marcel_hub.runner_client import Alerts
from marcel_hub.tasks.registry import TaskRegistry
from marcel_hub.ws import EventLog

log = logging.getLogger(__name__)

SHA = re.compile(r'[0-9a-f]{40}')
OPEN_STATES = {'open', 'draft'}
SUMMARY_MAX = 200
UNAPPROVED = (
    'A change to Marcel was merged without approval in the app; not redeploying. '
    'Marcel keeps running the code it had.'
)
NEW_COMMITS = (
    'New commits were added after you approved; not redeploying. Approve the new version in the '
    'app to redeploy it.'
)
MAIN = 'main'


class RedeployFailed(Exception):
    """The flag file could not be written. The message says why, in plain words."""


def request_redeploy(sha: str, flag: Path) -> None:
    """Ask the host to redeploy Marcel at `sha` (`.claude/rules/self-modification.md`): write the
    SHA to the flag file the host's `marcel-redeploy.path` unit watches. Nothing else: no restart,
    no checkout. `sha` must be a full 40-character lowercase hex commit id."""
    if not isinstance(sha, str) or not SHA.fullmatch(sha):
        raise ValueError('A redeploy needs a full commit id (40 hex characters).')
    # A new owner-only file with a random name, created only if nothing is there (O_EXCL), and
    # never through a symlink (O_NOFOLLOW): a file planted at that path is refused, not followed.
    tmp = flag.parent / f'.{flag.name}.{secrets.token_hex(8)}'
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except OSError as exc:
        raise RedeployFailed(
            f'Marcel could not ask for a redeploy: it cannot write {flag} ({exc.strerror}).'
        ) from None
    try:
        with os.fdopen(fd, 'w') as out:
            out.write(sha + '\n')
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, flag)  # the path unit never sees half a SHA; a symlink flag is replaced
    except OSError as exc:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise RedeployFailed(
            f'Marcel could not ask for a redeploy: it cannot write {flag} ({exc.strerror}).'
        ) from None
    with contextlib.suppress(OSError):  # the rename itself survives a power cut
        folder = os.open(flag.parent, os.O_RDONLY)
        try:
            os.fsync(folder)
        finally:
            os.close(folder)
    log.info('Redeploy requested at %s (flag %s).', sha, flag)


class PullSource(Protocol):
    async def pull(self, url: str) -> dict[str, Any]:
        """GitHub's answer for one PR. Raises `PrStatusUnavailable`."""
        ...


class NoPulls:
    async def pull(self, url: str) -> dict[str, Any]:
        raise PrStatusUnavailable(
            'The hub has no GitHub token (MARCEL_GITHUB_TOKEN is not set), so it cannot read '
            'this change to Marcel.'
        )


def head_of(pull: dict[str, Any]) -> str | None:
    """The PR's head commit from GitHub's answer, or None when it is not a full SHA."""
    sha = (pull.get('head') or {}).get('sha')
    return sha if isinstance(sha, str) and SHA.fullmatch(sha) else None


def base_of(pull: dict[str, Any]) -> tuple[str, str]:
    """(target branch, target repository) from GitHub's answer; '' for what it did not say."""
    base = pull.get('base') or {}
    ref, repo = base.get('ref'), (base.get('repo') or {}).get('full_name')
    return (ref if isinstance(ref, str) else '', repo if isinstance(repo, str) else '')


def into_main(pull: dict[str, Any]) -> bool:
    """The PR targets `main` of Marcel's own repository."""
    ref, repo = base_of(pull)
    return ref == MAIN and repo.lower() == SELF_REPO


def diff_stats(pull: dict[str, Any]) -> str:
    """ "3 files changed: 40 lines added, 12 removed." from GitHub's PR answer."""
    files, added, removed = (pull.get(k) for k in ('changed_files', 'additions', 'deletions'))
    if not all(isinstance(n, int) and not isinstance(n, bool) for n in (files, added, removed)):
        return 'GitHub did not say how big the change is.'
    return (
        f'{files} file{"" if files == 1 else "s"} changed: '
        f'{added} line{"" if added == 1 else "s"} added, {removed} removed.'
    )


class SelfChanges:
    def __init__(
        self,
        store: ArtifactStore,
        approvals: ApprovalBook,
        flag: Path,
        *,
        pulls: PullSource | None = None,
        events: EventLog | None = None,
        push: Push | None = None,
        alerts: Alerts | None = None,
        tasks: TaskRegistry | None = None,
        watch: Callable[[str], None] | None = None,
    ) -> None:
        self.store = store
        self.engine = store.engine
        self.approvals = approvals
        self.flag = flag
        self.pulls: PullSource = pulls or NoPulls()
        self.events = events
        self.push = push
        self.alerts = alerts
        self.tasks = tasks
        self.watch = watch  # the PR refresher's `watch`

    # Reading ----------------------------------------------------------------------------------

    @staticmethod
    def record(art: Artifact) -> dict[str, Any]:
        return dict(art.meta_json.get('self_change') or {})

    def pending_merges(self) -> list[str]:
        """Self-change PRs seen merged or closed whose follow-up never finished (a restart)."""
        with Session(self.engine) as s:
            rows = s.exec(
                select(Artifact)
                .where(Artifact.kind == ArtifactKind.pr)
                .where(col(Artifact.self_change).is_(True))
            ).all()
        return [
            a.id
            for a in rows
            if a.meta_json.get('pr', {}).get('state') not in OPEN_STATES
            and not self.record(a).get('outcome')
        ]

    def resume(self) -> None:
        """At hub start: refresh those PRs again, so their follow-up runs."""
        if self.watch is None:
            return
        for artifact_id in self.pending_merges():
            self.watch(artifact_id)

    # After each PR refresh --------------------------------------------------------------------

    async def after_refresh(self, art: Artifact) -> None:
        """`PrRefresher.after_refresh`. Raises `PrStatusUnavailable` when GitHub could not be
        read: the refresher then tries again later."""
        rec = self.record(art)
        if rec.get('outcome'):
            return
        state = art.meta_json.get('pr', {}).get('state')
        if not art.self_change:
            if rec.get('approval_id'):  # its address moved off Marcel's repository
                self._expire(rec, art)
            return
        if state in OPEN_STATES:
            await self._ensure_card(art, rec)
        elif state == 'merged':
            await self._merged(art, rec)
        elif state == 'closed':
            self._expire(rec, art)
            self._set(art.id, outcome='closed')

    async def _ensure_card(self, art: Artifact, rec: dict[str, Any]) -> None:
        """One card for the PR's current head. A push while the card is open replaces it; a push
        after the owner approved opens a fresh card, so the owner always approves what merges."""
        if art.task_id is None:
            log.info('Self-change PR %s has no task, so no approval card can be opened.', art.id)
            return
        assert art.url is not None
        pull = await self.pulls.pull(art.url)
        head = head_of(pull)
        if head is None:
            raise PrStatusUnavailable(
                'GitHub did not give the latest commit of this change to Marcel; the hub asks '
                'again in a few minutes.'
            )
        card = self._card(rec)
        same_pr = card is not None and rec.get('url') == art.url
        if same_pr and rec.get('head') == head:
            return
        if same_pr and card is not None and card.state == ApprovalState.denied:
            return  # the owner said no to this PR; new commits do not ask again
        was_approved = same_pr and card is not None and card.state == ApprovalState.approved
        self._expire(rec, art)  # a card for an older commit or address no longer applies
        self._open_card(art, pull, head, after_merge=False)
        if was_approved:
            self._say(
                art,
                f'New commits were added to "{art.title}" after you approved it. Approve the new '
                f'version ({head[:7]}) in the app before you merge it.',
            )

    def _open_card(
        self, art: Artifact, pull: dict[str, Any], head: str, *, after_merge: bool
    ) -> None:
        assert art.task_id is not None and art.url is not None
        title = str(pull.get('title') or art.title)
        branch = base_of(pull)[0] or 'a branch GitHub did not name'
        card = self.approvals.open_self_change(
            art.task_id,
            art.id,
            clip(f'Marcel wants to change itself: {title}', SUMMARY_MAX),
            f'Approve change {head[:7]} into {branch}. {diff_stats(pull)}\n{art.url}',
        )
        self._set(
            art.id, approval_id=card.id, url=art.url, head=head, decision=None,
            after_merge=after_merge or None,
        )  # fmt: skip
        log.info('Self-change PR %s at %s waits for the owner (%s).', art.url, head, card.id)
        self._post_card(card)

    async def _merged(self, art: Artifact, rec: dict[str, Any]) -> None:
        assert art.url is not None
        pull = await self.pulls.pull(art.url)
        head, sha = head_of(pull), pull.get('merge_commit_sha')
        merged = pull.get('merged') is True and isinstance(sha, str) and SHA.fullmatch(sha)
        if not merged or head is None:
            raise PrStatusUnavailable(
                'GitHub has not given the merge commit of this change to Marcel yet; the hub '
                'asks again in a few minutes.'
            )
        assert isinstance(sha, str)
        card = self._card(rec)
        if not self._approved_in_app(art, rec):
            waiting = card is not None and card.state == ApprovalState.open
            if rec.get('after_merge') and waiting and rec.get('head') == head:
                return  # a fresh card for the merged head waits for the owner
            log.warning('Self-change PR %s was merged without approval; not redeploying.', art.url)
            self._stop(art, rec, f'self_change:{art.id}', UNAPPROVED)
            return
        if not into_main(pull):
            ref, repo = base_of(pull)
            text = (
                f'A change to Marcel was merged into {ref or "an unknown branch"} of '
                f'{repo or "an unknown repository"}, not main of {SELF_REPO}; not redeploying.'
            )
            self._stop(art, rec, f'self_change:{art.id}:branch', text)
            return
        if rec.get('head') != head:  # the control: an approval covers exactly one commit
            log.warning('Self-change PR %s merged at %s, approved at %s.', art.url, head,
                        rec.get('head'))  # fmt: skip
            if self.alerts is not None:
                self.alerts.broke(f'self_change:{art.id}:new_commits', 'Marcel is not redeploying',
                                  NEW_COMMITS)  # fmt: skip
            self._say(art, f'{NEW_COMMITS} ({art.url})')
            if art.task_id is None:
                self._set(art.id, outcome='not_redeployed')
            else:
                self._open_card(art, pull, head, after_merge=True)
            return
        # If saving the outcome below fails, the next refresh comes back here and writes the same
        # SHA again: harmless, as the flag holds one SHA and the host redeploys to it once.
        try:
            request_redeploy(sha, self.flag)
        except RedeployFailed as exc:
            self._set(art.id, outcome='redeploy_failed', sha=sha)
            if self.alerts is not None:
                self.alerts.broke(f'self_change:{art.id}', 'Marcel cannot redeploy', str(exc))
            self._say(art, f'"{art.title}" is merged, but {exc}')
            return
        self._set(art.id, outcome='redeploy_requested', sha=sha)
        self._say(
            art,
            f'"{art.title}" is merged. Marcel is redeploying itself to {sha[:7]}; if the health '
            'check fails, it rolls back on its own.',
        )

    def _stop(self, art: Artifact, rec: dict[str, Any], cause: str, text: str) -> None:
        """No redeploy, ever, for this PR: the open card expires and the owner is told once."""
        self._expire(rec, art)
        self._set(art.id, outcome='not_redeployed')
        if self.alerts is not None:
            self.alerts.broke(cause, 'Marcel is not redeploying', text)
        self._say(art, f'{text} ({art.url})')

    def _card(self, rec: dict[str, Any]) -> Approval | None:
        approval_id = rec.get('approval_id')
        return self.approvals.get(approval_id) if isinstance(approval_id, str) else None

    def _approved_in_app(self, art: Artifact, rec: dict[str, Any]) -> bool:
        """The control: the owner approved this PR's card in the app, and the PR is still the
        one the card was opened for."""
        card = self._card(rec)
        return (
            card is not None
            and card.state == ApprovalState.approved
            and card.answered_via == AnsweredVia.app
            and card.artifact_id == art.id
            and rec.get('url') == art.url
        )

    # The owner's answer -----------------------------------------------------------------------

    async def answer(self, approval_id: str, approve: bool, note: str | None) -> Approval | None:
        """The owner answered a self-change card in the app. Returns the closed card, or None if
        it was not open."""
        state = ApprovalState.approved if approve else ApprovalState.denied
        row = self.approvals.resolve(approval_id, state, AnsweredVia.app)
        if row is None or row.artifact_id is None:
            return row
        try:
            art = self.store.get(row.artifact_id)
        except ArtifactNotFound:
            return row
        rec = self.record(art)
        merged_already = bool(rec.get('after_merge')) and rec.get('approval_id') == row.id
        self._set(art.id, decision='approved' if approve else 'denied')
        link = f'marcel://artifact/{art.id}'
        if merged_already:  # a fresh card for a head that is merged already
            if approve:
                self._say(
                    art,
                    f'You approved "{art.title}", which is merged already. Marcel redeploys it '
                    'now, and rolls back if the health check fails.',
                )
                if self.watch is not None:
                    self.watch(art.id)
            else:
                self._set(art.id, outcome='not_redeployed')
                self._say(art, f'You said no to "{art.title}". Marcel will not redeploy it.')
            return row
        if approve:
            self._say(
                art,
                f'You approved "{art.title}". Marcel never merges changes to itself: open the PR '
                f'and merge it on GitHub: {art.url} ({link}). Once GitHub shows it merged, Marcel '
                'redeploys itself, and rolls back if the health check fails.',
            )
            if self.watch is not None:
                self.watch(art.id)  # look at the PR again soon
        else:
            self._say(
                art,
                f'You said no to "{art.title}". Marcel will not redeploy it, even if it is '
                f'merged. The PR is still open; you can close it on GitHub: {art.url} ({link}).',
            )
            await self._tell_worker(row.task_id, art, note)
        return row

    async def _tell_worker(self, task_id: str, art: Artifact, note: str | None) -> None:
        """The hub's token cannot comment on or close the PR (ADR-002), so the worker that made
        it is asked to, with its own git credentials."""
        if self.tasks is None:
            return
        why = f' The owner said: "{note.strip()}".' if note and note.strip() else ''
        text = (
            f'The owner said no to this change to Marcel ({art.url}).{why} Do not merge it and '
            'do not push to it again. If your git credentials allow it, close the PR with a '
            'comment that the owner declined it. Then report done.'
        )
        try:
            await self.tasks.steer(task_id, text, author='marcel')
        except Exception:  # the owner was told already; the PR can be closed on GitHub
            log.exception('Could not tell task %s that its self-change was declined.', task_id)

    # Writing ----------------------------------------------------------------------------------

    def _set(self, artifact_id: str, **values: Any) -> None:
        art = self.store.get(artifact_id)
        rec = {**self.record(art), **values}
        kept = {k: v for k, v in rec.items() if v is not None}
        self.store.set_meta(artifact_id, self_change=kept)

    def _expire(self, rec: dict[str, Any], art: Artifact) -> None:
        approval_id = rec.get('approval_id')
        if isinstance(approval_id, str) and self.approvals.resolve(
            approval_id, ApprovalState.expired, None
        ):
            log.info('Self-change card %s for %s expired.', approval_id, art.url)

    def _post_card(self, card: Approval) -> None:
        if self.events is not None:
            try:
                with Session(self.engine) as s:
                    task = s.get(Task, card.task_id)
                    agent = s.get(Agent, task.agent_id) if task else None
                    if agent is not None and agent.owner_user_id != OWNER_USER_ID:
                        # A family helper's PR on Marcel: the owner answers it (B-24, B-38).
                        agent = s.exec(
                            select(Agent)
                            .where(Agent.owner_user_id == OWNER_USER_ID)
                            .order_by(col(Agent.id))
                        ).first()
                    if agent is not None:
                        post_message(
                            s, self.events, ensure_main_conversation(s, agent),
                            role=MessageRole.agent, kind=MessageKind.approval,
                            body={'approval_id': card.id}, task_id=card.task_id,
                        )  # fmt: skip
            except Exception:  # the card exists; the message is only where it shows
                log.exception('Could not post the self-change card %s.', card.id)
        if self.push is not None:
            try:
                self.push.approval_opened(card.id)
            except Exception:
                log.exception('Could not send the approval push for %s.', card.id)

    def _say(self, art: Artifact, text: str) -> None:
        """A plain-words note from the hub in the agent's main conversation."""
        if self.events is None:
            return
        try:
            with Session(self.engine) as s:
                agent_id = agent_of_artifact(s, art)
                agent = s.get(Agent, agent_id) if agent_id else None
                if agent is None:
                    return
                post_message(
                    s, self.events, ensure_main_conversation(s, agent), role=MessageRole.system,
                    kind=MessageKind.text, body={'text': text}, task_id=art.task_id,
                )  # fmt: skip
        except Exception:
            log.exception('Could not post a self-change note for %s.', art.id)


def install(
    app: FastAPI, flag: Path, push: Push | None = None, alerts: Alerts | None = None
) -> SelfChanges:
    """Wire self-changes to the PR refresher (`github.install` first) and the approval routes.
    The app's lifespan calls `resume` after the refresher starts."""
    store: ArtifactStore = app.state.artifacts
    refresher = app.state.pr_refresher
    source = store.pr_source
    changes = SelfChanges(
        store,
        app.state.channel.approvals,
        flag,
        pulls=source if isinstance(source, GitHubPrStatus) else None,
        events=app.state.events,
        push=push,
        alerts=alerts,
        tasks=app.state.tasks,
        watch=refresher.watch,
    )
    refresher.after_refresh.append(changes.after_refresh)
    app.state.selfmod = changes
    return changes
