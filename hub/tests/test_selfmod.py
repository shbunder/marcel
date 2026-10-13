"""Self-changes (S-11.1, B-24, ADR-005): a PR on Marcel's own repository gets an approval card;
the hub redeploys only a PR the owner approved in the app and then merged on GitHub.

GitHub is never reached: `FakeGitHub` answers through `httpx.MockTransport`, as in
test_github.py. Cards and events are checked against the contract.
"""

import asyncio
import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlmodel import Session
from test_channel_fakes import ChannelHub, channel_hub
from test_tasks_fakes import validator

from marcel_hub import selfmod
from marcel_hub.app import Settings, create_app, load_settings
from marcel_hub.artifacts import Content, PrStatusUnavailable, is_self_change_url
from marcel_hub.channel.approvals import is_self_change
from marcel_hub.channel.outbox import task_target
from marcel_hub.github import GitHubPrStatus
from marcel_hub.models import (
    AnsweredVia,
    Approval,
    ApprovalState,
    Artifact,
    MessageKind,
    MessageRole,
    Task,
    TaskState,
)
from marcel_hub.selfmod import (
    NEW_COMMITS,
    UNAPPROVED,
    NoPulls,
    RedeployFailed,
    SelfChanges,
    diff_stats,
    request_redeploy,
)

APPROVAL = validator('app-api.yaml#/components/schemas/Approval')
TOKEN = 'github_pat_11SECRETSECRETSECRET_do_not_leak_0123456789'
PR = 'https://github.com/shbunder/marcel/pull/14'
HEAD = 'a' * 40
MERGE = 'b' * 40


class FakeGitHub:
    """GitHub's REST API for Marcel's PRs: `pulls[n]` is what `GET …/pulls/n` answers."""

    def __init__(self) -> None:
        self.pulls: dict[int, dict[str, Any]] = {}
        self.requests: list[httpx.Request] = []
        self.add(14)

    def add(self, number: int, **fields: Any) -> dict[str, Any]:
        self.pulls[number] = {
            'state': 'open', 'draft': False, 'merged': False, 'merged_at': None,
            'merge_commit_sha': None, 'head': {'sha': HEAD}, 'requested_reviewers': [],
            'base': {'ref': 'main', 'repo': {'full_name': 'shbunder/marcel'}},
            'title': 'Hub: answer faster at night', 'changed_files': 3, 'additions': 40,
            'deletions': 12, **fields,
        }  # fmt: skip
        return self.pulls[number]

    def push(self, sha: str, number: int = 14) -> None:
        """Someone pushed a commit to the PR's branch."""
        self.pulls[number]['head'] = {'sha': sha}

    def merge(self, number: int = 14, sha: Any = MERGE) -> None:
        self.pulls[number].update(
            state='closed', merged=True, merged_at='2026-10-13T10:00:00Z', merge_commit_sha=sha
        )

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        parts = request.url.path.split('/')  # '', repos, owner, repo, pulls, n, …
        if parts[1] == 'repos' and parts[4] == 'pulls' and int(parts[5]) in self.pulls:
            if len(parts) == 6:
                return httpx.Response(200, json=self.pulls[int(parts[5])])
            return httpx.Response(200, json=[])  # reviews
        if parts[1] == 'repos' and parts[4] == 'commits':
            return httpx.Response(200, json={'total_count': 0, 'check_runs': []})
        return httpx.Response(404, json={'message': 'Not Found'})

    def source(self) -> GitHubPrStatus:
        client = httpx.AsyncClient(transport=httpx.MockTransport(self.handle))
        return GitHubPrStatus(TOKEN, client)


