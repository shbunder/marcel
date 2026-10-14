"""The usage probe: `claude -p /usage` parsed into the contract's `UsageReading`."""

import json
import re
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from conftest import runner_api, write_script

from marcel_runner import server, usage
from marcel_runner.login import LOOK_EVERY, LOST_MESSAGE, LoginCheck
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


USAGE_READING = runner_api('UsageReading')


def assert_contract_shape(body: dict) -> None:
    """`UsageReading` in contracts/runner-api.yaml: required keys, no others, right types."""
    USAGE_READING.validate(body)
    assert set(body) - {'reason'} == READING_KEYS
    assert 'reason' not in body or body['stale'] is True
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


def test_first_reading_failing_is_503_with_the_cause_in_words(tmp_path: Path) -> None:
    """What claude printed goes to the runner's journal only (it can name the account)."""
    claude = script(tmp_path, 'echo "login required" >&2; exit 1\n')
    probe = UsageProbe(claude, clock=Clock())
    probe.refresh()
    code, body = probe.reading()
    assert code == 503
    assert body['code'] == 'no_reading'
    assert body['detail'] == f'`{claude} -p /usage` failed with exit code 1.'
    assert 'login required' not in json.dumps(body)


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
        ('echo "boom" >&2; exit 1\n', 'exit code 1'),
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


# --- Claude Code 2.1.299 (recorded on the NUC, 2026-10-14 ~08:00 UTC) -------------------------

FIXTURES = Path(__file__).parent / 'test_usage_fixtures'
TAKEN_299 = datetime(2026, 10, 14, 8, 0, tzinfo=UTC)


def test_2_1_299_text_parses_to_both_windows() -> None:
    """2.1.299 adds a per-model weekly line and a "what's contributing" section, both ignored."""
    windows = parse_usage((FIXTURES / 'usage-2.1.299-text.txt').read_text('utf-8'), TAKEN_299)
    assert windows == {
        'five_hour': {'used_pct': 18, 'resets_at': '2026-10-14T09:40:00Z'},
        'weekly': {'used_pct': 44, 'resets_at': '2026-10-16T02:00:00Z'},
    }


def test_logged_out_claude_says_log_in_not_that_the_wording_changed() -> None:
    """Logged out, or its login expired (2026-10-14 02:52), /usage prints the cost view."""
    text = (FIXTURES / 'usage-2.1.299-logged-out.txt').read_text('utf-8')
    with pytest.raises(UsageUnreadable) as e:
        parse_usage(text, TAKEN_299)
    # The same sentence as Health.login.message, so the owner reads one sentence in both places.
    assert str(e.value) == LOST_MESSAGE
    assert e.value.login_lost is True
    assert e.value.detail.startswith('Total cost:')


def test_logged_out_on_the_first_read_answers_no_reading_and_says_why(tmp_path: Path) -> None:
    claude = script(tmp_path, f'cat "{FIXTURES / "usage-2.1.299-logged-out.txt"}"\n')
    probe = UsageProbe(claude, clock=Clock(TAKEN_299))
    probe.refresh()
    status, body = probe.reading()
    assert status == 503 and body['code'] == 'no_reading'
    assert body['detail'] == LOST_MESSAGE
    assert 'reason' not in body  # no_reading has none: /health `login` tells the hub


def test_logged_out_after_a_good_reading_keeps_it_stale_and_says_log_in(tmp_path: Path) -> None:
    """2026-10-14: a good reading at 02:51, then the login refresh failed at 02:52."""
    out = tmp_path / 'out.txt'
    out.write_text((FIXTURES / 'usage-2.1.299-text.txt').read_text('utf-8'))
    claude = script(tmp_path, f'cat "{out}"\n')
    clock = Clock(TAKEN_299)
    probe = UsageProbe(claude, clock=clock)
    probe.refresh()
    out.write_text((FIXTURES / 'usage-2.1.299-logged-out.txt').read_text('utf-8'))
    clock.now = TAKEN_299 + timedelta(minutes=5)
    probe.refresh()
    status, body = probe.reading()
    assert status == 200 and body['stale'] is True
    assert body['five_hour']['used_pct'] == 18 and body['taken_at'] == '2026-10-14T08:00:00Z'
    assert body['reason'] == LOST_MESSAGE
    assert_contract_shape(body)


def test_the_usage_lines_win_over_a_cost_line_in_the_same_output() -> None:
    """The cost view explains only output with no usage line, not a cost line beside one."""
    recorded = (FIXTURES / 'usage-2.1.299-text.txt').read_text('utf-8')
    text = recorded + '\nTotal cost:            $0.0000\n'
    assert parse_usage(text, TAKEN_299)['five_hour']['used_pct'] == 18


# --- UsageReading.reason and the login (runner-api 0.1.13, S-03.13) ------------------------------


