"""The project tracker in recorded worker turns (S-20.2, B-46).

`worker_replays.json` holds what a Kickoff quest and a coding quest do with a project's tracker:
the channel `report` calls Marcel's hub receives, and the `Artifact`/`ArtifactData` calls on the
claude.ai tracker (SP9 proved them unattended on the NUC). Each turn is checked against the skill
that asks for it (so the skill cannot drop a rule the turn relies on), every report against the
channel contract and the hub's own frame check, and the card moves against the rules."""

import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest
from marcel_hub.channel.frames import check_artifact

ROOT = Path(__file__).resolve().parents[4]
SKILLS = ROOT / 'plugins' / 'marcel' / 'skills'
REPLAYS = json.loads((Path(__file__).parent / 'worker_replays.json').read_text())['replays']
CHANNEL = json.loads((ROOT / 'contracts' / 'channel.schema.json').read_text())
TRACKER = 'https://claude.ai/artifact/Tr4ckerGardenPlanner01'


def report_schema() -> dict[str, Any]:
    """The contract's `Report` without the frame envelope (`type`, `call_id`), which the channel
    server adds around the tool's input."""
    return {'$ref': '#/$defs/Report', '$defs': CHANNEL['$defs']}


def calls(replay: str, tool: str) -> list[dict[str, Any]]:
    return [c['input'] for c in REPLAYS[replay]['calls'] if c['tool'] == tool]


@pytest.mark.parametrize('replay', sorted(REPLAYS))
def test_the_skill_still_says_what_the_turn_relies_on(replay: str) -> None:
    text = ' '.join((SKILLS / REPLAYS[replay]['skill'] / 'SKILL.md').read_text().split())
    for rule in REPLAYS[replay]['skill_says']:
        assert ' '.join(rule.split()) in text, f'{replay}: the skill no longer says “{rule}”'


@pytest.mark.parametrize('replay', sorted(REPLAYS))
def test_every_report_fits_the_channel_contract_and_the_hub(replay: str) -> None:
    for report in calls(replay, 'report'):
        frame = {'type': 'report', 'call_id': 'c_0001', **report}
        jsonschema.validate(frame, report_schema())
        if 'artifact' in report:
            check_artifact(dict(report['artifact']))  # raises BadFrame when the hub would refuse it


def test_the_kickoff_publishes_the_tracker_then_reports_it() -> None:
    steps = [c['tool'] for c in REPLAYS['kickoff-tracker']['calls']]
    publish = calls('kickoff-tracker', 'Artifact')
    assert publish == [publish[0]] and publish[0]['capabilities'] == {'db': {}}
    assert publish[0]['file_path'].endswith('/tracker.html')
    meta = [c for c in calls('kickoff-tracker', 'ArtifactData') if c['collection'] == 'meta']
    assert [(m['doc_id'], sorted(m['data'])) for m in meta] == [
        ('project', ['goal', 'name', 'repo'])
    ]
    trackers = [r['artifact'] for r in calls('kickoff-tracker', 'report')
                if r.get('artifact', {}).get('role') == 'tracker']  # fmt: skip
    assert trackers == [{'kind': 'link', 'title': 'Garden planner tracker', 'url': TRACKER,
                         'role': 'tracker'}]  # fmt: skip
    # published and described before it is reported, so the link opens on a tracker with a name
    assert steps.index('Artifact') < steps.index('ArtifactData') < [
        i for i, c in enumerate(REPLAYS['kickoff-tracker']['calls'])
        if c['input'].get('artifact', {}).get('role') == 'tracker'
    ][0]  # fmt: skip


@pytest.mark.parametrize('replay', sorted(REPLAYS))
def test_a_quest_moves_only_its_own_card_in_progress_then_review(replay: str) -> None:
    task_id = REPLAYS[replay]['task_id']
    sequence = REPLAYS[replay]['calls']
    moves = [
        (i, c['input']) for i, c in enumerate(sequence)
        if c['tool'] == 'ArtifactData' and c['input']['collection'] == 'cards'
    ]  # fmt: skip
    assert {m['doc_id'] for _, m in moves} == {task_id}  # never another quest's card
    assert [m['data'].get('status') for _, m in moves] == ['In Progress', 'Review']
    started = next(i for i, c in enumerate(sequence) if c['input'].get('kind') == 'started')
    pr = next(
        i for i, c in enumerate(sequence) if c['input'].get('artifact', {}).get('kind') == 'pr'
    )
    (first, _), (second, review) = moves
    assert started < first and pr < second  # In Progress after started; Review after the PR
    assert review['data']['pr_url'] == sequence[pr]['input']['artifact']['url']
    assert review['if_version'] >= 1  # every update is pinned
    assert all(m['url'] == TRACKER for _, m in moves)
    assert 'Done' not in json.dumps([m for _, m in moves])  # the digest sets Done, not the quest


def test_the_coding_quest_logs_each_move() -> None:
    logs = [c for c in calls('coding-quest-card', 'ArtifactData') if c['collection'] == 'log']
    assert [(entry['data']['card'], entry['data']['text']) for entry in logs] == [
        ('tsk_login', 'In Progress'), ('tsk_login', 'Review: PR #14'),
    ]  # fmt: skip


def test_the_digest_moves_merged_prs_to_done() -> None:
    text = ' '.join((SKILLS / 'digest' / 'SKILL.md').read_text().split())
    assert 'every card in `Review` whose PR the facts list as merged goes to `Done`' in text