class Rig:
    def __init__(self, hub: ChannelHub, gh: FakeGitHub, tmp_path: Path) -> None:
        self.hub = hub
        self.gh = gh
        self.store = hub.app.state.artifacts
        self.refresher = hub.app.state.pr_refresher
        self.changes: SelfChanges = hub.app.state.selfmod
        source = gh.source()
        self.store.pr_source = source
        self.changes.pulls = source
        self.changes.push = hub.pushes  # recorded, as the gateway's are
        self.changes.alerts = hub.alerts
        (tmp_path / 'state').mkdir()
        self.flag = self.changes.flag = tmp_path / 'state' / 'redeploy.flag'
        self.task_id, _ = hub.task(TaskState.working, title='Make the hub answer faster')

    def add_pr(self, url: str = PR, task_id: str | None = None) -> str:
        spec = {'kind': 'pr', 'title': 'Answer faster at night', 'url': url}
        return self.store.publish_reported(spec, task_id=task_id or self.task_id).id

    def refresh(self, artifact_id: str) -> None:
        self.hub.client.portal.call(self.refresher.refresh, artifact_id)

    def cards(self) -> list[dict[str, Any]]:
        return [c for c in self.hub.approvals() if c['tool'] == 'self_change']

    def card(self) -> dict[str, Any]:
        (card,) = self.cards()
        return card

    def notes(self) -> list[str]:
        return [
            m.body_json['text']
            for m in self.hub.messages()
            if m.role == MessageRole.system and m.kind == MessageKind.text
        ]

    def record(self, artifact_id: str) -> dict[str, Any]:
        return self.store.get(artifact_id).meta_json.get('self_change', {})


@pytest.fixture
def gh() -> FakeGitHub:
    return FakeGitHub()


@pytest.fixture
def rig(tmp_path: Path, gh: FakeGitHub) -> Iterator[Rig]:
    with channel_hub(tmp_path) as hub:
        yield Rig(hub, gh, tmp_path)


# The flag: a PR on Marcel's own repository, decided by the hub. ------------------------------


@pytest.mark.parametrize(
    ('url', 'expected'),
    [
        (PR, True),
        ('https://github.com/SHBunder/Marcel/pull/14', True),
        ('https://github.com/shbunder/marcel/pull/14/', True),
        ('https://github.com/shbunder/marcel-dev/pull/14', False),
        ('https://github.com/someone/marcel/pull/14', False),
        ('http://github.com/shbunder/marcel/pull/14', False),
        ('https://github.com.evil.example/shbunder/marcel/pull/14', False),
        ('https://github.com/shbunder/marcel/pull/14?x=1', False),
        ('https://github.com/shbunder/marcel/pull/14#top', False),
        ('https://github.com/shbunder/marcel/pull/14/files', False),
        ('https://github.com/shbunder/marcel/tree/main', False),
        ('https://[bad', False),
        (None, False),
    ],
)
def test_only_a_pr_on_marcels_repository_is_a_self_change(url: str | None, expected: bool) -> None:
    assert is_self_change_url(url) is expected


def test_the_store_flags_a_self_change_pr_and_nothing_else(rig: Rig) -> None:
    mine = rig.store.get(rig.add_pr())
    other = rig.store.get(rig.add_pr('https://github.com/shbunder/dotfiles/pull/3'))
    link = rig.store.publish_reported({'kind': 'link', 'title': 'PR', 'url': PR})
    assert (mine.self_change, other.self_change, link.self_change) == (True, False, False)
    assert rig.store.to_api(mine)['self_change'] is True


def test_a_new_address_decides_the_flag_again(rig: Rig) -> None:
    art_id = rig.add_pr('https://github.com/shbunder/dotfiles/pull/3')
    moved = rig.store.add_version(art_id, Content(url=PR))
    assert moved.self_change is True
    back = rig.store.add_version(art_id, Content(url='https://github.com/shbunder/dotfiles/pull/4'))
    assert back.self_change is False


# The card: "Marcel wants to change itself", with the diff stats. -----------------------------


def test_a_self_change_pr_opens_one_card_with_its_diff_stats(rig: Rig) -> None:
    art_id = rig.add_pr()
    rig.refresh(art_id)
    rig.refresh(art_id)  # a second refresh opens no second card
    card = rig.card()
    assert card['summary'] == 'Marcel wants to change itself: Hub: answer faster at night'
    assert card['detail'] == (
        f'Approve change aaaaaaa into main. 3 files changed: 40 lines added, 12 removed.\n{PR}'
    )
    assert (card['artifact_id'], card['task_id'], card['state']) == (art_id, rig.task_id, 'open')
    assert rig.hub.pushes.sent == [card['id']]
    shown = [m for m in rig.hub.messages() if m.kind == MessageKind.approval]
    assert [m.body_json['approval_id'] for m in shown] == [card['id']]
    assert rig.record(art_id) == {'approval_id': card['id'], 'url': PR, 'head': HEAD}
    assert {r.method for r in rig.gh.requests} == {'GET'}  # the hub only ever reads GitHub