def test_a_failed_read_after_a_good_one_says_why_and_the_next_good_read_drops_it(
    tmp_path: Path,
) -> None:
    flag = tmp_path / 'broken'
    claude = script(
        tmp_path, f'if [ -e "{flag}" ]; then echo "boom" >&2; exit 1; fi\ncat "{RECORDED}"\n'
    )
    clock = Clock()
    probe = UsageProbe(claude, clock=clock)
    probe.refresh()
    flag.touch()
    clock.now = TAKEN + timedelta(minutes=5)
    probe.refresh()
    _, body = probe.reading()
    assert_contract_shape(body)
    assert body['stale'] is True
    assert body['reason'] == f'`{claude} -p /usage` failed with exit code 1.'
    flag.unlink()
    probe.refresh()
    _, body = probe.reading()
    assert_contract_shape(body)
    assert body['stale'] is False and 'reason' not in body


def test_a_reading_that_is_only_old_has_no_reason(tmp_path: Path) -> None:
    clock = Clock()
    probe = UsageProbe(recorded_claude(tmp_path), clock=clock)
    probe.refresh()
    clock.now = TAKEN + timedelta(minutes=16)
    _, body = probe.reading()
    assert_contract_shape(body)
    assert body['stale'] is True and 'reason' not in body


def test_the_cost_view_tells_the_login_and_the_reason_is_its_message(
    tmp_path: Path, fake_claude: Path
) -> None:
    out = tmp_path / 'out.txt'
    out.write_text((FIXTURES / 'usage-2.1.299-text.txt').read_text('utf-8'))
    login_clock = Clock(TAKEN_299)
    login = LoginCheck(fake_claude, tmp_path / 'state', tmp_path / 'daemon.log', clock=login_clock)
    usage_claude = write_script(tmp_path / 'usage-claude', f'cat "{out}"\n')
    probe = UsageProbe(usage_claude, clock=Clock(TAKEN_299), login=login)
    probe.refresh()
    assert login()['state'] == 'ok'
    out.write_text((FIXTURES / 'usage-2.1.299-logged-out.txt').read_text('utf-8'))
    probe.refresh()
    login_clock.now += timedelta(seconds=LOOK_EVERY)
    answer = login()
    _, body = probe.reading()
    assert_contract_shape(body)
    assert answer['state'] == 'lost'
    assert body['reason'] == answer['message']


def test_a_failure_other_than_the_cost_view_does_not_tell_the_login(
    tmp_path: Path, fake_claude: Path
) -> None:
    login = LoginCheck(fake_claude, tmp_path / 'state', tmp_path / 'daemon.log', clock=Clock())
    probe = UsageProbe(
        write_script(tmp_path / 'usage-claude', 'exit 3\n'), clock=Clock(), login=login
    )
    probe.refresh()
    assert login()['state'] == 'ok'


def test_no_account_detail_reaches_a_usage_answer(tmp_path: Path) -> None:
    """`claude` printing the account on a failure: the reason never repeats what it said."""
    account = 'owner@example.com'
    flag = tmp_path / 'broken'
    claude = script(
        tmp_path,
        f'if [ -e "{flag}" ]; then echo "{account} is not allowed" >&2; exit 1; fi\n'
        f'cat "{RECORDED}"\n',
    )
    probe = UsageProbe(claude, clock=Clock())
    probe.refresh()
    flag.touch()
    probe.refresh()
    _, body = probe.reading()
    assert account not in json.dumps(body)


ACCOUNT = 'owner@example.com'


def test_an_account_in_what_claude_printed_never_reaches_a_503(tmp_path: Path) -> None:
    claude = script(
        tmp_path, f'echo "Signed in as {ACCOUNT}" >&2; echo "not allowed" >&2; exit 1\n'
    )
    probe = UsageProbe(claude, clock=Clock())
    probe.refresh()
    code, body = probe.reading()
    assert code == 503
    assert ACCOUNT not in json.dumps(body)


def test_what_claude_printed_goes_to_the_journal_at_debug_priority_only(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    claude = script(
        tmp_path, f'echo "Signed in as {ACCOUNT}" >&2; echo "not allowed" >&2; exit 1\n'
    )
    UsageProbe(claude, clock=Clock()).refresh()
    lines = capsys.readouterr().err.splitlines()
    assert (
        lines[0]
        == f'marcel-runner: could not read usage. `{claude} -p /usage` failed with exit code 1.'
    )
    assert lines[1:] == [
        f'<7>marcel-runner: /usage said: Signed in as {ACCOUNT}',
        '<7>marcel-runner: /usage said: not allowed',
    ]
    assert [line for line in lines if ACCOUNT in line and not line.startswith('<7>')] == []


def test_the_same_failure_with_new_output_logs_the_new_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    said = tmp_path / 'said.txt'
    said.write_text('first\n')
    probe = UsageProbe(script(tmp_path, f'cat "{said}" >&2; exit 1\n'), clock=Clock())
    probe.refresh()
    probe.refresh()
    said.write_text('second\n')
    probe.refresh()
    err = capsys.readouterr().err
    assert err.count('/usage said: first') == 1 and err.count('/usage said: second') == 1
