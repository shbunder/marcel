"""Is Claude Code still logged in? `Health.login` from `claude auth status`, the supervisor's
`daemon.log` and a `/usage` that shows the cost view (runner-api 0.1.13, S-03.13)."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from conftest import (
    AUTH_LOGGED_IN,
    AUTH_LOGGED_OUT,
    LOGIN_FIXTURES,
    auth_answer,
    runner_api,
    write_script,
)

from marcel_runner.login import LOOK_EVERY, LOST_MESSAGE, UNKNOWN_MESSAGE, LoginCheck

LOGIN = runner_api('Health/properties/login')
T0 = datetime(2026, 10, 14, 2, 52, 30, tzinfo=UTC)

# ~/.claude/daemon.log on 2026-10-14 (fixtures/login/README.md): an update restarts the supervisor
# at 02:51, its login refresh fails at 02:52 and it finds a token again at 05:02.
OUTAGE = (LOGIN_FIXTURES / 'daemon-2026-10-14-0251.log').read_text('utf-8').splitlines(True)


def _at(text: str) -> int:
    return next(i for i, line in enumerate(OUTAGE) if text in line)


BEFORE = ''.join(OUTAGE[: _at('02:52:06')])  # the restarts; the token is fine
LOSS = ''.join(OUTAGE[_at('02:52:06') : _at('05:02:10')])  # refresh fails, no token
BACK = ''.join(OUTAGE[_at('05:02:10') :])  # token found via keychain re-check, and after
# 12:58–13:02: four failed refreshes with the token still valid, then the same loss at 13:02.
STILL_VALID = ''.join(
    line
    for line in (LOGIN_FIXTURES / 'daemon-2026-10-14-1258.log').read_text('utf-8').splitlines(True)
    if line < '[2026-10-14T13:02:36'
)


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now


def iso(t: datetime) -> str:
    return t.strftime('%Y-%m-%dT%H:%M:%SZ')


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def log(tmp_path: Path) -> Path:
    path = tmp_path / 'daemon.log'
    path.write_text(BEFORE)
    return path


def new_check(claude: Path, tmp_path: Path, log: Path, clock: Clock) -> LoginCheck:
    return LoginCheck(claude, tmp_path / 'state', log, clock=clock)


def look(check: LoginCheck, clock: Clock) -> dict:
    """The next fresh look, checked against runner-api.yaml `Health.login`."""
    clock.now += timedelta(seconds=LOOK_EVERY)
    answer = check()
    LOGIN.validate(answer)
    return answer


def append(log: Path, text: str) -> None:
    with log.open('a') as f:
        f.write(text)


# --- claude auth status ---------------------------------------------------------------------------


def test_logged_in_is_ok_with_since(fake_claude: Path, tmp_path: Path, log: Path, clock) -> None:
    check = new_check(fake_claude, tmp_path, log, clock)
    assert look(check, clock) == {'state': 'ok', 'since': iso(clock.now)}


def test_logged_out_is_lost_with_since_and_what_to_do(
    fake_claude: Path, tmp_path: Path, log: Path, clock: Clock
) -> None:
    auth_answer(fake_claude, AUTH_LOGGED_OUT, code=1)  # as recorded: exit 1, the JSON on stdout
    check = new_check(fake_claude, tmp_path, log, clock)
    answer = look(check, clock)
    assert answer == {'state': 'lost', 'since': iso(clock.now), 'message': LOST_MESSAGE}
    assert 'claude auth login' in LOST_MESSAGE  # the command daemon.log names, which worked


@pytest.mark.parametrize(
    ('stdout', 'code'),
    [
        ('', 2),  # it failed and said nothing
        ('Error: something went wrong\n', 1),  # not that JSON
        ('{"authMethod": "claude.ai"}', 0),  # JSON without loggedIn
        ('[true]', 0),  # JSON, not an object
    ],
)
def test_auth_status_that_cannot_be_read_is_unknown(
    fake_claude: Path, tmp_path: Path, log: Path, clock: Clock, stdout: str, code: int
) -> None:
    auth_answer(fake_claude, stdout, code)
    answer = look(new_check(fake_claude, tmp_path, log, clock), clock)
    assert answer == {'state': 'unknown', 'since': iso(clock.now), 'message': UNKNOWN_MESSAGE}


def test_claude_missing_or_hanging_is_unknown(tmp_path: Path, log: Path, clock: Clock) -> None:
    missing = new_check(tmp_path / 'nope' / 'claude', tmp_path, log, clock)
    assert look(missing, clock)['state'] == 'unknown'
    slow = write_script(tmp_path / 'slow', 'sleep 5\n')
    hanging = LoginCheck(slow, tmp_path / 'state2', log, clock=clock, timeout=0.2)
    assert look(hanging, clock)['state'] == 'unknown'


def test_one_look_per_30_seconds(fake_claude: Path, tmp_path: Path, log: Path, clock) -> None:
    calls = tmp_path / 'auth-status.calls'
    check = new_check(fake_claude, tmp_path, log, clock)
    first = look(check, clock)
    clock.now += timedelta(seconds=LOOK_EVERY - 1)
    auth_answer(fake_claude, AUTH_LOGGED_OUT, code=1)
    assert check() == first  # a /health between two looks gets the last answer
    assert len(calls.read_text().splitlines()) == 1
    clock.now += timedelta(seconds=1)
    assert check()['state'] == 'lost'
    assert len(calls.read_text().splitlines()) == 2


# --- the supervisor's daemon.log ------------------------------------------------------------------


def test_a_new_failure_line_is_lost_while_auth_status_says_logged_in(
    fake_claude: Path, tmp_path: Path, log: Path, clock: Clock
) -> None:
    check = new_check(fake_claude, tmp_path, log, clock)
    assert look(check, clock)['state'] == 'ok'
    append(log, LOSS)
    answer = look(check, clock)
    assert answer == {'state': 'lost', 'since': iso(clock.now), 'message': LOST_MESSAGE}


def test_quiet_looks_alone_never_end_a_loss(
    fake_claude: Path, tmp_path: Path, log: Path, clock: Clock
) -> None:
    """2026-10-14: one failure line at 02:52, then silence until 05:02 while the supervisor had no
    token. Looks that find no new line are not evidence the login is back."""
    check = new_check(fake_claude, tmp_path, log, clock)
    look(check, clock)
    append(log, LOSS)
    since = look(check, clock)['since']
    for _ in range(10):
        assert look(check, clock) == {'state': 'lost', 'since': since, 'message': LOST_MESSAGE}


def test_after_the_recovery_line_one_clean_look_is_still_lost_two_are_ok(
    fake_claude: Path, tmp_path: Path, log: Path, clock: Clock
) -> None:
    check = new_check(fake_claude, tmp_path, log, clock)
    look(check, clock)
    append(log, LOSS)
    since = look(check, clock)['since']
    append(log, BACK)  # 05:02 "token found via keychain re-check"
    assert look(check, clock) == {'state': 'lost', 'since': since, 'message': LOST_MESSAGE}
    assert look(check, clock) == {'state': 'ok', 'since': iso(clock.now)}


def test_a_new_failure_after_one_clean_look_starts_the_count_again(
    fake_claude: Path, tmp_path: Path, log: Path, clock: Clock
) -> None:
    check = new_check(fake_claude, tmp_path, log, clock)
    look(check, clock)
    append(log, LOSS)
    since = look(check, clock)['since']
    append(log, BACK)
    look(check, clock)  # clean 1
    append(log, LOSS)
    assert look(check, clock)['state'] == 'lost'
    append(log, BACK)
    assert look(check, clock) == {'state': 'lost', 'since': since, 'message': LOST_MESSAGE}
    assert look(check, clock)['state'] == 'ok'


def test_a_failed_refresh_with_the_token_still_valid_is_not_a_loss(
    fake_claude: Path, tmp_path: Path, log: Path, clock: Clock
) -> None:
    check = new_check(fake_claude, tmp_path, log, clock)
    look(check, clock)
    append(log, STILL_VALID)  # 12:58–13:01: "retrying in ~60s (token still valid)"
    assert look(check, clock)['state'] == 'ok'


def test_a_fresh_runner_reads_the_supervisors_current_state_from_the_log_tail(
    fake_claude: Path, tmp_path: Path, log: Path, clock: Clock
) -> None:
    append(log, LOSS)  # the loss was logged before this runner started
    assert look(new_check(fake_claude, tmp_path / 'a', log, clock), clock)['state'] == 'lost'
    append(log, BACK)
    assert look(new_check(fake_claude, tmp_path / 'b', log, clock), clock)['state'] == 'ok'


def test_a_failure_line_split_across_two_looks_is_still_seen(
    fake_claude: Path, tmp_path: Path, log: Path, clock: Clock
) -> None:
    check = new_check(fake_claude, tmp_path, log, clock)
    look(check, clock)
    half = len(LOSS) - 20
    append(log, LOSS[:half])  # the supervisor is mid-write when the runner looks
    look(check, clock)
    append(log, LOSS[half:])
    assert look(check, clock)['state'] == 'lost'


def test_a_rotated_log_is_read_from_its_start(
    fake_claude: Path, tmp_path: Path, log: Path, clock: Clock
) -> None:
    check = new_check(fake_claude, tmp_path, log, clock)
    look(check, clock)
    log.write_text(LOSS)  # shorter than before: a new file
    assert look(check, clock)['state'] == 'lost'


def test_no_daemon_log_is_judged_on_auth_status_alone(
    fake_claude: Path, tmp_path: Path, clock: Clock
) -> None:
    check = new_check(fake_claude, tmp_path, tmp_path / 'missing.log', clock)
    assert look(check, clock)['state'] == 'ok'


def test_auth_status_failing_during_a_loss_keeps_the_loss(
    fake_claude: Path, tmp_path: Path, log: Path, clock: Clock
) -> None:
    auth_answer(fake_claude, AUTH_LOGGED_OUT, code=1)
    check = new_check(fake_claude, tmp_path, log, clock)
    since = look(check, clock)['since']
    auth_answer(fake_claude, '', code=2)
    assert look(check, clock) == {'state': 'lost', 'since': since, 'message': LOST_MESSAGE}
    append(log, LOSS)  # a new failure line beats a check that cannot run
    assert look(check, clock)['state'] == 'lost'


def test_logged_in_again_needs_two_clean_looks(
    fake_claude: Path, tmp_path: Path, log: Path, clock: Clock
) -> None:
    auth_answer(fake_claude, AUTH_LOGGED_OUT, code=1)
    check = new_check(fake_claude, tmp_path, log, clock)
    look(check, clock)
    auth_answer(fake_claude, AUTH_LOGGED_IN)
    assert look(check, clock)['state'] == 'lost'
    assert look(check, clock)['state'] == 'ok'


# --- since survives a runner restart --------------------------------------------------------------


def test_a_restarted_runner_keeps_the_loss_since(
    fake_claude: Path, tmp_path: Path, log: Path, clock: Clock
) -> None:
    auth_answer(fake_claude, AUTH_LOGGED_OUT, code=1)
    since = look(new_check(fake_claude, tmp_path, log, clock), clock)['since']
    clock.now += timedelta(minutes=10)
    again = new_check(fake_claude, tmp_path, log, clock)  # the same state folder
    assert look(again, clock) == {'state': 'lost', 'since': since, 'message': LOST_MESSAGE}


def test_a_restarted_runner_does_not_end_a_loss_on_its_first_look(
    fake_claude: Path, tmp_path: Path, log: Path, clock: Clock
) -> None:
    auth_answer(fake_claude, AUTH_LOGGED_OUT, code=1)
    since = look(new_check(fake_claude, tmp_path, log, clock), clock)['since']
    auth_answer(fake_claude, AUTH_LOGGED_IN)
    again = new_check(fake_claude, tmp_path, log, clock)
    assert look(again, clock)['since'] == since
    assert look(again, clock)['state'] == 'ok'


@pytest.mark.parametrize('stored', ['not json', '[]', '{"state": "gone", "since": 3}'])
def test_an_unreadable_login_json_starts_afresh(
    fake_claude: Path, tmp_path: Path, log: Path, clock: Clock, stored: str
) -> None:
    (tmp_path / 'state').mkdir()
    (tmp_path / 'state' / 'login.json').write_text(stored)
    check = new_check(fake_claude, tmp_path, log, clock)
    assert look(check, clock) == {'state': 'ok', 'since': iso(clock.now)}
    assert json.loads((tmp_path / 'state' / 'login.json').read_text())['state'] == 'ok'


def test_a_state_folder_it_cannot_write_never_stops_the_check(
    fake_claude: Path, tmp_path: Path, log: Path, clock: Clock
) -> None:
    blocked = tmp_path / 'file'
    blocked.write_text('')  # a file where the state folder should be
    check = LoginCheck(fake_claude, blocked, log, clock=clock)
    assert look(check, clock)['state'] == 'ok'


# --- /usage showing the cost view -----------------------------------------------------------------


def test_usage_in_the_cost_view_is_a_loss_until_a_good_usage_read(
    fake_claude: Path, tmp_path: Path, log: Path, clock: Clock
) -> None:
    kicked: list[int] = []
    check = new_check(fake_claude, tmp_path, log, clock)
    check.refresh_usage = lambda: kicked.append(1)
    look(check, clock)
    check.usage_says(lost=True)
    since = look(check, clock)['since']
    for _ in range(3):  # auth status says logged in, but /usage is still on the cost view
        assert look(check, clock) == {'state': 'lost', 'since': since, 'message': LOST_MESSAGE}
    assert kicked, 'logged in again by auth status: the usage read is asked for at once'
    check.usage_says(lost=False)  # a good /usage reading
    assert look(check, clock)['state'] == 'lost'
    assert look(check, clock)['state'] == 'ok'


def test_no_usage_read_is_asked_for_while_auth_status_says_logged_out(
    fake_claude: Path, tmp_path: Path, log: Path, clock: Clock
) -> None:
    kicked: list[int] = []
    auth_answer(fake_claude, AUTH_LOGGED_OUT, code=1)
    check = new_check(fake_claude, tmp_path, log, clock)
    check.refresh_usage = lambda: kicked.append(1)
    check.usage_says(lost=True)
    look(check, clock)
    look(check, clock)
    assert kicked == []


# --- what the owner may read ----------------------------------------------------------------------


def test_no_account_detail_reaches_an_answer(
    fake_claude: Path, tmp_path: Path, log: Path, clock: Clock
) -> None:
    secrets = [
        v for k, v in json.loads(AUTH_LOGGED_IN).items() if k in {'email', 'orgId', 'orgName'}
    ]
    assert len(secrets) == 3
    check = new_check(fake_claude, tmp_path, log, clock)
    answers = [look(check, clock)]
    auth_answer(fake_claude, AUTH_LOGGED_OUT, code=1)
    answers.append(look(check, clock))
    auth_answer(fake_claude, AUTH_LOGGED_IN.replace('true', 'maybe'), code=0)
    answers.append(look(check, clock))
    text = json.dumps(answers) + (tmp_path / 'state' / 'login.json').read_text()
    for secret in secrets:
        assert secret not in text


# --- wired in production --------------------------------------------------------------------------


def test_main_gives_health_a_login_check_on_the_state_folder_and_daemon_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marcel_runner import server

    seen: dict = {}

    class Srv:
        class usage:  # noqa: N801 - stands in for the UsageProbe attribute
            @staticmethod
            def start() -> None:
                pass

        def serve_forever(self) -> None:
            raise KeyboardInterrupt

        def server_close(self) -> None:
            pass

    def serve(*args, **kwargs) -> Srv:
        seen.update(kwargs)
        return Srv()

    monkeypatch.setenv('MARCEL_RUNNER_SOCK', str(tmp_path / 'runner.sock'))
    monkeypatch.setenv('MARCEL_CLAUDE', str(tmp_path / 'claude'))
    monkeypatch.setenv('MARCEL_RUNNER_STATE', str(tmp_path / 'state'))
    monkeypatch.setattr(server, 'serve', serve)
    assert server.main() == 0
    login = seen['login']
    assert isinstance(login, LoginCheck)
    assert login.state_file == tmp_path / 'state' / 'login.json'
    assert login.daemon_log == Path.home() / '.claude' / 'daemon.log'