def test_a_pr_elsewhere_opens_no_card(rig: Rig) -> None:
    rig.gh.add(3)
    art_id = rig.add_pr('https://github.com/shbunder/dotfiles/pull/3')
    rig.refresh(art_id)
    assert rig.cards() == []


def test_a_self_change_pr_with_no_task_opens_no_card(rig: Rig) -> None:
    art = rig.store.publish_reported({'kind': 'pr', 'title': 'From Marcel', 'url': PR})
    rig.refresh(art.id)
    assert rig.cards() == []


def test_the_card_outlives_the_task_that_made_the_pr(rig: Rig) -> None:
    art_id = rig.add_pr()
    rig.refresh(art_id)
    rig.hub.gateway.task_ended(rig.task_id, 'done')
    assert rig.card()['state'] == 'open'


def test_github_unreadable_means_no_card_yet_and_a_retry(rig: Rig) -> None:
    art_id = rig.add_pr()
    rig.changes.pulls = NoPulls()
    rig.refresh(art_id)
    assert rig.cards() == []
    assert 'MARCEL_GITHUB_TOKEN' in (rig.store.get(art_id).meta_json.get('pr_note') or '')
    assert art_id in rig.refresher.due()


@pytest.mark.parametrize(
    ('pull', 'said'),
    [
        ({'changed_files': 1, 'additions': 1, 'deletions': 0},
         '1 file changed: 1 line added, 0 removed.'),
        ({'changed_files': 2}, 'GitHub did not say how big the change is.'),
        ({'changed_files': True, 'additions': 1, 'deletions': 1},
         'GitHub did not say how big the change is.'),
    ],
)  # fmt: skip
def test_diff_stats_in_plain_words(pull: dict[str, Any], said: str) -> None:
    assert diff_stats(pull) == said


# Done when (adapted, ADR-005): no redeploy without an approval in Marcel. -------------------


def test_no_redeploy_without_an_approval_in_the_app(rig: Rig, caplog: Any) -> None:
    """The control. Remove the approval check in `SelfChanges._merged` and this fails."""
    art_id = rig.add_pr()
    rig.refresh(art_id)
    rig.gh.merge()
    with caplog.at_level(logging.WARNING):
        rig.refresh(art_id)
    assert not rig.flag.exists()
    assert rig.hub.alerts.raised == [(f'self_change:{art_id}', 'Marcel is not redeploying',
                                      UNAPPROVED)]  # fmt: skip
    assert rig.card()['state'] == 'expired'
    assert rig.record(art_id)['outcome'] == 'not_redeployed'
    assert any(UNAPPROVED in n for n in rig.notes())
    rig.refresh(art_id)  # once: a later refresh changes nothing
    assert len(rig.hub.alerts.raised) == 1


def test_an_approval_alone_never_redeploys(rig: Rig) -> None:
    art_id = rig.add_pr()
    rig.refresh(art_id)
    res = rig.hub.answer(rig.card()['id'], 'approve')
    assert res.status_code == 200, res.text
    APPROVAL.validate(res.json())
    assert res.json()['state'] == 'approved' and res.json()['answered_via'] == 'app'
    rig.refresh(art_id)  # still open on GitHub
    assert not rig.flag.exists()
    (said,) = rig.notes()
    assert 'merge it on GitHub' in said and PR in said and f'marcel://artifact/{art_id}' in said
    assert rig.hub.gateway.outbox.pending(task_target(rig.task_id)) == []


def test_approved_in_the_app_and_merged_on_github_redeploys_at_the_merge_commit(
    rig: Rig,
) -> None:
    art_id = rig.add_pr()
    rig.refresh(art_id)
    rig.hub.answer(rig.card()['id'], 'approve')
    rig.gh.merge()
    rig.refresh(art_id)
    assert rig.flag.read_text() == MERGE + '\n'
    assert rig.record(art_id)['outcome'] == 'redeploy_requested'
    assert rig.record(art_id)['sha'] == MERGE
    assert 'redeploying itself to bbbbbbb' in rig.notes()[-1]
    assert rig.hub.alerts.raised == []
    # The answer did not go to the worker's session: no prompt waits on a self-change card.
    assert rig.hub.gateway.outbox.pending(task_target(rig.task_id)) == []


