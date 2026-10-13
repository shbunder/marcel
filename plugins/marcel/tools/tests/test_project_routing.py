"""Project-aware routing (S-06.6, B-31): three sample requests, replayed by a fake `claude -p`
(`fake_brain.py`) through the real tools server to the real hub (in-process), land in the projects
the eval expects. No model and no network: the brain's turns are recordings."""

import json
from pathlib import Path
from typing import Any

import pytest
from conftest import World
from fake_brain import FakeBrain, NotFaked, load_replays
from marcel_hub.models import Project, Task
from skills_eval import EvalServer, check_case, claude_command, load_cases, mcp_config
from sqlmodel import Session, select

PROJECT_CASES = ['project-by-repo', 'project-by-goal', 'project-inbox']


def case_named(case_id: str) -> dict[str, Any]:
    return next(c for c in load_cases() if c['id'] == case_id)


def projects_like_the_eval(world: World) -> None:
    """The projects the eval's canned `projects_list` shows, made for real."""
    world.ok('projects_create', {'name': 'Marcel', 'goal': 'The home assistant the family talks to',
                                 'repo': 'shbunder/marcel'})  # fmt: skip
    world.ok('projects_create', {'name': 'Energy contracts',
                                 'goal': 'Cheaper gas and electricity for the house'})  # fmt: skip


class Replay:
    """A fake brain session: the fake `claude` talking MCP to the eval's tools server, whose
    brain tools reach the hub and whose `reply` is only logged."""

    def __init__(self, world: World, tmp_path: Path, case_id: str) -> None:
        self.case = case_named(case_id)
        self.calls: list[dict[str, Any]] = []
        self.answers: dict[str, str] = {}
        self.config = tmp_path / 'mcp.json'
        self.config.write_text(json.dumps(mcp_config(case_id, tmp_path / 'calls.jsonl')))
        out: list[str] = []

        def caller(name: str, args: Any) -> tuple[str, bool]:
            self.calls.append({'tool': name, 'args': args})
            text, is_error = ('Sent.', False) if name == 'reply' else world.call(name, args)
            self.answers[name] = text
            return text, is_error

        self.server = EvalServer(caller, out.append)
        self.out = out

    def send(self, msg: dict[str, Any]) -> dict[str, Any]:
        self.server.handle_line(json.dumps(msg))
        return json.loads(self.out.pop())

    def run(self, argv: list[str] | None = None, replays: dict[str, Any] | None = None) -> Any:
        argv = argv or claude_command(self.case, self.config)
        return FakeBrain(load_cases(), replays or load_replays()).run(argv, self.send)


def filed_in(world: World) -> list[tuple[str, str]]:
    with Session(world.app.state.engine) as s:
        return [(t.title, s.get_one(Project, t.project_id).slug) for t in s.exec(select(Task))]


@pytest.mark.parametrize('case_id', PROJECT_CASES)
def test_a_sample_request_is_filed_in_the_expected_project(
    case_id: str, world: World, tmp_path: Path
) -> None:
    projects_like_the_eval(world)
    replay = Replay(world, tmp_path, case_id)
    result = replay.run()
    assert result['is_error'] is False, result['result']
    assert check_case(replay.case, replay.calls) == []
    spawn = next(s for s in replay.case['expect'] if s['tool'] == 'tasks_spawn')
    title = next(c['args']['title'] for c in replay.calls if c['tool'] == 'tasks_spawn')
    assert filed_in(world) == [(title, spawn['args']['project'])]


def test_only_the_repo_field_ties_the_repo_request_to_its_project(
    world: World, tmp_path: Path
) -> None:
    projects_like_the_eval(world)
    replay = Replay(world, tmp_path, 'project-by-repo')
    replay.run()
    (line,) = [x for x in replay.answers['projects_list'].splitlines() if x.startswith('- marcel ')]
    assert line.endswith('(repo shbunder/marcel) [working 0, needs you 0, done this week 0]')
    assert 'shbunder/marcel' not in line.split(' (repo ')[0]
    canned = case_named('project-by-repo')['answers']['projects_list']
    (eval_line,) = [x for x in canned.splitlines() if x.startswith('- marcel ')]
    assert eval_line.split(' — ')[1].split(' [')[0] == line.split(' — ')[1].split(' [')[0]


def test_every_project_case_has_a_recorded_turn() -> None:
    replays = load_replays()
    # the mention case: S-18.2; the kickoff case: S-20.1
    assert set(replays) == {*PROJECT_CASES, 'mention-quest', 'kickoff-new-repo'}
    for case_id in PROJECT_CASES:
        assert case_named(case_id)['behaviours'] == ['B-31']


def test_the_fake_refuses_a_skill_that_lost_the_rule(world: World, tmp_path: Path) -> None:
    replay = Replay(world, tmp_path, 'project-inbox')
    argv = claude_command(replay.case, replay.config)
    at = argv.index('--append-system-prompt') + 1
    argv[at] = argv[at].replace('**Nothing fits** → `inbox`.', '**Nothing fits** → anywhere.')
    with pytest.raises(NotFaked, match='the skill no longer says'):
        replay.run(argv)
    assert replay.calls == []


def test_the_fake_plays_only_what_was_recorded(world: World, tmp_path: Path) -> None:
    replay = Replay(world, tmp_path, 'project-inbox')
    argv = claude_command(replay.case, replay.config)
    with pytest.raises(NotFaked, match='not faked'):
        replay.run(argv[:2] + ['Something nobody recorded.'] + argv[3:])
    with pytest.raises(NotFaked, match='only "claude -p"'):
        replay.run(['claude', '--bg'])
    with pytest.raises(NotFaked, match='without --allowedTools'):
        replay.run([a for a in argv if a != '--allowedTools'])


def test_the_fake_checks_each_call_like_claude_code(world: World, tmp_path: Path) -> None:
    replay = Replay(world, tmp_path, 'project-inbox')
    recorded = load_replays()['project-inbox']
    bad_args = {**recorded, 'calls': [{'tool': 'tasks_spawn', 'args': {'title': 'x'}}]}
    with pytest.raises(NotFaked, match='does not fit'):
        replay.run(replays={'project-inbox': bad_args})
    no_tool = {**recorded, 'calls': [{'tool': 'tasks_create', 'args': {}}]}
    with pytest.raises(NotFaked, match='tasks_create is not a tool'):
        replay.run(replays={'project-inbox': no_tool})


def test_an_error_answer_ends_the_turn(world: World, tmp_path: Path) -> None:
    replay = Replay(world, tmp_path, 'project-inbox')
    recorded = load_replays()['project-inbox']
    lost = {'tool': 'tasks_move', 'args': {'id': 'tsk_nope', 'project': 'inbox'}}
    result = replay.run(
        replays={'project-inbox': {**recorded, 'calls': [lost, *recorded['calls']]}}
    )
    assert result['is_error'] is True
    assert 'tsk_nope' in result['result']
    assert filed_in(world) == []


def test_a_server_error_is_a_failed_run(world: World, tmp_path: Path) -> None:
    replay = Replay(world, tmp_path, 'project-inbox')
    replay.send = lambda msg: {'jsonrpc': '2.0', 'id': msg['id'], 'error': {'code': -1}}  # type: ignore[method-assign]
    with pytest.raises(NotFaked, match='answered initialize'):
        replay.run()
