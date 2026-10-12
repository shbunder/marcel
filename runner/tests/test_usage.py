"""The usage probe: `claude -p /usage` parsed into the contract's `UsageReading`."""

import json
import re
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from conftest import write_script

from marcel_runner import server, usage
from marcel_runner.usage import UsageProbe, UsageUnreadable, parse_usage

# The /usage text recorded on the NUC with Claude Code 2.1.289 (SP4). The contract test below reads
# it as-is: if Claude Code changes its wording, re-record it and this file says what broke.
RECORDED = Path(__file__).parents[2] / 'scratch/spikes/SP4-usage/evidence/usage-p-text.txt'
# When SP4 ran the command: 2026-10-04 ~10:30 UTC.
TAKEN = datetime(2026, 10, 4, 10, 30, 12, tzinfo=UTC)

READING_KEYS = {'five_hour', 'weekly', 'source', 'taken_at', 'stale'}
ISO_Z = re.compile(r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$')


class Clock:
    def __init__(self, now: datetime = TAKEN) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def script(tmp_path: Path, body: str) -> Path:
    return write_script(tmp_path / 'claude', body)


def recorded_claude(tmp_path: Path) -> Path:
    """A claude that prints the recorded /usage text, and checks it was asked the SP4 way."""
    return script(
        tmp_path,
        'if [ "$*" != "-p /usage --no-session-persistence" ]; then\n'
        '  echo "asked: $*" >&2; exit 2\n'
        'fi\n'
        f'cat "{RECORDED}"\n',
    )


def assert_contract_shape(body: dict) -> None:
    """`UsageReading` in contracts/runner-api.yaml: required keys, no others, right types."""
    assert set(body) == READING_KEYS
    assert body['source'] in {'cli_usage', 'statusline'}
    assert isinstance(body['stale'], bool)
    assert ISO_Z.match(body['taken_at'])
    for window in ('five_hour', 'weekly'):
        assert set(body[window]) == {'used_pct', 'resets_at'}
        assert 0 <= body[window]['used_pct'] <= 100
        assert ISO_Z.match(body[window]['resets_at'])


# --- the contract test on the recorded text ---------------------------------------------------


def test_recorded_usage_text_parses_to_both_windows() -> None:
    windows = parse_usage(RECORDED.read_text('utf-8'), TAKEN)
    assert windows == {
        'five_hour': {'used_pct': 4, 'resets_at': '2026-10-04T14:29:00Z'},
        'weekly': {'used_pct': 66, 'resets_at': '2026-10-09T01:59:00Z'},
    }


def test_the_fake_claude_fixture_is_the_recorded_text() -> None:
    fake = Path(__file__).parent / 'fake_claude/fixtures/usage/usage-p-text.txt'
    assert fake.read_text('utf-8') == RECORDED.read_text('utf-8')


def test_the_json_wrapper_carries_the_same_text_with_no_model_call() -> None:
    record = json.loads((RECORDED.parent / 'usage-p-json.json').read_text('utf-8'))
    assert record['num_turns'] == 0 and record['total_cost_usd'] == 0
    assert parse_usage(record['result'], TAKEN) == parse_usage(RECORDED.read_text('utf-8'), TAKEN)


# --- wording changes fail loud and readable ---------------------------------------------------


def test_changed_wording_names_the_missing_line_and_where_to_fix_it() -> None:
    text = RECORDED.read_text('utf-8').replace('Current session:', 'This session:')
    with pytest.raises(UsageUnreadable) as e:
        parse_usage(text, TAKEN)
    assert 'Current session' in str(e.value)
    assert 'usage.py' in str(e.value)
    assert 'This session: 4% used' in e.value.detail


def test_missing_weekly_line_is_named_too() -> None:
    text = '\n'.join(
        line for line in RECORDED.read_text('utf-8').splitlines() if 'all models' not in line
    )
    with pytest.raises(UsageUnreadable, match=r'Current week \(all models\)'):
        parse_usage(text, TAKEN)


def test_empty_output_is_unreadable_not_a_crash() -> None:
    with pytest.raises(UsageUnreadable) as e:
        parse_usage('', TAKEN)
    assert e.value.detail == '(no output)'


def test_unknown_reset_time_format_is_unreadable() -> None:
    text = 'Current session: 4% used · resets tomorrow (UTC)\n' + RECORDED.read_text('utf-8')
    with pytest.raises(UsageUnreadable, match='tomorrow'):
        parse_usage(text, TAKEN)


def test_unknown_timezone_is_unreadable() -> None:
    text = RECORDED.read_text('utf-8').replace('2:29pm (UTC)', '2:29pm (Nowhere)')
    with pytest.raises(UsageUnreadable, match='Nowhere'):
        parse_usage(text, TAKEN)


# --- reset times ------------------------------------------------------------------------------


def test_reset_without_minutes_parses() -> None:
    text = (
        'Current session: 10% used · resets Oct 4, 2pm (UTC)\n'
        'Current week (all models): 20% used · resets Oct 9, 2am (UTC)\n'
    )
    windows = parse_usage(text, TAKEN)
    assert windows['five_hour']['resets_at'] == '2026-10-04T14:00:00Z'
    assert windows['weekly']['resets_at'] == '2026-10-09T02:00:00Z'


def test_reset_in_january_seen_in_december_is_next_year() -> None:
    text = (
        'Current session: 1% used · resets Dec 31, 11pm (UTC)\n'
        'Current week (all models): 1% used · resets Jan 2, 1am (UTC)\n'
    )
    windows = parse_usage(text, datetime(2026, 12, 31, 20, 0, tzinfo=UTC))
    assert windows['weekly']['resets_at'] == '2027-01-02T01:00:00Z'


def test_local_timezone_is_converted_to_utc() -> None:
    text = (
        'Current session: 1% used · resets Oct 4, 4:29pm (CET)\n'
        'Current week (all models): 1% used · resets Oct 9, 1:59am (UTC)\n'
    )
    # CET in tzdata follows summer time: 16:29 CEST is 14:29 UTC.
    assert parse_usage(text, TAKEN)['five_hour']['resets_at'] == '2026-10-04T14:29:00Z'


def test_used_pct_over_100_is_capped_at_the_contract_maximum() -> None:
    text = (
        'Current session: 104% used · resets Oct 4, 2pm (UTC)\n'
        'Current week (all models): 100% used · resets Oct 9, 2am (UTC)\n'
    )
    assert parse_usage(text, TAKEN)['five_hour']['used_pct'] == 100


# --- the probe --------------------------------------------------------------------------------


def test_probe_reads_claude_the_sp4_way_and_answers_a_contract_reading(tmp_path: Path) -> None:
    probe = UsageProbe(recorded_claude(tmp_path), clock=Clock())
    probe.refresh()
    code, body = probe.reading()
    assert code == 200
    assert_contract_shape(body)
    assert body == {
        'five_hour': {'used_pct': 4, 'resets_at': '2026-10-04T14:29:00Z'},
        'weekly': {'used_pct': 66, 'resets_at': '2026-10-09T01:59:00Z'},
        'source': 'cli_usage',
        'taken_at': '2026-10-04T10:30:12Z',
        'stale': False,
    }


def test_before_any_reading_the_probe_answers_503_no_reading(tmp_path: Path) -> None:
    code, body = UsageProbe(recorded_claude(tmp_path), clock=Clock()).reading()
    assert code == 503
    assert body['code'] == 'no_reading'
    assert set(body) <= {'code', 'message', 'detail'}


def test_first_reading_failing_is_503_with_what_claude_said(tmp_path: Path) -> None:
    probe = UsageProbe(script(tmp_path, 'echo "login required" >&2; exit 1\n'), clock=Clock())
    probe.refresh()
    code, body = probe.reading()
    assert code == 503
    assert body['code'] == 'no_reading'
    assert 'login required' in body['detail']


def test_source_unavailable_keeps_last_values_and_says_stale(tmp_path: Path) -> None:
    claude = recorded_claude(tmp_path)
    clock = Clock()
    probe = UsageProbe(claude, clock=clock)
    probe.refresh()
    _, fresh = probe.reading()

    claude.unlink()  # the source goes away: claude is gone
    clock.now = TAKEN + timedelta(minutes=5)
    probe.refresh()
    code, body = probe.reading()
    assert code == 200
    assert_contract_shape(body)
    assert body['stale'] is True
    assert body['five_hour'] == fresh['five_hour']
    assert body['weekly'] == fresh['weekly']
    assert body['taken_at'] == fresh['taken_at']


def test_recovery_after_a_failure_is_fresh_again(tmp_path: Path) -> None:
    good = RECORDED.read_text('utf-8')
    flag = tmp_path / 'broken'
    claude = script(tmp_path, f'if [ -e "{flag}" ]; then exit 1; fi\ncat "{RECORDED}"\n')
    probe = UsageProbe(claude, clock=Clock())
    probe.refresh()
    flag.touch()
    probe.refresh()
    assert probe.reading()[1]['stale'] is True
    flag.unlink()
    probe.refresh()
    assert probe.reading()[1]['stale'] is False
    assert good  # the recorded text is not empty


def test_reading_older_than_15_minutes_is_stale_even_without_a_failure(tmp_path: Path) -> None:
    clock = Clock()
    probe = UsageProbe(recorded_claude(tmp_path), clock=clock)
    probe.refresh()
    clock.now = TAKEN + timedelta(minutes=15)
    assert probe.reading()[1]['stale'] is False
    clock.now = TAKEN + timedelta(minutes=15, seconds=1)
    assert probe.reading()[1]['stale'] is True


def test_changed_wording_in_live_output_is_stale_not_a_crash(tmp_path: Path) -> None:
    clock = Clock()
    claude = recorded_claude(tmp_path)
    probe = UsageProbe(claude, clock=clock)
    probe.refresh()
    script(tmp_path, 'echo "Session usage: 4 percent"\n')
    probe.refresh()
    code, body = probe.reading()
    assert code == 200 and body['stale'] is True
    assert 'Current session' in (probe.last_error or '')


@pytest.mark.parametrize(
    ('body', 'expect'),
    [
        ('exit 3\n', 'exit code 3'),
        ('echo "boom" >&2; exit 1\n', 'boom'),
    ],
)
def test_every_failure_has_a_plain_reason(tmp_path: Path, body: str, expect: str) -> None:
    probe = UsageProbe(script(tmp_path, body), clock=Clock())
    probe.refresh()
    assert expect in (probe.last_error or '')
    assert probe.reading()[0] == 503


def test_a_hanging_claude_times_out_with_a_plain_reason(tmp_path: Path) -> None:
    # `exec` so the timeout kills the sleep itself, not a shell that leaves it holding the pipe.
    probe = UsageProbe(script(tmp_path, 'exec sleep 30\n'), timeout=1, clock=Clock())
    probe.refresh()
    assert 'did not answer within 1 s' in (probe.last_error or '')
    assert probe.reading()[0] == 503


def test_claude_is_run_with_tz_utc_so_reset_zones_are_one_word(tmp_path: Path) -> None:
    claude = script(tmp_path, f'[ "$TZ" = "UTC" ] || exit 1\ncat "{RECORDED}"\n')
    for env in (None, {'TZ': 'Europe/Brussels', 'PATH': '/usr/bin:/bin'}):
        probe = UsageProbe(claude, env=env, clock=Clock())
        probe.refresh()
        assert probe.reading()[0] == 200, probe.last_error


def test_missing_claude_has_a_plain_reason(tmp_path: Path) -> None:
    probe = UsageProbe(tmp_path / 'absent', clock=Clock())
    probe.refresh()
    assert 'absent' in (probe.last_error or '')


def test_non_executable_claude_has_a_plain_reason(tmp_path: Path) -> None:
    plain = tmp_path / 'claude'
    plain.write_text('not a program')
    probe = UsageProbe(plain, clock=Clock())
    probe.refresh()
    assert 'cannot run' in (probe.last_error or '')


def test_failures_are_logged_once_per_change(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    probe = UsageProbe(script(tmp_path, 'exit 1\n'), clock=Clock())
    probe.refresh()
    probe.refresh()
    assert len(capsys.readouterr().err.splitlines()) == 1


def test_probe_runs_claude_with_its_env(tmp_path: Path) -> None:
    claude = script(tmp_path, f'[ "$MARK" = "yes" ] || exit 1\ncat "{RECORDED}"\n')
    probe = UsageProbe(claude, env={'MARK': 'yes', 'PATH': '/usr/bin:/bin'}, clock=Clock())
    probe.refresh()
    assert probe.reading()[0] == 200


def test_poll_loop_reads_immediately_and_stops(tmp_path: Path) -> None:
    probe = UsageProbe(recorded_claude(tmp_path), clock=Clock())
    thread = probe.start(every=60)
    try:
        deadline = datetime.now() + timedelta(seconds=10)
        while probe.reading()[0] != 200 and datetime.now() < deadline:
            threading.Event().wait(0.02)
        assert probe.reading()[0] == 200
    finally:
        probe.stop()
    thread.join(5)
    assert not thread.is_alive()


def test_default_clock_is_utc_now(tmp_path: Path) -> None:
    probe = UsageProbe(recorded_claude(tmp_path))
    before = datetime.now(UTC)
    probe.refresh()
    _, body = probe.reading()
    taken = datetime.strptime(body['taken_at'], '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=UTC)
    assert before - timedelta(seconds=1) <= taken <= datetime.now(UTC)


def test_the_poll_interval_and_staleness_match_the_contract() -> None:
    # contracts/runner-api.yaml getUsage: poll every 5 minutes, stale after 15.
    assert usage.POLL_EVERY == 300
    assert timedelta(minutes=15) == usage.STALE_AFTER


def test_main_starts_the_usage_poll(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    started: list[bool] = []

    class Probe:
        def start(self) -> None:
            started.append(True)

    class Srv:
        usage = Probe()

        def serve_forever(self) -> None:
            raise KeyboardInterrupt

        def server_close(self) -> None:
            pass

    monkeypatch.setenv('MARCEL_RUNNER_SOCK', str(tmp_path / 'runner.sock'))
    monkeypatch.setenv('MARCEL_CLAUDE', str(tmp_path / 'claude'))
    monkeypatch.setattr(server, 'serve', lambda *a, **kw: Srv())
    assert server.main() == 0
    assert started == [True]