def test_an_approval_from_a_terminal_does_not_count(rig: Rig) -> None:
    art_id = rig.add_pr()
    rig.refresh(art_id)
    rig.changes.approvals.resolve(rig.card()['id'], ApprovalState.approved, AnsweredVia.terminal)
    rig.gh.merge()
    rig.refresh(art_id)
    assert not rig.flag.exists()
    assert rig.record(art_id)['outcome'] == 'not_redeployed'


def test_an_approval_is_for_one_pr_address_only(rig: Rig) -> None:
    """Approve PR 14, then the worker points the artifact at PR 15: 15 needs its own approval."""
    art_id = rig.add_pr()
    rig.refresh(art_id)
    first = rig.card()['id']
    rig.hub.answer(first, 'approve')
    rig.gh.add(15, title='Something else')
    rig.store.add_version(art_id, Content(url='https://github.com/shbunder/marcel/pull/15'))
    rig.refresh(art_id)
    second = [c for c in rig.cards() if c['id'] != first]
    assert [c['summary'] for c in second] == ['Marcel wants to change itself: Something else']
    rig.gh.merge(15)
    rig.refresh(art_id)
    assert not rig.flag.exists()
    assert rig.record(art_id)['outcome'] == 'not_redeployed'


def test_an_approval_never_carries_over_to_an_already_merged_pr(rig: Rig) -> None:
    """Approve PR 14, then the worker points the artifact at PR 15, merged already: no new card
    is opened for a merged PR, and the approval for 14 does not cover 15."""
    art_id = rig.add_pr()
    rig.refresh(art_id)
    rig.hub.answer(rig.card()['id'], 'approve')
    rig.gh.add(15)
    rig.gh.merge(15)
    rig.store.add_version(art_id, Content(url='https://github.com/shbunder/marcel/pull/15'))
    rig.refresh(art_id)
    assert not rig.flag.exists()
    assert rig.record(art_id)['outcome'] == 'not_redeployed'


def test_a_pr_moved_off_marcels_repository_closes_its_card(rig: Rig) -> None:
    art_id = rig.add_pr()
    rig.refresh(art_id)
    rig.gh.add(3)
    rig.store.add_version(art_id, Content(url='https://github.com/shbunder/dotfiles/pull/3'))
    rig.refresh(art_id)
    assert rig.card()['state'] == 'expired'


# The SHA comes only from GitHub's answer for the merged PR. ---------------------------------


@pytest.mark.parametrize('sha', [None, 'HEAD', 'b' * 39, 'B' * 40, 'b' * 40 + '; reboot', 12])
def test_a_missing_or_odd_merge_commit_is_never_written(rig: Rig, sha: Any) -> None:
    art_id = rig.add_pr()
    rig.refresh(art_id)
    rig.hub.answer(rig.card()['id'], 'approve')
    rig.gh.merge(sha=sha)
    rig.refresh(art_id)
    assert not rig.flag.exists()
    assert 'merge commit' in (rig.store.get(art_id).meta_json.get('pr_note') or '')
    assert art_id in rig.refresher.due()  # asked again later
    rig.gh.merge()  # GitHub has it now
    rig.refresh(art_id)
    assert rig.flag.read_text() == MERGE + '\n'


def test_merged_must_be_what_github_says(rig: Rig) -> None:
    art_id = rig.add_pr()
    rig.refresh(art_id)
    rig.hub.answer(rig.card()['id'], 'approve')
    rig.gh.merge()
    rig.gh.pulls[14]['merged'] = 'yes'
    rig.refresh(art_id)
    assert not rig.flag.exists()


def test_a_flag_that_cannot_be_written_is_an_alert(rig: Rig, tmp_path: Path) -> None:
    rig.changes.flag = tmp_path / 'missing' / 'redeploy.flag'
    art_id = rig.add_pr()
    rig.refresh(art_id)
    rig.hub.answer(rig.card()['id'], 'approve')
    rig.gh.merge()
    rig.refresh(art_id)
    ((cause, title, body),) = rig.hub.alerts.raised
    assert (cause, title) == (f'self_change:{art_id}', 'Marcel cannot redeploy')
    assert 'cannot write' in body and 'redeploy.flag' in body
    assert rig.record(art_id)['outcome'] == 'redeploy_failed'


# Deny (adapted, ADR-005): the card closes; the worker is asked to close the PR. -------------


