"""S-02.12: the runner's /health says whether Claude Code is still logged in, and a stale usage
reading says why (runner-api 0.1.13; the second asked for by S-03.7).

Found live on the NUC: after a Claude Code self-update the supervisor could not refresh its login
for about two hours; no session started, /usage failed, and nothing told the owner."""

from __future__ import annotations

from typing import Any

import pytest
from conftest import BASE, load_yaml
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry

LOST_MESSAGE = (
    'Claude Code on the NUC is logged out. Log in again: on the NUC run `claude auth login`.'
)
HEALTH: dict[str, Any] = {
    'ok': True,
    'version': '0.1.0',
    'claude_version': '2.1.296 (Claude Code)',
    'claude_version_expected': '2.1.296',
    'supervisor': {'running': True, 'own_unit': True},
    'cap': 4,
    'active_workers': 0,
}


def flat(text: str) -> str:
    return ' '.join(text.split())


def health(registry: Registry) -> Draft202012Validator:
    return Draft202012Validator(
        {'$ref': f'{BASE}runner-api.yaml#/components/schemas/Health'},
        registry=registry,
        format_checker=FormatChecker(),
    )


def login_schema() -> dict[str, Any]:
    return load_yaml('runner-api.yaml')['components']['schemas']['Health']['properties']['login']


def test_the_version_was_bumped() -> None:
    assert load_yaml('runner-api.yaml')['info']['version'] == '0.1.14'  # 0.1.14 in S-02.13


def test_login_is_optional_so_an_older_runner_still_fits(registry: Registry) -> None:
    required = load_yaml('runner-api.yaml')['components']['schemas']['Health']['required']
    assert 'login' not in required
    health(registry).validate(HEALTH)
    assert 'read that as `unknown`' in flat(login_schema()['description'])


@pytest.mark.parametrize(
    'login',
    [
        {'state': 'ok'},
        {'state': 'ok', 'since': '2026-10-14T06:12:00Z'},
        {'state': 'lost', 'since': '2026-10-14T02:51:00Z', 'message': LOST_MESSAGE},
        {
            'state': 'unknown',
            'message': 'The runner could not ask Claude Code whether it is logged in.',
        },
    ],
)
def test_each_login_state_is_accepted(login: dict[str, Any], registry: Registry) -> None:
    health(registry).validate({**HEALTH, 'login': login})


@pytest.mark.parametrize(
    'login',
    [
        {},
        {'state': 'expired', 'message': LOST_MESSAGE},
        {'state': 'lost', 'message': LOST_MESSAGE},  # no since: the hub could tell it twice
        {'state': 'lost', 'since': '2026-10-14T02:51:00Z'},  # no words for the owner
        {'state': 'unknown'},
        {'state': 'lost', 'since': 'last night', 'message': LOST_MESSAGE},
        {'state': 'ok', 'email': 'owner@example.com'},
    ],
)
def test_a_bad_login_is_refused(login: dict[str, Any], registry: Registry) -> None:
    assert not health(registry).is_valid({**HEALTH, 'login': login})


def test_a_lost_login_leaves_ok_to_the_runner(registry: Registry) -> None:
    # A redeploy check reads `ok`; a rollback cannot log anyone in.
    lost = {'state': 'lost', 'since': '2026-10-14T02:51:00Z', 'message': LOST_MESSAGE}
    health(registry).validate({**HEALTH, 'ok': True, 'login': lost})
    words = flat(login_schema()['description'])
    assert '`lost` does not set `ok` to false on its own' in words
    assert '`claude auth status`' in words and '`~/.claude/daemon.log`' in words


def test_the_hub_tells_a_loss_once_per_since() -> None:
    since = flat(login_schema()['properties']['since']['description'])
    assert 'once per `since`' in since


# A stale usage reading says why (S-03.7) -------------------------------------------------------

READING: dict[str, Any] = {
    'five_hour': {'used_pct': 4, 'resets_at': '2026-10-14T05:00:00Z'},
    'weekly': {'used_pct': 66, 'resets_at': '2026-10-16T01:59:00Z'},
    'source': 'cli_usage',
    'taken_at': '2026-10-14T02:47:00Z',
    'stale': False,
}


def usage(registry: Registry) -> Draft202012Validator:
    return Draft202012Validator(
        {'$ref': f'{BASE}runner-api.yaml#/components/schemas/UsageReading'},
        registry=registry,
        format_checker=FormatChecker(),
    )


def test_a_stale_reading_may_say_why(registry: Registry) -> None:
    schema = load_yaml('runner-api.yaml')['components']['schemas']['UsageReading']
    assert 'reason' not in schema['required']
    usage(registry).validate(READING)
    usage(registry).validate({**READING, 'stale': True})  # an older runner, or simply too old
    usage(registry).validate({**READING, 'stale': True, 'reason': LOST_MESSAGE})


def test_a_fresh_reading_has_no_reason(registry: Registry) -> None:
    assert not usage(registry).is_valid({**READING, 'reason': LOST_MESSAGE})
    assert not usage(registry).is_valid({**READING, 'stale': True, 'reason': 3})


def test_a_lost_login_gives_the_same_words_in_both_places() -> None:
    schemas = load_yaml('runner-api.yaml')['components']['schemas']
    reason = flat(schemas['UsageReading']['properties']['reason']['description'])
    assert 'the same words as `Health.login.message`' in reason
    assert 'only with `stale` true' in reason
    assert '`UsageReading.reason`' in flat(login_schema()['description'])
