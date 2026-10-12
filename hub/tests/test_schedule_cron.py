"""Five-field cron in a time zone: each wall-clock time fires once, across both clock changes."""

from datetime import UTC, datetime

import pytest

from marcel_hub.schedule.cron import Cron, CronError

BRUSSELS = 'Europe/Brussels'


def utc(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


def fires(cron: Cron, after: str, n: int) -> list[str]:
    out, at = [], utc(after)
    for _ in range(n):
        nxt = cron.next_after(at)
        assert nxt is not None
        out.append(nxt.strftime('%Y-%m-%d %H:%M'))
        at = nxt
    return out


def test_mondays_at_eight_in_brussels_are_stored_in_utc() -> None:
    cron = Cron.parse('0 8 * * 1', BRUSSELS)
    # 12 October 2026 is a Monday; Brussels is UTC+2 until 25 October.
    assert fires(cron, '2026-10-12 05:00', 3) == [
        '2026-10-12 06:00', '2026-10-19 06:00', '2026-10-26 07:00',
    ]  # fmt: skip


def test_a_daily_time_follows_the_wall_clock_across_fall_back() -> None:
    cron = Cron.parse('0 7 * * *', BRUSSELS)
    assert fires(cron, '2026-10-24 06:00', 3) == [
        '2026-10-25 06:00', '2026-10-26 06:00', '2026-10-27 06:00',
    ]  # fmt: skip


def test_a_doubled_time_fires_once_when_the_clocks_go_back() -> None:
    # 25 October 2026: 03:00 CEST becomes 02:00 CET, so 02:30 happens twice.
    cron = Cron.parse('30 2 * * *', BRUSSELS)
    assert fires(cron, '2026-10-24 12:00', 3) == [
        '2026-10-25 00:30', '2026-10-26 01:30', '2026-10-27 01:30',
    ]  # fmt: skip


def test_a_skipped_time_fires_once_when_the_clocks_go_forward() -> None:
    # 29 March 2026: 02:00 CET becomes 03:00 CEST, so 02:30 never shows on the clock.
    cron = Cron.parse('30 2 * * *', BRUSSELS)
    assert fires(cron, '2026-03-28 12:00', 3) == [
        '2026-03-29 01:30', '2026-03-30 00:30', '2026-03-31 00:30',
    ]  # fmt: skip


def test_every_half_hour_through_the_spring_gap_never_repeats_an_instant() -> None:
    cron = Cron.parse('*/30 * * * *', BRUSSELS)
    got = fires(cron, '2026-03-29 00:00', 5)
    assert got == sorted(set(got))
    assert got[:3] == ['2026-03-29 00:30', '2026-03-29 01:00', '2026-03-29 01:30']


@pytest.mark.parametrize(
    ('expression', 'after', 'expected'),
    [
        ('*/15 9-10 * * mon-fri', '2026-10-17 00:00', '2026-10-19 07:00'),  # Saturday → Monday
        ('0 12 1,15 * *', '2026-10-02 00:00', '2026-10-15 10:00'),
        ('0 0 * jan *', '2026-10-02 00:00', '2026-12-31 23:00'),
        ('0 9 13 * 5', '2026-10-01 00:00', '2026-10-02 07:00'),  # day 13 or a Friday
        ('0 9 * * 7', '2026-10-12 00:00', '2026-10-18 07:00'),  # 7 is Sunday too
        ('5-10/5 * * * *', '2026-10-12 00:06', '2026-10-12 00:10'),
    ],
)
def test_fields(expression: str, after: str, expected: str) -> None:
    nxt = Cron.parse(expression, BRUSSELS).next_after(utc(after))
    assert nxt is not None and nxt.strftime('%Y-%m-%d %H:%M') == expected


@pytest.mark.parametrize(
    ('expression', 'tz', 'says'),
    [
        ('0 8 * *', BRUSSELS, 'five fields'),
        ('61 8 * * 1', BRUSSELS, 'minute field'),
        ('0 8 * * funday', BRUSSELS, 'weekday field'),
        ('0 8-6 * * *', BRUSSELS, 'hour field'),
        ('*/0 8 * * *', BRUSSELS, 'minute field'),
        ('0 0 31 2 *', BRUSSELS, 'never comes round'),
        ('0 8 * * 1', 'Mars/Olympus', 'Europe/Brussels'),
    ],
)
def test_bad_crons_say_what_to_change(expression: str, tz: str, says: str) -> None:
    with pytest.raises(CronError, match=says):
        Cron.parse(expression, tz)