def test_deny_closes_the_card_and_asks_the_worker_to_close_the_pr(rig: Rig) -> None:
    art_id = rig.add_pr()
    rig.refresh(art_id)
    res = rig.hub.answer(rig.card()['id'], 'deny', note='Not before the holidays.')
    assert res.status_code == 200 and res.json()['state'] == 'denied'
    assert rig.card()['state'] == 'denied'
    (said,) = rig.notes()
    assert 'You said no' in said and 'close it on GitHub' in said and PR in said
    (frame,) = rig.hub.gateway.outbox.pending(task_target(rig.task_id))
    assert frame['type'] == 'steer' and frame['author'] == 'marcel'
    assert PR in frame['text'] and 'close the PR' in frame['text']
    assert 'Not before the holidays.' in frame['text'] and 'Do not merge' in frame['text']
    assert rig.record(art_id)['decision'] == 'denied'


def test_a_denied_pr_merged_anyway_is_not_redeployed(rig: Rig) -> None:
    art_id = rig.add_pr()
    rig.refresh(art_id)
    rig.hub.answer(rig.card()['id'], 'deny')
    rig.gh.merge()
    rig.refresh(art_id)
    assert not rig.flag.exists()
    assert [a[2] for a in rig.hub.alerts.raised] == [UNAPPROVED]


def test_deny_on_a_stopped_task_still_closes_the_card(rig: Rig) -> None:
    art_id = rig.add_pr()
    rig.refresh(art_id)
    with Session(rig.hub.engine) as s:
        task = s.get(Task, rig.task_id)
        assert task is not None
        task.state = TaskState.stopped
        s.add(task)
        s.commit()
    res = rig.hub.answer(rig.card()['id'], 'deny')
    assert res.status_code == 200 and res.json()['state'] == 'denied'
    assert rig.hub.gateway.outbox.pending(task_target(rig.task_id)) == []
    assert 'close it on GitHub' in rig.notes()[0]


def test_a_card_is_answered_once(rig: Rig) -> None:
    art_id = rig.add_pr()
    rig.refresh(art_id)
    card_id = rig.card()['id']
    assert rig.hub.answer(card_id, 'approve').status_code == 200
    again = rig.hub.answer(card_id, 'deny')
    assert again.status_code == 409 and again.json()['state'] == 'approved'


def test_a_closed_pr_expires_its_card(rig: Rig) -> None:
    art_id = rig.add_pr()
    rig.refresh(art_id)
    rig.gh.pulls[14]['state'] = 'closed'
    rig.refresh(art_id)
    assert rig.card()['state'] == 'expired'
    assert rig.record(art_id)['outcome'] == 'closed'
    assert rig.hub.alerts.raised == []


def test_a_workers_prompt_named_self_change_is_an_ordinary_card(rig: Rig) -> None:
    prompt = Approval(task_id=rig.task_id, request_id='qiwxo', tool='self_change', summary='x')
    assert not is_self_change(prompt)


# After a restart. ----------------------------------------------------------------------------


def test_a_merge_seen_before_a_restart_is_followed_up(rig: Rig) -> None:
    art_id = rig.add_pr()
    rig.refresh(art_id)
    rig.hub.answer(rig.card()['id'], 'approve')
    rig.gh.merge()
    # The hub stored "merged" and stopped before the follow-up ran.
    rig.store.set_meta(art_id, pr={'number': 14, 'state': 'merged'})
    rig.refresher._due.clear()
    assert rig.changes.pending_merges() == [art_id]
    rig.changes.resume()
    assert art_id in rig.refresher.due()
    rig.refresh(art_id)
    assert rig.flag.read_text() == MERGE + '\n'
    assert rig.changes.pending_merges() == []


def test_resume_without_a_refresher_does_nothing(rig: Rig) -> None:
    rig.changes.watch = None
    rig.changes.resume()


# request_redeploy: one function, the flag file and nothing else. ----------------------------


def test_request_redeploy_writes_the_sha_to_the_flag(tmp_path: Path) -> None:
    flag = tmp_path / 'redeploy.flag'
    request_redeploy(MERGE, flag)
    assert flag.read_text() == MERGE + '\n'
    assert [p.name for p in tmp_path.iterdir()] == ['redeploy.flag']  # no temp file left


