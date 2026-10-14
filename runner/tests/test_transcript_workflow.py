"""A workflow's run (S-03.11, runner-api 0.1.12, SP10): `workflow_start` / `workflow_stop` in the
main transcript, the run's `journal.jsonl` through `tail?workflow=`, an agent's file through
`tail?workflow=&subagent=`, and the path checks. The recording is
`fake_claude/fixtures/transcripts/workflow-layout/` (Claude Code 2.1.296 on the NUC)."""

import json
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from test_transcript import FIXTURES, valid
from test_transcript_tail import CWD, Rig

from marcel_runner import transcript
from marcel_runner.sessions import project_dir
from marcel_runner.transcript import Normaliser

LAYOUT = FIXTURES / 'workflow-layout'
REC = '632be746-d16f-4a4c-90ac-871472bdd573'
RUN = 'wf_f6861b61-537'
AGENT = 'a05c6304d69317a22'
CALL = 'toolu_019vWxSTvPp8kbRjoswVjudg'
MAIN = LAYOUT / f'{REC}.jsonl'
JOURNAL = LAYOUT / REC / 'subagents' / 'workflows' / RUN / 'journal.jsonl'
AGENT_FILE = JOURNAL.parent / f'agent-{AGENT}.jsonl'


def normalise(path: Path, n: Normaliser) -> list[dict[str, Any]]:
    events, offset = [], 0
    for raw in path.read_bytes().splitlines(keepends=True):
        offset += len(raw)
        events += [valid(e) for e in n.line(raw, offset)]
    return events


