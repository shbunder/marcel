"""Transcript lines → transcript.schema.json events (`Normaliser`), reading a growing file
(`LineReader`) and finding it (`transcript_path`). Every event any test makes is checked against
the contract's schema. The recorded SP2 transcripts are the fake claude's fixtures."""

import importlib.util
import json
import os
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from marcel_runner import transcript
from marcel_runner.transcript import LineReader, Locator, Normaliser, transcript_path

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).parent / 'fake_claude' / 'fixtures' / 'transcripts'
SUBAGENT = FIXTURES / 't1-happy-path' / 'subagents' / 'agent-a918dd7c41e7ee8b8.jsonl'
SCHEMA = json.loads((ROOT / 'contracts' / 'transcript.schema.json').read_text())
VALIDATOR = Draft202012Validator(SCHEMA)


def valid(event: dict[str, Any]) -> dict[str, Any]:
    """Check one event against transcript.schema.json (and its time, which the plain validator
    does not check). Returns it."""
    VALIDATOR.validate(event)
    datetime.fromisoformat(event['at'].replace('Z', '+00:00'))
    return event


def normalise_file(path: Path, subagent_id: str | None = None) -> list[dict[str, Any]]:
    n = Normaliser(path.stem, subagent_id)
    events, offset = [], 0
    for raw in path.read_bytes().splitlines(keepends=True):
        offset += len(raw)
        events += [valid(e) for e in n.line(raw, offset)]
    return events