@pytest.mark.parametrize('sha', ['', 'main', 'b' * 39, 'b' * 41, 'B' * 40, '../' + 'b' * 37])
def test_request_redeploy_refuses_anything_but_a_full_sha(tmp_path: Path, sha: str) -> None:
    flag = tmp_path / 'redeploy.flag'
    with pytest.raises(ValueError, match='40 hex'):
        request_redeploy(sha, flag)
    assert not flag.exists()


def test_request_redeploy_says_plainly_when_it_cannot_write(tmp_path: Path) -> None:
    flag = tmp_path / 'nowhere' / 'redeploy.flag'
    with pytest.raises(RedeployFailed, match='cannot write') as raised:
        request_redeploy(MERGE, flag)
    assert raised.value.__suppress_context__


def test_no_pulls_says_the_token_is_missing() -> None:
    with pytest.raises(PrStatusUnavailable, match='MARCEL_GITHUB_TOKEN'):
        asyncio.run(NoPulls().pull(PR))


# Wiring. -------------------------------------------------------------------------------------


def test_the_flag_path_is_a_setting(tmp_path: Path) -> None:
    env = {'MARCEL_CONFIG': str(tmp_path / 'none.toml')}
    assert str(load_settings(env).redeploy_flag) == '/state/redeploy.flag'
    env['MARCEL_REDEPLOY_FLAG'] = str(tmp_path / 'flag')
    assert load_settings(env).redeploy_flag == tmp_path / 'flag'


def test_the_app_wires_self_changes(rig: Rig) -> None:
    assert rig.changes.after_refresh in rig.refresher.after_refresh
    assert rig.changes.watch == rig.refresher.watch


def test_the_app_uses_the_github_client_for_pulls(tmp_path: Path) -> None:
    app: Any = create_app(Settings(data_dir=tmp_path, db_path=tmp_path / 'm.db', github_token='t'))
    assert isinstance(app.state.selfmod.pulls, GitHubPrStatus)
    bare: Any = create_app(Settings(data_dir=tmp_path / 'b', db_path=tmp_path / 'b.db'))
    assert isinstance(bare.state.selfmod.pulls, NoPulls)


def test_a_failed_message_or_push_never_loses_the_card(rig: Rig, monkeypatch: Any) -> None:
    def boom(*_a: Any, **_k: Any) -> None:
        raise RuntimeError('down')

    monkeypatch.setattr(selfmod, 'post_message', boom)
    monkeypatch.setattr(rig.hub.pushes, 'approval_opened', boom)
    art_id = rig.add_pr()
    rig.refresh(art_id)
    assert rig.card()['state'] == 'open'
    rig.hub.answer(rig.card()['id'], 'approve')  # the note fails quietly too
    with Session(rig.hub.engine) as s:
        assert s.get(Artifact, art_id) is not None


# Review fix 1: an approval covers exactly one commit (the PR head when the card opened). ----

PUSHED = 'c' * 40


def test_approved_at_one_commit_and_merged_with_more_is_not_redeployed(rig: Rig) -> None:
    """The control. Approve at X, push Y, merge: no redeploy, one alert, a fresh card for Y.
    Remove the head check in `SelfChanges._merged` and this fails."""
    art_id = rig.add_pr()
    rig.refresh(art_id)
    first = rig.card()['id']
    rig.hub.answer(first, 'approve')
    rig.gh.push(PUSHED)
    rig.gh.merge()  # pushed and merged between two refreshes
    rig.refresh(art_id)
    assert not rig.flag.exists()
    assert rig.hub.alerts.raised == [(f'self_change:{art_id}:new_commits',
                                      'Marcel is not redeploying', NEW_COMMITS)]  # fmt: skip
    (fresh,) = [c for c in rig.cards() if c['id'] != first]
    assert fresh['state'] == 'open' and fresh['detail'].startswith(
        'Approve change ccccccc into main.'
    )
    assert rig.record(art_id)['head'] == PUSHED and 'outcome' not in rig.record(art_id)
    # Approving the fresh card redeploys the merge.
    res = rig.hub.answer(fresh['id'], 'approve')
    assert res.status_code == 200
    assert 'merged already' in rig.notes()[-1]
    assert art_id in rig.refresher.due()
    rig.refresh(art_id)
    assert rig.flag.read_text() == MERGE + '\n'
    assert len(rig.hub.alerts.raised) == 1