def of(events: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [e for e in events if e['type'] == kind]


def line(entry: dict[str, Any], n: Normaliser) -> list[dict[str, Any]]:
    return [valid(e) for e in n.line(json.dumps(entry).encode() + b'\n', 10)]


def launch(use_id: str = 'toolu_1', run: str = 'wf_abc', **extra: Any) -> dict[str, Any]:
    result = {'status': 'async_launched', 'taskType': 'local_workflow', 'runId': run, **extra}
    block = {'type': 'tool_result', 'tool_use_id': use_id, 'content': 'Workflow launched'}
    return {
        'type': 'user',
        'toolUseResult': result,
        'message': {'role': 'user', 'content': [block]},
    }


def notification(use_id: str = 'toolu_1', status: str = 'completed', summary: str = 'done') -> dict:
    text = (
        f'<task-notification>\n<task-id>t1</task-id>\n<tool-use-id>{use_id}</tool-use-id>\n'
        f'<status>{status}</status>\n<summary>{summary}</summary>\n</task-notification>'
    )
    return {'type': 'user', 'message': {'role': 'user', 'content': text}}


# The main transcript ---------------------------------------------------------------------------


def test_the_recorded_main_transcript_starts_and_stops_one_workflow() -> None:
    events = normalise(MAIN, Normaliser(REC))
    [start] = of(events, 'workflow_start')
    assert start['data'] == {
        'tool_use_id': CALL,
        'run_id': RUN,
        'task_id': 'wlqyqf6bp',
        'name': 'summarize-sp10-notes',
        'description': 'Read each of the 10 note files in sp10-files and return a one-line summary',
    }
    [stop] = of(events, 'workflow_stop')
    assert stop['data']['tool_use_id'] == CALL and stop['data']['run_id'] == RUN
    assert stop['data']['status'] == 'completed'
    assert stop['data']['summary'].startswith('Dynamic workflow "Read each of the 10 note files')
    assert not [e for e in of(events, 'text') if '<task-notification>' in e['data']['text']]
    assert events.index(start) < events.index(stop)
    assert all('workflow_id' not in e for e in events), 'the main stream is no workflow stream'


@pytest.mark.parametrize(
    ('status', 'becomes'),
    [('completed', 'completed'), ('failed', 'failed'), ('stopped', 'stopped'),
     ('killed', 'stopped'), ('exploded', 'unknown')],
)  # fmt: skip
def test_the_notifications_status_maps(status: str, becomes: str) -> None:
    n = Normaliser('s')
    line(launch(), n)
    [stop] = line(notification(status=status), n)
    assert stop['data']['status'] == becomes and stop['data']['run_id'] == 'wf_abc'


def test_a_notification_for_a_call_it_did_not_see_launch_stays_text() -> None:
    [event] = line(notification('toolu_other'), Normaliser('s'))
    assert event['type'] == 'text'


def test_a_launch_without_a_good_run_id_is_no_workflow() -> None:
    for bad in (launch(run='../x'), launch(taskType='local_bash'), launch(status='completed')):
        events = line(bad, Normaliser('s'))
        assert [e['type'] for e in events] == ['tool_result'], bad


def test_defaults_and_a_long_summary() -> None:
    n = Normaliser('s')
    [_, start] = line(launch(), n)
    assert start['data'] == {'tool_use_id': 'toolu_1', 'run_id': 'wf_abc', 'name': 'workflow'}
    [stop] = line(notification(summary='x' * 3000), n)
    assert len(stop['data']['summary']) == transcript.MAX_SUMMARY


def test_a_resume_after_the_launch_still_knows_the_run(tmp_path: Path) -> None:
    path = tmp_path / 's.jsonl'
    first = json.dumps(launch()).encode() + b'\n'
    path.write_bytes(first + json.dumps(notification()).encode() + b'\n')
    n = Normaliser('s')
    transcript._agents_before(n, path, len(first), transcript.MAX_LINE)
    [stop] = line(notification(), n)
    assert stop['type'] == 'workflow_stop' and stop['data']['run_id'] == 'wf_abc'


def test_remembered_runs_are_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(transcript, 'MAX_AGENTS', 2)
    n = Normaliser('s')
    for i in range(3):
        line(launch(f'toolu_{i}', f'wf_{i}'), n)
    assert list(n.workflows) == ['toolu_1', 'toolu_2']


# The journal and an agent's file ---------------------------------------------------------------


def test_the_recorded_journal_gives_ten_starts_and_ten_stops() -> None:
    events = normalise(JOURNAL, Normaliser(REC, None, RUN, journal=True))
    starts, stops = of(events, 'workflow_agent_start'), of(events, 'workflow_agent_stop')
    assert len(starts) == 10 and len(stops) == 10 and len(events) == 20
    assert all(e['workflow_id'] == RUN and 'subagent_id' not in e for e in events)
    assert {e['data']['phase'] for e in starts} == {'Summarize'}
    assert {e['data']['agent_id'] for e in starts} == {e['data']['agent_id'] for e in stops}
    kept = next(e for e in stops if e['data']['agent_id'] == AGENT)
    assert json.loads(kept['data']['summary'])['file'] == 'note8.txt'


def test_journal_lines_in_other_shapes() -> None:
    n = Normaliser('s', None, 'wf_abc', journal=True)
    assert line({'type': 'launched'}, n) == []
    [start] = line({'type': 'started', 'agentId': 'abc'}, n)
    assert start['data'] == {'run_id': 'wf_abc', 'agent_id': 'abc', 'label': 'agent'}
    [stop] = line({'type': 'result', 'agentId': 'abc', 'result': 'x' * 3000}, n)
    assert len(stop['data']['summary']) == transcript.MAX_SUMMARY
    [quiet] = line({'type': 'result', 'agentId': 'abc'}, n)
    assert quiet['data'] == {'run_id': 'wf_abc', 'agent_id': 'abc'}
    for odd in ({'type': 'started', 'agentId': 'NOT-HEX'}, {'type': 'paused'}):
        [raw] = line(odd, n)
        assert raw['type'] == 'raw' and raw['workflow_id'] == 'wf_abc'


def test_the_kept_agent_file_streams_with_both_ids() -> None:
    events = normalise(AGENT_FILE, Normaliser(REC, AGENT, RUN))
    assert events and all(e['workflow_id'] == RUN and e['subagent_id'] == AGENT for e in events)


# Over the socket -------------------------------------------------------------------------------


@pytest.fixture
def rig(tmp_path: Path) -> Iterator[Rig]:
    r = Rig(tmp_path / 'home')
    r.rows = [{'id': REC[:8], 'sessionId': REC, 'cwd': CWD}]
    project = r.home / '.claude' / 'projects' / project_dir(CWD)
    project.mkdir(parents=True)
    shutil.copy(MAIN, project / MAIN.name)
    shutil.copytree(LAYOUT / REC, project / REC)
    yield r
    r.close()


def session_dir(rig: Rig) -> Path:
    return rig.home / '.claude' / 'projects' / project_dir(CWD) / REC


def test_the_journal_and_an_agent_stream_over_the_socket(rig: Rig) -> None:
    journal = rig.tail(f'workflow={RUN}&follow=false', ref=REC)
    assert journal.status == 200
    events = journal.all()
    assert len(of(events, 'workflow_agent_start')) == 10
    agent = rig.tail(f'workflow={RUN}&subagent={AGENT}&follow=false', ref=REC)
    streamed = agent.all()
    assert streamed and all(e['workflow_id'] == RUN and e['subagent_id'] == AGENT for e in streamed)
    main = rig.tail('follow=false', ref=REC).all()
    assert len(of(main, 'workflow_start')) == 1 and len(of(main, 'workflow_stop')) == 1


@pytest.mark.parametrize(
    ('query', 'says'),
    [('workflow=wf_../x', 'not a workflow run id'), ('workflow=../x', 'not a workflow run id'),
     (f'workflow={RUN}&subagent=NOT-HEX', 'not a subagent id')],
)  # fmt: skip
def test_bad_params_answer_400(rig: Rig, query: str, says: str) -> None:
    answer = rig.tail(query, ref=REC)
    assert answer.status == 400
    body = answer.json()
    assert body['code'] == 'bad_request' and says in body['message']


def test_a_symlink_out_of_the_session_folder_answers_400(rig: Rig, tmp_path: Path) -> None:
    # Control: remove the path check and this goes red.
    outside = tmp_path / 'elsewhere'
    outside.mkdir()
    (outside / 'journal.jsonl').write_text('{"type": "launched"}\n')
    link = session_dir(rig) / 'subagents' / 'workflows' / 'wf_evil'
    link.symlink_to(outside, target_is_directory=True)
    answer = rig.tail('workflow=wf_evil&follow=false', ref=REC)
    assert answer.status == 400 and 'out of the session' in answer.json()['message']
    file_link = session_dir(rig) / 'subagents' / 'workflows' / RUN / 'agent-abc.jsonl'
    file_link.symlink_to(outside / 'journal.jsonl')
    assert rig.tail(f'workflow={RUN}&subagent=abc&follow=false', ref=REC).status == 400


def test_a_link_inside_the_session_folder_is_fine(rig: Rig) -> None:
    run = session_dir(rig) / 'subagents' / 'workflows'
    (run / 'wf_alias').symlink_to(run / RUN, target_is_directory=True)
    assert rig.tail('workflow=wf_alias&follow=false', ref=REC).status == 200


def test_a_journal_that_does_not_exist_yet_is_waited_for(rig: Rig) -> None:
    tail = rig.tail('workflow=wf_later', ref=REC)
    assert tail.status == 200
    run = session_dir(rig) / 'subagents' / 'workflows' / 'wf_later'
    run.mkdir()
    (run / 'journal.jsonl').write_text('{"type":"started","agentId":"abc","label":"one"}\n')
    [start] = tail.take(1)
    assert start['type'] == 'workflow_agent_start' and start['workflow_id'] == 'wf_later'
    tail.close()


def test_a_symlink_made_after_the_tail_opened_is_never_read(rig: Rig, tmp_path: Path) -> None:
    tail = rig.tail('workflow=wf_late_link', ref=REC)
    assert tail.status == 200
    outside = tmp_path / 'out2'
    outside.mkdir()
    (outside / 'journal.jsonl').write_text('{"type":"started","agentId":"abc"}\n')
    (session_dir(rig) / 'subagents' / 'workflows' / 'wf_late_link').symlink_to(outside)
    locate = transcript.Locator(rig.home, REC, CWD, None, 'wf_late_link')
    assert locate() is None and locate.unsafe()
    tail.s.settimeout(1.0)  # the open stream sends nothing from the outside file
    with pytest.raises(TimeoutError):
        tail.next()
    tail.close()


def test_one_tail_per_file_includes_the_workflow(rig: Rig) -> None:
    journal = rig.tail(f'workflow={RUN}', ref=REC)
    main = rig.tail('', ref=REC)
    assert len(journal.take(1)) == 1 and len(main.take(1)) == 1  # two files, both open
    again = rig.tail(f'workflow={RUN}', ref=REC)
    assert len(again.take(1)) == 1
    journal.all()  # the first journal tail ended: the newest wins
    for t in (main, again):
        t.close()
