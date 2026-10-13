"""Self-changes (S-11.1, B-24, ADR-005): Marcel changing its own code, only through a PR that the
owner approves in the app and merges on GitHub.

1. A worker works on `shbunder/marcel` and hands in a PR artifact. The store flags it
   `self_change` from its URL alone (`artifacts.is_self_change_url`): a worker can neither claim
   nor avoid the flag.
2. At the PR's first good refresh (`github.PrRefresher.after_refresh`) the hub reads the PR from
   GitHub and opens an approval card: "Marcel wants to change itself: <PR title>", with the diff
   stats (files, lines added and removed). The card outlives the task that made the PR.
3. **Approve** records the owner's go-ahead and tells them, in plain words, to merge the PR on
   GitHub. The hub never merges: its token is read-only (ADR-002), and a PR merged by Marcel
   itself is forbidden (`.claude/rules/self-modification.md`).
   **Deny** records the no, tells the owner they may close the PR on GitHub, and sends the worker
   that made it a follow-up to close it with its own git credentials if it can.
4. When a refresh sees the PR **merged**, the hub calls `request_redeploy` with the merge commit,
   but only if the card was approved in the app, for this very PR URL. The SHA comes only from
   GitHub's answer for the merged PR (`merge_commit_sha`), and must be 40 hex characters; nothing
   a worker or the owner typed is ever used. Merged without that approval: no redeploy and one
   plain alert. Closed without merging: the open card expires.

What happened to a self-change is kept on its artifact as `meta_json['self_change']`
(`approval_id`, `url`, `decision`, `outcome`, `sha`); once `outcome` is set, nothing more happens.

`request_redeploy` only writes the flag file. The host's `marcel-redeploy.path` unit does the
rest (S-11.2): pull, rebuild, health check, `git revert` on failure. The hub never restarts
anything itself.
"""

import contextlib
import logging
import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from fastapi import FastAPI
from sqlmodel import Session, col, select

from marcel_hub.artifacts import ArtifactNotFound, ArtifactStore, PrStatusUnavailable
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


class RedeployFailed(Exception):
    """The flag file could not be written. The message says why, in plain words."""


def request_redeploy(sha: str, flag: Path) -> None:
    """Ask the host to redeploy Marcel at `sha` (`.claude/rules/self-modification.md`): write the
    SHA to the flag file the host's `marcel-redeploy.path` unit watches. Nothing else: no restart,
    no checkout. `sha` must be a full 40-character lowercase hex commit id."""
    if not isinstance(sha, str) or not SHA.fullmatch(sha):
        raise ValueError('A redeploy needs a full commit id (40 hex characters).')
    tmp = flag.with_name(flag.name + '.tmp')
    try:
        tmp.write_text(sha + '\n')
        os.replace(tmp, flag)  # the path unit never sees half a SHA
    except OSError as exc:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise RedeployFailed(
            f'Marcel could not ask for a redeploy: it cannot write {flag} ({exc.strerror}).'
        ) from None
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
        if rec.get('approval_id') and rec.get('url') == art.url:
            return
        if art.task_id is None:
            log.info('Self-change PR %s has no task, so no approval card can be opened.', art.id)
            return
        assert art.url is not None
        self._expire(rec, art)  # a card for the PR's old address no longer applies
        pull = await self.pulls.pull(art.url)
        title = str(pull.get('title') or art.title)
        card = self.approvals.open_self_change(
            art.task_id,
            art.id,
            clip(f'Marcel wants to change itself: {title}', SUMMARY_MAX),
            f'{diff_stats(pull)}\n{art.url}',
        )
        self._set(art.id, approval_id=card.id, url=art.url, decision=None)
        log.info('Self-change PR %s is waiting for the owner (approval %s).', art.url, card.id)
        self._post_card(card)

    async def _merged(self, art: Artifact, rec: dict[str, Any]) -> None:
        if not self._approved_in_app(art, rec):
            log.warning('Self-change PR %s was merged without approval; not redeploying.', art.url)
            self._expire(rec, art)
            self._set(art.id, outcome='not_redeployed')
            if self.alerts is not None:
                self.alerts.broke(f'self_change:{art.id}', 'Marcel is not redeploying', UNAPPROVED)
            self._say(art, f'{UNAPPROVED} ({art.url})')
            return
        assert art.url is not None
        pull = await self.pulls.pull(art.url)
        sha = pull.get('merge_commit_sha')
        if pull.get('merged') is not True or not isinstance(sha, str) or not SHA.fullmatch(sha):
            raise PrStatusUnavailable(
                'GitHub has not given the merge commit of this change to Marcel yet; the hub '
                'asks again in a few minutes.'
            )
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

    def _approved_in_app(self, art: Artifact, rec: dict[str, Any]) -> bool:
        """The control: the owner approved this PR's card in the app, and the PR is still the
        one the card was opened for."""
        approval_id = rec.get('approval_id')
        card = self.approvals.get(approval_id) if isinstance(approval_id, str) else None
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
        self._set(art.id, decision='approved' if approve else 'denied')
        link = f'marcel://artifact/{art.id}'
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