def test_approved_and_merged_at_the_same_commit_redeploys(rig: Rig) -> None:
    art_id = rig.add_pr()
    rig.refresh(art_id)
    rig.hub.answer(rig.card()['id'], 'approve')
    rig.gh.merge()  # head is still the approved commit
    rig.refresh(art_id)
    assert rig.flag.read_text() == MERGE + '\n'


def test_a_push_while_the_card_is_open_replaces_the_card(rig: Rig) -> None:
    art_id = rig.add_pr()
    rig.refresh(art_id)
    first = rig.card()['id']
    rig.gh.push(PUSHED)
    rig.refresh(art_id)
    cards = {c['id']: c for c in rig.cards()}
    assert cards[first]['state'] == 'expired'
    (new,) = [c for c in cards.values() if c['id'] != first]
    assert new['state'] == 'open' and 'Approve change ccccccc' in new['detail']
    rig.hub.answer(new['id'], 'approve')
    rig.gh.merge()
    rig.refresh(art_id)
    assert rig.flag.read_text() == MERGE + '\n'


def test_a_push_after_approval_asks_again_before_the_merge(rig: Rig) -> None:
    art_id = rig.add_pr()
    rig.refresh(art_id)
    first = rig.card()['id']
    rig.hub.answer(first, 'approve')
    rig.gh.push(PUSHED)
    rig.refresh(art_id)  # still open on GitHub
    (new,) = [c for c in rig.cards() if c['id'] != first]
    assert new['state'] == 'open'
    assert 'New commits were added' in rig.notes()[-1] and 'ccccccc' in rig.notes()[-1]
    rig.gh.merge()  # merged without approving the new version
    rig.refresh(art_id)
    assert not rig.flag.exists()
    assert [a[2] for a in rig.hub.alerts.raised] == [UNAPPROVED]


def test_new_commits_on_a_denied_pr_do_not_ask_again(rig: Rig) -> None:
    art_id = rig.add_pr()
    rig.refresh(art_id)
    rig.hub.answer(rig.card()['id'], 'deny')
    rig.gh.push(PUSHED)
    rig.refresh(art_id)
    assert [c['state'] for c in rig.cards()] == ['denied']


def test_denying_the_fresh_card_after_a_merge_ends_it(rig: Rig) -> None:
    art_id = rig.add_pr()
    rig.refresh(art_id)
    rig.hub.answer(rig.card()['id'], 'approve')
    rig.gh.push(PUSHED)
    rig.gh.merge()
    rig.refresh(art_id)
    fresh = next(c for c in rig.cards() if c['state'] == 'open')
    rig.hub.answer(fresh['id'], 'deny')
    assert rig.record(art_id)['outcome'] == 'not_redeployed'
    assert rig.changes.pending_merges() == []
    assert not rig.flag.exists()


def test_a_fresh_card_waits_quietly_across_a_restart(rig: Rig) -> None:
    art_id = rig.add_pr()
    rig.refresh(art_id)
    rig.hub.answer(rig.card()['id'], 'approve')
    rig.gh.push(PUSHED)
    rig.gh.merge()
    rig.refresh(art_id)
    assert rig.changes.pending_merges() == [art_id]
    rig.refresh(art_id)  # what `resume` causes after a restart
    assert len(rig.hub.alerts.raised) == 1
    assert next(c for c in rig.cards() if c['state'] == 'open')
    assert not rig.flag.exists()


def test_a_merged_pr_with_no_task_and_new_commits_ends(rig: Rig) -> None:
    art_id = rig.add_pr()
    rig.refresh(art_id)
    rig.hub.answer(rig.card()['id'], 'approve')
    with Session(rig.hub.engine) as s:
        row = s.get(Artifact, art_id)
        assert row is not None
        row.task_id, row.project_id = None, rig.store.get(art_id).project_id
        s.add(row)
        s.commit()
    rig.gh.push(PUSHED)
    rig.gh.merge()
    rig.refresh(art_id)
    assert rig.record(art_id)['outcome'] == 'not_redeployed'
    assert not rig.flag.exists()


def test_a_pr_without_a_head_commit_opens_no_card_yet(rig: Rig) -> None:
    rig.gh.pulls[14]['head'] = {'sha': 'not-a-sha'}
    art_id = rig.add_pr()
    rig.refresh(art_id)
    assert rig.cards() == []
    assert 'latest commit' in (rig.store.get(art_id).meta_json.get('pr_note') or '')


# Review fix 2: only a PR into main of shbunder/marcel redeploys. -------------------------------


