"""`plugins/marcel/scripts/session_end.py`, the plugin's `Stop` and `SessionEnd` hooks (S-05.8).

It is a standalone file (standard library only), so it is loaded by path. Its calls go to the real
hub app in-process (`world`). Nothing reaches a network or `claude`."""

import importlib.util
import io
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from conftest import SECRET, ClientTransport, World
from marcel_hub.models import Task, TaskState
from sqlmodel import Session

SCRIPT = Path(__file__).resolve().parents[2] / 'scripts' / 'session_end.py'
HOOKS = Path(__file__).resolve().parents[2] / 'hooks' / 'hooks.json'


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location('marcel_session_end_script', SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


end = load()
BLOCK = json.dumps({'decision': 'block', 'reason': end.REMINDER})


class Counting(ClientTransport):
    def __init__(self, inner: ClientTransport) -> None:
        super().__init__(inner.client)
        self.bodies: list[Any] = []

    def request(self, method: str, path: str, **kw: Any) -> tuple[int, Any]:
        self.bodies.append(kw.get('body'))
        return super().request(method, path, **kw)


class Down:
    def request(self, method: str, path: str, **kw: Any) -> tuple[int, Any]:
        raise ConnectionRefusedError(61, 'Connection refused')


def env(world: World) -> dict[str, str]:
    return {'CLAUDE_PLUGIN_DATA': str(world.data_dir)}


def worker(world: World, now: TaskState = TaskState.working) -> tuple[str, str]:
    """A worker task in `now`, and its session id (the fake runner's `ses-<task id>`)."""
    text = world.ok(
        'tasks_spawn', {'title': 'Check disk usage', 'location': 'nuc', 'prompt': 'Go.'}
    )
    task_id = json.loads(text.split('\n\n', 1)[1])['id']
    with Session(world.app.state.engine) as s:
        task = s.get(Task, task_id)
        assert task is not None
        task.state = now
        s.add(task)
        s.commit()
    return task_id, f'ses-{task_id}'


def state(world: World, task_id: str) -> TaskState:
    with Session(world.app.state.engine) as s:
        task = s.get(Task, task_id)
        assert task is not None
        return task.state


def stop(sid: str, active: bool = False) -> dict[str, Any]:
    return {'hook_event_name': 'Stop', 'session_id': sid, 'stop_hook_active': active}


def ended(sid: str, reason: str = 'prompt_input_exit') -> dict[str, Any]:
    return {'hook_event_name': 'SessionEnd', 'session_id': sid, 'reason': reason}


def test_a_worker_owing_a_report_is_reminded_once(world: World) -> None:
    _, sid = worker(world)
    transport = Counting(ClientTransport(world.client))
    assert end.run(stop(sid), env(world), transport) == BLOCK
    # Claude Code goes on because of this hook: the next stop is let through, unasked.
    assert end.run(stop(sid, active=True), env(world), transport) == ''
    assert transport.bodies == [{'event': 'stop'}]


@pytest.mark.parametrize('now', [TaskState.done, TaskState.failed, TaskState.silent])
def test_nothing_owed_lets_the_stop_through(world: World, now: TaskState) -> None:
    _, sid = worker(world, now)
    assert end.run(stop(sid), env(world), ClientTransport(world.client)) == ''


def test_the_brain_is_never_held(world: World) -> None:
    transport = ClientTransport(world.client)
    assert end.run(stop(world.brain_session), env(world), transport) == ''
    assert end.run(ended(world.brain_session), env(world), transport) == ''


def test_a_worker_that_ends_without_a_report_fails(world: World) -> None:
    task_id, sid = worker(world)
    transport = Counting(ClientTransport(world.client))
    assert end.run(ended(sid), env(world), transport) == ''
    assert transport.bodies == [{'event': 'session_end', 'reason': 'prompt_input_exit'}]
    assert state(world, task_id) == TaskState.failed


def test_a_long_or_missing_reason(world: World) -> None:
    _, sid = worker(world, TaskState.done)
    transport = Counting(ClientTransport(world.client))
    end.run(ended(sid, 'x' * 100), env(world), transport)
    end.run(ended(sid, '   '), env(world), transport)
    assert transport.bodies == [
        {'event': 'session_end', 'reason': 'x' * 64},
        {'event': 'session_end'},
    ]


def test_a_hub_that_does_not_answer_means_nothing_owed(
    world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    _, sid = worker(world)
    assert end.run(stop(sid), env(world), Down()) == ''
    assert end.run(ended(sid), env(world), Down()) == ''
    assert capsys.readouterr().err.count('the hub is not answering') == 2


def test_a_refusal_means_nothing_owed(world: World, capsys: pytest.CaptureFixture[str]) -> None:
    transport = ClientTransport(world.client)
    assert end.run(stop('ses-unknown'), env(world), transport) == ''
    assert 'the hub answered 401' in capsys.readouterr().err


def test_without_a_secret_nothing_is_asked(world: World, tmp_path: Path) -> None:
    _, sid = worker(world)
    transport = Counting(ClientTransport(world.client))
    assert end.run(stop(sid), {}, transport) == ''
    assert end.run(stop(sid), {'CLAUDE_PLUGIN_DATA': str(tmp_path)}, transport) == ''
    (tmp_path / 'hub-secret').write_text('\n')
    assert end.run(stop(sid), {'CLAUDE_PLUGIN_DATA': str(tmp_path)}, transport) == ''
    assert transport.bodies == []


@pytest.mark.parametrize(
    'hook', [{}, {'hook_event_name': 'Stop'}, {'hook_event_name': 'PreCompact', 'session_id': 'x'}]
)
def test_other_input_does_nothing(world: World, hook: dict[str, Any]) -> None:
    transport = Counting(ClientTransport(world.client))
    assert end.run(hook, env(world), transport) == ''
    assert transport.bodies == []


def test_main_always_exits_0(world: World, capsys: pytest.CaptureFixture[str]) -> None:
    assert end.main(io.StringIO('not json'), {}) == 0
    assert capsys.readouterr().err.startswith('session_end:')
    _, sid = worker(world)
    port_env = {**env(world), 'MARCEL_HUB_URL': 'http://127.0.0.1:9'}
    assert end.main(io.StringIO(json.dumps(stop(sid))), port_env) == 0
    out, err = capsys.readouterr()
    assert out == '' and 'the hub is not answering' in err and SECRET not in out + err
    assert end.main(io.StringIO('[]'), port_env) == 0


def test_the_hooks_run_this_script() -> None:
    hooks = json.loads(HOOKS.read_text())['hooks']
    command = 'python3 "${CLAUDE_PLUGIN_ROOT}/scripts/session_end.py"'
    for event in ('Stop', 'SessionEnd'):
        [entry] = hooks[event]
        assert 'matcher' not in entry
        assert entry['hooks'] == [{'type': 'command', 'command': command, 'timeout': 5}]
    assert end.TIMEOUT < 5