def reference() -> Any:
    spec = importlib.util.spec_from_file_location(
        'reference_normalizer', ROOT / 'contracts' / 'tests' / 'reference_normalizer.py'
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def line(name: str, index: int) -> bytes:
    return (FIXTURES / name).read_bytes().splitlines(keepends=True)[index]


def one(entry: dict[str, Any], n: Normaliser | None = None) -> list[dict[str, Any]]:
    events = (n or Normaliser('s')).line(json.dumps(entry).encode() + b'\n', 10)
    return [valid(e) for e in events]


# --- the SP2 mapping ---------------------------------------------------------------------------

# One recorded line of each kind SP2 found, and the events it must give (SP2's table).
LINE_TABLE = [
    ('user prompt', 't2-permission-approved.jsonl', 6, ['text']),
    ('assistant text', 't1-happy-path.jsonl', 46, ['text']),
    ('assistant thinking', 't1-happy-path.jsonl', 45, ['thinking']),
    ('assistant tool_use', 't2-permission-approved.jsonl', 26, ['tool_call']),
    ('tool result', 't2-permission-approved.jsonl', 27, ['tool_result']),
    ('Edit result with a patch', 't1-happy-path.jsonl', 60, ['tool_result', 'diff']),
    ('Agent launch result', 't1-happy-path.jsonl', 32, ['tool_result', 'subagent_start']),
    ('subagent hand-back', 't1-happy-path.jsonl', 51, ['subagent_stop']),
    ('denied tool result', 't3-permission-denied.jsonl', 28, ['tool_result']),
    ('user text block', 't3-permission-denied.jsonl', 30, ['text']),
    ('API error', 'synth-api-error.jsonl', 26, ['error']),
    ('usage limit', 'synth-limit-reached.jsonl', 26, ['error']),
]


@pytest.mark.parametrize(
    ('kind', 'name', 'index', 'types'), LINE_TABLE, ids=[row[0] for row in LINE_TABLE]
)
def test_every_sp2_line_kind_maps_to_its_events(
    kind: str, name: str, index: int, types: list[str]
) -> None:
    n = Normaliser('s')
    for earlier in range(index):  # the lines before it, so an Agent call is known
        n.line(line(name, earlier), 1)
    events = [valid(e) for e in n.line(line(name, index), 99)]
    assert [e['type'] for e in events] == types, kind
    assert [e['index'] for e in events] == list(range(len(types)))
    assert {e['offset'] for e in events} == {99}


def _first_of_each_type() -> list[tuple[str, str, int]]:
    seen: dict[str, tuple[str, str, int]] = {}
    for path in sorted(FIXTURES.glob('*.jsonl')):
        for i, raw in enumerate(path.read_bytes().splitlines()):
            entry = json.loads(raw)
            key = entry['type'] + ('/' + entry['subtype'] if entry.get('subtype') else '')
            seen.setdefault(key, (key, path.name, i))
    return sorted(seen.values())


EXPECTED_BY_LINE_TYPE = {
    **{kind: [] for kind in transcript.DROPPED},
    'system/turn_duration': ['turn_end'],
    'ai-title': ['raw'],  # not in SP2's table: shown as raw until someone maps it
}


@pytest.mark.parametrize(('key', 'name', 'index'), _first_of_each_type())
def test_every_recorded_line_type_has_a_known_mapping(key: str, name: str, index: int) -> None:
    events = [valid(e) for e in Normaliser('s').line(line(name, index), 1)]
    if key in ('user', 'assistant'):
        assert events or key == 'user'  # covered line by line in LINE_TABLE
        return
    assert key in EXPECTED_BY_LINE_TYPE, f'{key} is new: add it to the table'
    assert [e['type'] for e in events] == EXPECTED_BY_LINE_TYPE[key]


# What each recorded transcript gives as a whole: event type → count.
FILE_TABLE = {
    'synth-api-error.jsonl': {'error': 1, 'text': 1},
    'synth-limit-reached.jsonl': {'error': 1, 'text': 1},
    't1-copy-resume-of-live-session.jsonl': {
        'diff': 2,
        'raw': 1,
        'subagent_start': 1,
        'subagent_stop': 1,
        'text': 5,
        'thinking': 2,
        'tool_call': 8,
        'tool_result': 8,
        'turn_end': 3,
    },
    't1-happy-path.jsonl': {
        'diff': 1,
        'subagent_start': 1,
        'subagent_stop': 1,
        'text': 5,
        'thinking': 2,
        'tool_call': 5,
        'tool_result': 5,
        'turn_end': 3,
    },
    't2-permission-approved.jsonl': {'text': 2, 'tool_call': 1, 'tool_result': 1, 'turn_end': 1},
    't3-permission-denied.jsonl': {
        'text': 2,
        'thinking': 1,
        'tool_call': 1,
        'tool_result': 1,
        'turn_end': 1,
    },
}


@pytest.mark.parametrize('name', sorted(FILE_TABLE))
def test_each_recorded_transcript_gives_the_expected_events(name: str) -> None:
    events = normalise_file(FIXTURES / name)
    assert dict(Counter(e['type'] for e in events)) == FILE_TABLE[name]
    assert events[-1]['offset'] <= (FIXTURES / name).stat().st_size


def test_the_table_covers_every_recording() -> None:
    assert sorted(p.name for p in FIXTURES.glob('*.jsonl')) == sorted(FILE_TABLE)


@pytest.mark.parametrize('name', sorted(FILE_TABLE))
def test_the_port_gives_what_the_contract_reference_gives(name: str) -> None:
    """Same events, except a line without a time: the reference says 1970, the runner gives it the
    time of the line before it."""
    ours = normalise_file(FIXTURES / name)
    theirs = reference().normalize_file(
        (FIXTURES / name).read_text(), Path(name).stem, with_usage=True
    )
    assert len(ours) == len(theirs)
    for mine, ref in zip(ours, theirs, strict=True):
        if ref['at'] == '1970-01-01T00:00:00Z':
            ref = {**ref, 'at': mine['at']}
        assert mine == ref


# --- TurnEnd.usage (runner-api 0.1.7) ---------------------------------------------------------

T1 = FIXTURES / 't1-happy-path.jsonl'
USAGE_KEYS = (
    'input_tokens',
    'cache_creation_input_tokens',
    'cache_read_input_tokens',
    'output_tokens',
)


def _main_counts_before_each_turn_end(path: Path) -> list[dict[str, int]]:
    """By hand: the four counts of the last main-thread assistant line before each turn end."""
    out, last = [], None
    for raw in path.read_bytes().splitlines():
        entry = json.loads(raw)
        usage = (entry.get('message') or {}).get('usage') if entry['type'] == 'assistant' else None
        if usage and not entry.get('isSidechain'):
            last = {k: usage[k] for k in USAGE_KEYS}
        if entry.get('subtype') == 'turn_duration':
            assert last is not None
            out.append(last)
            last = None
    return out


def test_every_turn_end_of_the_happy_path_carries_the_turns_last_usage() -> None:
    turns = [e for e in normalise_file(T1) if e['type'] == 'turn_end']
    assert len(turns) == 3
    assert [t['data']['usage'] for t in turns] == _main_counts_before_each_turn_end(T1)
    assert all(set(t['data']['usage']) == set(USAGE_KEYS) for t in turns)


def test_subagent_lines_never_give_their_counts_to_a_turn_end() -> None:
    """Control: the subagent's assistant lines (`isSidechain`, much larger counts) come after
    the main thread's last one, right before the turn end; the turn end keeps the main counts."""
    lines = T1.read_bytes().splitlines(keepends=True)
    first_end = next(i for i, raw in enumerate(lines) if b'turn_duration' in raw)
    sidechain = [
        raw for raw in SUBAGENT.read_bytes().splitlines(keepends=True) if b'"usage"' in raw
    ]
    assert sidechain and all(json.loads(raw)['isSidechain'] for raw in sidechain)
    n, offset, turns = Normaliser('s'), 0, []
    for raw in [*lines[:first_end], *sidechain, lines[first_end]]:
        offset += len(raw)
        turns += [e for e in n.line(raw, offset) if e['type'] == 'turn_end']
    [turn] = [valid(t) for t in turns]
    assert turn['data']['usage'] == _main_counts_before_each_turn_end(T1)[0]


def test_a_subagent_stream_never_carries_usage() -> None:
    """Control: a file under `subagents/` never counts, even where a line lacks `isSidechain`."""
    n = Normaliser('s', subagent_id='a918dd7c41e7ee8b8')
    for i, raw in enumerate(SUBAGENT.read_bytes().splitlines(keepends=True)):
        entry = json.loads(raw)
        entry.pop('isSidechain', None)
        n.line(json.dumps(entry).encode(), i)
    end = {'type': 'system', 'subtype': 'turn_duration', 'durationMs': 5}
    [turn] = one({**end, 'isSidechain': False}, n)
    assert 'usage' not in turn['data']


def test_usage_is_cleared_at_each_turn_end() -> None:
    n = Normaliser('s')
    counts = dict.fromkeys(USAGE_KEYS, 3)
    one({'type': 'assistant', 'message': {'content': [], 'usage': counts}}, n)
    end = {'type': 'system', 'subtype': 'turn_duration', 'durationMs': 5}
    [first] = one(end, n)
    [second] = one(end, n)
    assert first['data']['usage'] == counts
    assert 'usage' not in second['data']


@pytest.mark.parametrize(
    'usage',
    [
        {'input_tokens': 1, 'cache_creation_input_tokens': 2, 'cache_read_input_tokens': 3},
        {**dict.fromkeys(USAGE_KEYS, 1), 'output_tokens': -1},
        {**dict.fromkeys(USAGE_KEYS, 1), 'output_tokens': True},
        {**dict.fromkeys(USAGE_KEYS, 1), 'output_tokens': '4'},
        'lots',
    ],
)
def test_incomplete_counts_keep_the_ones_before(usage: Any) -> None:
    n = Normaliser('s')
    good = dict.fromkeys(USAGE_KEYS, 7)
    one({'type': 'assistant', 'message': {'content': [], 'usage': good}}, n)
    one({'type': 'assistant', 'message': {'content': [], 'usage': usage}}, n)
    [turn] = one({'type': 'system', 'subtype': 'turn_duration', 'durationMs': 1}, n)
    assert turn['data']['usage'] == good


def test_a_reread_file_forgets_the_counts() -> None:
    n = Normaliser('s')
    one({'type': 'assistant', 'message': {'content': [], 'usage': dict.fromkeys(USAGE_KEYS, 1)}}, n)
    n.reset()
    [turn] = one({'type': 'system', 'subtype': 'turn_duration', 'durationMs': 1}, n)
    assert 'usage' not in turn['data']


@pytest.mark.parametrize('turn', [0, 1, 2])
def test_a_resume_between_the_last_answer_and_the_turn_end_keeps_the_counts(
    tmp_path: Path, turn: int
) -> None:
    """A tail resumed after a turn's last assistant line still sends that turn's counts."""
    path = tmp_path / 't1.jsonl'
    path.write_bytes(T1.read_bytes())
    lines = T1.read_bytes().splitlines(keepends=True)
    ends = [i for i, raw in enumerate(lines) if b'turn_duration' in raw]
    pos = sum(len(x) for x in lines[: ends[turn]])
    n = Normaliser('s')
    transcript._agents_before(n, path, pos, transcript.MAX_LINE)
    [end] = n.line(lines[ends[turn]], pos + len(lines[ends[turn]]))
    assert end['data']['usage'] == _main_counts_before_each_turn_end(T1)[turn]


def test_a_resume_right_after_a_turn_end_carries_nothing_from_before_it(tmp_path: Path) -> None:
    path = tmp_path / 't.jsonl'
    entries = [
        {'type': 'assistant', 'message': {'content': [], 'usage': dict.fromkeys(USAGE_KEYS, 1)}},
        {'type': 'system', 'subtype': 'turn_duration', 'durationMs': 1},
    ]
    path.write_bytes(b''.join(json.dumps(e).encode() + b'\n' for e in entries))
    n = Normaliser('s')
    transcript._agents_before(n, path, path.stat().st_size, transcript.MAX_LINE)
    assert n.usage == {}


def test_a_subagent_transcript_carries_its_id_on_every_event() -> None:
    events = normalise_file(SUBAGENT, subagent_id='a918dd7c41e7ee8b8')
    assert Counter(e['type'] for e in events) == {'text': 1, 'tool_call': 2, 'tool_result': 2}
    assert {e['subagent_id'] for e in events} == {'a918dd7c41e7ee8b8'}


def test_subagent_start_names_what_the_agent_call_asked_for() -> None:
    [start] = [
        e for e in normalise_file(FIXTURES / 't1-happy-path.jsonl') if e['type'] == 'subagent_start'
    ]
    assert start['data'] == {
        'tool_use_id': 'toolu_01S8KjA6XyNXa1ku18fcsiHj',
        'agent_id': 'a918dd7c41e7ee8b8',
        'description': 'Find buggy function',
        'subagent_type': 'general-purpose',
    }


def test_denied_tool_use_is_marked_and_the_fix_is_a_diff() -> None:
    [denied] = [
        e
        for e in normalise_file(FIXTURES / 't3-permission-denied.jsonl')
        if e['type'] == 'tool_result'
    ]
    assert denied['data']['denied'] is True
    diffs = [e for e in normalise_file(FIXTURES / 't1-happy-path.jsonl') if e['type'] == 'diff']
    lines = [x for d in diffs for h in d['data']['hunks'] for x in h['lines']]
    assert '+    return a - b' in lines and '-    return a + b' in lines


def test_the_usage_limit_is_flagged_and_an_api_error_is_not() -> None:
    [limit] = [
        e for e in normalise_file(FIXTURES / 'synth-limit-reached.jsonl') if e['type'] == 'error'
    ]
    [api] = [e for e in normalise_file(FIXTURES / 'synth-api-error.jsonl') if e['type'] == 'error']
    assert limit['data']['usage_limit'] is True and limit['data']['api_error_status'] == 429
    assert api['data']['usage_limit'] is False


# --- lines nobody has seen ---------------------------------------------------------------------


def test_an_unknown_entry_type_becomes_raw_and_does_not_crash() -> None:
    [event] = one({'type': 'brand-new-thing', 'x': 1, 'timestamp': '2026-10-12T10:00:00Z'})
    assert event['type'] == 'raw'
    assert event['data'] == {
        'entry_type': 'brand-new-thing',
        'entry': {'type': 'brand-new-thing', 'x': 1, 'timestamp': '2026-10-12T10:00:00Z'},
    }
    assert event['at'] == '2026-10-12T10:00:00Z'


def test_a_huge_unknown_entry_is_cut_down() -> None:
    [event] = one({'type': 'brand-new-thing', 'blob': 'x' * 20000})
    assert event['data'] == {
        'entry_type': 'brand-new-thing',
        'entry': {'type': 'brand-new-thing', 'truncated': True},
    }


@pytest.mark.parametrize(
    'raw', [b'{"type": "assistant", "mess\n', b'[1, 2]\n', b'"text"\n', b'\xff\xfe\n']
)
def test_a_line_that_is_not_a_json_object_is_raw_unreadable(raw: bytes) -> None:
    [event] = Normaliser('s').line(raw, 5)
    assert valid(event)['data'] == {'entry_type': 'unreadable'}


@pytest.mark.parametrize(
    'entry',
    [
        {'type': 'assistant', 'message': 'not an object'},
        {'type': 'assistant', 'message': {'content': [{'type': 'tool_use', 'input': {}}]}},
        {'type': 'user', 'message': {'content': [{'type': 'tool_result'}]}},
        {
            'type': 'user',
            'message': {'content': [{'type': 'tool_result', 'tool_use_id': 't', 'content': 'ok'}]},
            'toolUseResult': {'structuredPatch': [{'oldStart': 'x'}]},
        },
    ],
)
def test_a_known_type_in_an_unknown_shape_becomes_raw(entry: dict[str, Any]) -> None:
    [event] = one(entry)
    assert event['type'] == 'raw' and event['data']['entry_type'] == entry['type']


def test_odd_but_harmless_shapes_are_skipped_not_raw() -> None:
    entry = {
        'type': 'assistant',
        'message': {'content': ['a string', {'type': 'text', 'text': ''}, {'type': 'image'}]},
    }
    assert one(entry) == []
    assert one({'type': 'assistant', 'message': {'content': 'just text'}}) == []
    assert one({'type': 'user', 'isMeta': True, 'message': {'content': 'meta'}}) == []
    assert one({'type': 'system', 'subtype': 'compact_boundary'}) == []


def test_a_turn_end_without_counts_and_a_tool_call_without_input() -> None:
    [turn] = one(
        {'type': 'system', 'subtype': 'turn_duration', 'durationMs': 'soon', 'messageCount': -1}
    )
    assert turn['data'] == {'duration_ms': 0}
    call = {'type': 'tool_use', 'id': 't1', 'name': 'TodoWrite', 'input': None}
    [event] = one({'type': 'assistant', 'message': {'content': [call]}})
    assert event['data'] == {
        'tool_use_id': 't1',
        'tool': 'TodoWrite',
        'summary': 'TodoWrite',
        'input': {},
    }


def test_each_string_of_a_tool_input_is_cut_to_8_kb() -> None:
    tool_input = {'command': 'echo ' + 'y' * 9000, 'nested': {'list': ['z' * 9000, 3]}}
    call = {'type': 'tool_use', 'id': 't1', 'name': 'Bash', 'input': tool_input}
    [event] = one({'type': 'assistant', 'message': {'content': [call]}})
    data = event['data']
    assert len(data['input']['command']) == 8192
    assert (
        len(data['input']['nested']['list'][0]) == 8192 and data['input']['nested']['list'][1] == 3
    )
    assert len(data['summary']) == 200


def test_a_long_tool_result_is_cut_and_marked() -> None:
    block = {
        'type': 'tool_result',
        'tool_use_id': 't',
        'content': [{'type': 'text', 'text': 'r' * 9000}],
    }
    [event] = one({'type': 'user', 'message': {'content': [block]}})
    assert len(event['data']['text']) == 8192 and event['data']['truncated'] is True


def test_a_line_without_a_time_takes_the_one_before_it() -> None:
    n = Normaliser('s')
    one({'type': 'user', 'timestamp': '2026-10-12T10:00:00.000Z', 'message': {'content': 'hi'}}, n)
    [event] = one({'type': 'ai-title', 'aiTitle': 'x'}, n)
    assert event['at'] == '2026-10-12T10:00:00.000Z'
    [first] = one({'type': 'ai-title', 'timestamp': 'not a time'})
    datetime.fromisoformat(first['at'].replace('Z', '+00:00'))  # now, not 'not a time'


def test_a_hand_back_without_the_marker_has_status_unknown() -> None:
    text = (
        'Another Claude session sent a message:\n<agent-message from="abc123">done</agent-message>'
    )
    [event] = one({'type': 'user', 'isMeta': True, 'message': {'content': text}})
    assert event['data']['status'] == 'unknown' and event['data']['agent_id'] == 'abc123'


def test_an_agent_launch_with_no_known_call_is_still_a_subagent_start() -> None:
    block = {
        'type': 'tool_result',
        'tool_use_id': 't9',
        'content': 'Async agent launched. agentId: abc',
    }
    events = one({'type': 'user', 'message': {'content': [block]}})
    assert events[-1]['data'] == {'tool_use_id': 't9', 'agent_id': 'abc', 'description': 'subagent'}


def test_remembered_agent_calls_are_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(transcript, 'MAX_AGENTS', 3)
    n = Normaliser('s')
    for i in range(10):
        call = {
            'type': 'tool_use',
            'id': f't{i}',
            'name': 'Agent',
            'input': {'description': f'd{i}'},
        }
        one({'type': 'assistant', 'message': {'content': [call]}}, n)
    assert list(n.agents) == ['t7', 't8', 't9']
    n.reset()
    assert n.agents == {}


def test_a_too_long_line_is_a_raw_event() -> None:
    [event] = Normaliser('s', 'abc').too_long(500, 400)
    assert valid(event)['data'] == {
        'entry_type': 'line-too-long',
        'entry': {'type': 'line-too-long', 'bytes': 400, 'truncated': True},
    }
    assert event['subagent_id'] == 'abc' and event['offset'] == 500


# --- reading the file --------------------------------------------------------------------------


def read(reader: LineReader) -> list[tuple[int, int, bytes | None]]:
    return list(reader.lines())


def test_complete_lines_come_with_their_end_offset(tmp_path: Path) -> None:
    f = tmp_path / 't.jsonl'
    f.write_bytes(b'one\ntwo\n')
    assert read(LineReader(lambda: f)) == [(0, 4, b'one\n'), (4, 8, b'two\n')]


def test_a_partial_last_line_waits_for_its_newline(tmp_path: Path) -> None:
    """Control: a line still being written is never sent half."""
    f = tmp_path / 't.jsonl'
    f.write_bytes(b'one\ntw')
    reader = LineReader(lambda: f)
    assert read(reader) == [(0, 4, b'one\n')]
    assert read(reader) == []
    with f.open('ab') as out:
        out.write(b'o\n')
    assert read(reader) == [(4, 8, b'two\n')]


def test_resuming_from_an_offset_gives_only_what_follows(tmp_path: Path) -> None:
    f = tmp_path / 't.jsonl'
    f.write_bytes(b'one\ntwo\nthree\n')
    assert read(LineReader(lambda: f, 4)) == [(4, 8, b'two\n'), (8, 14, b'three\n')]
    assert read(LineReader(lambda: f, 14)) == []


@pytest.mark.parametrize('offset', [5, 6, 7])
def test_an_offset_inside_a_line_starts_at_that_line(tmp_path: Path, offset: int) -> None:
    f = tmp_path / 't.jsonl'
    f.write_bytes(b'one\ntwo\nthree\n')
    assert read(LineReader(lambda: f, offset))[0] == (4, 8, b'two\n')


def test_an_offset_inside_a_long_first_line_starts_at_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(transcript, 'CHUNK', 4)  # look back over several chunks
    f = tmp_path / 't.jsonl'
    f.write_bytes(b'a' * 30 + b'\n' + b'b' * 30 + b'\n')
    assert read(LineReader(lambda: f, 20))[0][:2] == (0, 31)
    assert read(LineReader(lambda: f, 50))[0][:2] == (31, 62)


def test_an_offset_past_the_end_reads_the_file_again(tmp_path: Path) -> None:
    f = tmp_path / 't.jsonl'
    f.write_bytes(b'one\n')
    reader = LineReader(lambda: f, 400)
    assert read(reader) == [(0, 4, b'one\n')]
    assert reader.restarts == 1


def test_a_file_that_does_not_exist_yet_is_waited_for(tmp_path: Path) -> None:
    f = tmp_path / 't.jsonl'
    reader = LineReader(lambda: f if f.exists() else None)
    assert read(reader) == []
    f.write_bytes(b'one\n')
    assert read(reader) == [(0, 4, b'one\n')]


def test_a_file_that_cannot_be_opened_is_waited_for(tmp_path: Path) -> None:
    reader = LineReader(lambda: tmp_path / 'missing.jsonl')
    assert read(reader) == []


def test_a_truncated_file_is_read_again_from_the_start(tmp_path: Path) -> None:
    f = tmp_path / 't.jsonl'
    f.write_bytes(b'one\ntwo\n')
    reader = LineReader(lambda: f)
    read(reader)
    f.write_bytes(b'new\n')
    assert read(reader) == [(0, 4, b'new\n')]
    assert reader.restarts == 1


def test_a_replaced_file_is_finished_then_the_new_one_read_from_the_start(tmp_path: Path) -> None:
    f = tmp_path / 't.jsonl'
    f.write_bytes(b'one\n')
    reader = LineReader(lambda: f)
    assert read(reader) == [(0, 4, b'one\n')]
    with f.open('ab') as out:  # the old file gets one more line before it is replaced
        out.write(b'two\n')
    new = tmp_path / 'new.jsonl'
    new.write_bytes(b'fresh start\n')
    os.replace(new, f)
    assert read(reader) == [(4, 8, b'two\n')]
    assert reader.restarts == 1
    assert read(reader) == [(0, 12, b'fresh start\n')]


def test_a_deleted_file_is_waited_for_and_read_from_the_start(tmp_path: Path) -> None:
    f = tmp_path / 't.jsonl'
    f.write_bytes(b'one\n')
    reader = LineReader(lambda: f if f.exists() else None)
    read(reader)
    f.unlink()
    assert read(reader) == []
    assert read(reader) == []
    f.write_bytes(b'two\n')
    assert read(reader) == [(0, 4, b'two\n')]


def test_a_too_long_line_is_skipped_without_holding_it(tmp_path: Path) -> None:
    """Control: a line over the limit is never returned (nor held) whole."""
    f = tmp_path / 't.jsonl'
    f.write_bytes(b'short\n' + b'x' * 1000 + b'\n' + b'after\n')
    got = read(LineReader(lambda: f, max_line=100))
    assert got == [(0, 6, b'short\n'), (6, 1007, None), (1007, 1013, b'after\n')]


def test_a_too_long_line_still_being_written_waits_for_its_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(transcript, 'CHUNK', 64)
    f = tmp_path / 't.jsonl'
    f.write_bytes(b'x' * 500)
    reader = LineReader(lambda: f, max_line=100)
    assert read(reader) == []
    with f.open('ab') as out:
        out.write(b'y' * 500)
    assert read(reader) == []
    with f.open('ab') as out:
        out.write(b'\nnext\n')
    assert read(reader) == [(0, 1001, None), (1001, 1006, b'next\n')]


def test_on_open_is_told_where_reading_starts(tmp_path: Path) -> None:
    f = tmp_path / 't.jsonl'
    f.write_bytes(b'one\ntwo\n')
    opened: list[tuple[Path, int]] = []
    read(LineReader(lambda: f, 6, on_open=lambda p, pos: opened.append((p, pos))))
    assert opened == [(f, 4)]


# --- finding the file --------------------------------------------------------------------------

SID = '7aa6a32d-dc9c-43e3-b170-ee90995ba343'


def test_the_transcript_is_in_the_folder_named_after_the_cwd(tmp_path: Path) -> None:
    folder = tmp_path / '.claude' / 'projects' / '-home-me-work-marcel-wt-tsk-01'
    folder.mkdir(parents=True)
    (folder / f'{SID}.jsonl').write_text('')
    assert (
        transcript_path(tmp_path, SID, '/home/me/work/marcel-wt/tsk_01') == folder / f'{SID}.jsonl'
    )


def test_another_folder_name_is_found_by_the_session_id(tmp_path: Path) -> None:
    """The folder rule is only recorded for `/`; a differently named folder is still found."""
    folder = tmp_path / '.claude' / 'projects' / '-home-me-work-marcel-wt-tsk_01'
    (folder / SID / 'subagents').mkdir(parents=True)
    (folder / f'{SID}.jsonl').write_text('')
    (folder / SID / 'subagents' / 'agent-abc.jsonl').write_text('')
    cwd = '/home/me/work/marcel-wt/tsk_01'
    assert transcript_path(tmp_path, SID, cwd) == folder / f'{SID}.jsonl'
    assert (
        transcript_path(tmp_path, SID, cwd, 'abc') == folder / SID / 'subagents' / 'agent-abc.jsonl'
    )
    assert transcript_path(tmp_path, SID, '') == folder / f'{SID}.jsonl'


def test_no_file_yet_is_none_and_an_odd_id_is_never_globbed(tmp_path: Path) -> None:
    (tmp_path / '.claude' / 'projects' / 'p').mkdir(parents=True)
    (tmp_path / '.claude' / 'projects' / 'p' / 'x.jsonl').write_text('')
    assert transcript_path(tmp_path, SID, '/home/me') is None
    assert transcript_path(tmp_path, '*', '/home/me') is None


def test_a_waiting_tail_searches_every_folder_at_most_once_every_few_seconds(
    tmp_path: Path,
) -> None:
    """While the file does not exist, the expected folder is looked at on every poll, but the
    search through every project folder runs at most once per `search_every` seconds."""
    projects = tmp_path / '.claude' / 'projects'
    (projects / 'other').mkdir(parents=True)
    now = [0.0]
    locate = Locator(tmp_path, SID, '/home/me/work', search_every=5.0, clock=lambda: now[0])
    for _ in range(10):  # 2.5 s of polls, four a second
        assert locate() is None
        now[0] += 0.25
    assert locate.searches == 1

    found = projects / 'other' / f'{SID}.jsonl'
    found.write_text('')
    assert locate() is None  # not searched again yet
    now[0] = 5.0
    assert locate() == found
    assert locate.searches == 2

    expected = projects / '-home-me-work' / f'{SID}.jsonl'
    expected.parent.mkdir()
    expected.write_text('')
    assert locate() == expected  # the expected folder is never throttled
    assert locate.searches == 2