@pytest.mark.parametrize(
    ('base', 'said'),
    [
        ({'ref': 'dev', 'repo': {'full_name': 'shbunder/marcel'}},
         'merged into dev of shbunder/marcel'),
        ({'ref': 'main', 'repo': {'full_name': 'someone/marcel'}},
         'merged into main of someone/marcel'),
        ({}, 'merged into an unknown branch of an unknown repository'),
    ],
)  # fmt: skip
def test_only_a_pr_into_main_redeploys(rig: Rig, base: dict[str, Any], said: str) -> None:
    """The control. Remove the `into_main` check in `SelfChanges._merged` and this fails."""
    rig.gh.pulls[14]['base'] = base
    art_id = rig.add_pr()
    rig.refresh(art_id)
    assert ' into ' in rig.card()['detail']
    rig.hub.answer(rig.card()['id'], 'approve')
    rig.gh.merge()
    rig.refresh(art_id)
    assert not rig.flag.exists()
    ((cause, _, body),) = rig.hub.alerts.raised
    assert cause == f'self_change:{art_id}:branch' and said in body and 'not redeploying' in body
    assert rig.record(art_id)['outcome'] == 'not_redeployed'


def test_the_card_names_the_target_branch(rig: Rig) -> None:
    rig.gh.pulls[14]['base'] = {'ref': 'dev', 'repo': {'full_name': 'shbunder/marcel'}}
    art_id = rig.add_pr()
    rig.refresh(art_id)
    assert rig.card()['detail'].startswith('Approve change aaaaaaa into dev.')


# Review fix 3: the flag file is written safely. ------------------------------------------------


def test_a_planted_symlink_at_the_temp_path_is_never_followed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    victim = tmp_path / 'victim.txt'
    victim.write_text('keep me\n')
    monkeypatch.setattr(selfmod.secrets, 'token_hex', lambda _n: 'fixed')
    flag = tmp_path / 'redeploy.flag'
    (tmp_path / '.redeploy.flag.fixed').symlink_to(victim)
    with pytest.raises(RedeployFailed, match='cannot write'):
        request_redeploy(MERGE, flag)
    assert victim.read_text() == 'keep me\n'
    assert not flag.exists()
    assert (tmp_path / '.redeploy.flag.fixed').is_symlink()  # left alone, not removed


def test_the_flag_is_owner_only(tmp_path: Path) -> None:
    flag = tmp_path / 'redeploy.flag'
    request_redeploy(MERGE, flag)
    assert flag.stat().st_mode & 0o777 == 0o600


def test_a_failed_rename_leaves_no_temp_file(tmp_path: Path) -> None:
    flag = tmp_path / 'redeploy.flag'
    flag.mkdir()  # a folder in the way: the rename fails
    with pytest.raises(RedeployFailed, match='cannot write'):
        request_redeploy(MERGE, flag)
    assert sorted(p.name for p in tmp_path.iterdir()) == ['redeploy.flag']


# Review fix 4: writing the same SHA twice is harmless. ----------------------------------------


def test_the_same_sha_written_twice_is_the_same_flag(tmp_path: Path) -> None:
    flag = tmp_path / 'redeploy.flag'
    request_redeploy(MERGE, flag)
    request_redeploy(MERGE, flag)
    assert flag.read_text() == MERGE + '\n'
    assert [p.name for p in tmp_path.iterdir()] == ['redeploy.flag']


def test_a_lost_outcome_writes_the_same_sha_again(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    art_id = rig.add_pr()
    rig.refresh(art_id)
    rig.hub.answer(rig.card()['id'], 'approve')
    rig.gh.merge()
    real = rig.changes._set
    failed: list[bool] = []

    def flaky(artifact_id: str, **values: Any) -> None:
        if values.get('outcome') and not failed:
            failed.append(True)
            raise RuntimeError('database locked')
        real(artifact_id, **values)

    monkeypatch.setattr(rig.changes, '_set', flaky)
    rig.refresh(art_id)  # the flag is written, the outcome is not saved
    assert rig.flag.read_text() == MERGE + '\n' and 'outcome' not in rig.record(art_id)
    assert art_id in rig.refresher.due()  # tried again
    rig.refresh(art_id)
    assert rig.flag.read_text() == MERGE + '\n'
    assert rig.record(art_id)['outcome'] == 'redeploy_requested'
