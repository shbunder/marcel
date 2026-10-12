"""Five-field cron (`minute hour day month weekday`), read as wall-clock time in a time zone.

`Cron.next_after(when)` gives the first fire strictly after `when`, in UTC. Each wall-clock time
that matches fires **once**, whatever the clocks do:

- **Spring forward.** A time that does not exist that night (02:30 when 02:00 jumps to 03:00)
  fires once, at the moment the clocks jump past it (03:30 local, the old offset's 02:30).
- **Fall back.** A time that happens twice (02:30 when 03:00 goes back to 02:00) fires at its
  first occurrence only.

Fields take `*`, numbers, ranges `a-b`, steps `*/n` and `a-b/n`, lists `a,b`, and three-letter
names (`mon`, `jan`). Weekday 0 and 7 are both Sunday. When both day and weekday are restricted, a
day matching either one fires (as in classic cron).
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

MONTHS = ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec']
DAYS = ['sun', 'mon', 'tue', 'wed', 'thu', 'fri', 'sat']
# (name, lowest, highest, names counted from `lowest`)
FIELDS = (
    ('minute', 0, 59, []),
    ('hour', 0, 23, []),
    ('day', 1, 31, []),
    ('month', 1, 12, MONTHS),
    ('weekday', 0, 7, DAYS),
)
SEARCH_DAYS = 8 * 366  # far enough for 29 February on a Monday


class CronError(ValueError):
    """A cron or time zone that cannot be used. The message says what to change."""


def zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise CronError(f'tz "{name}" is not a time zone. Use one like Europe/Brussels.') from exc


def _number(text: str, low: int, names: list[str]) -> int:
    lowered = text.lower()
    if lowered in names:
        return names.index(lowered) + low
    if not text.isdigit():
        raise ValueError(text)
    return int(text)


def _field(text: str, name: str, low: int, high: int, names: list[str]) -> frozenset[int]:
    values: set[int] = set()
    for part in text.split(','):
        span, _, step_text = part.partition('/')
        try:
            step = int(step_text) if step_text else 1
            if span == '*':
                first, last = low, high
            elif '-' in span:
                a, b = span.split('-', 1)
                first, last = _number(a, low, names), _number(b, low, names)
            else:
                first = _number(span, low, names)
                last = high if step_text else first
        except ValueError:
            raise CronError(f'The {name} field "{text}" is not a number, range or list.') from None
        if step < 1 or not low <= first <= last <= high:
            raise CronError(f'The {name} field "{text}" must stay within {low}-{high}.')
        values.update(range(first, last + 1, step))
    return frozenset(values)


@dataclass(frozen=True)
class Cron:
    expression: str
    tz: ZoneInfo
    minutes: frozenset[int]
    hours: frozenset[int]
    days: frozenset[int]
    months: frozenset[int]
    weekdays: frozenset[int]  # 0 is Sunday
    any_day: bool
    any_weekday: bool

    @classmethod
    def parse(cls, expression: str, tz: str) -> 'Cron':
        """Raises `CronError` with a plain message for a cron or time zone that cannot be used."""
        parts = expression.split()
        if len(parts) != 5:
            raise CronError(
                f'cron "{expression}" needs five fields: minute hour day month weekday, '
                'e.g. "0 8 * * 1" for Mondays at 08:00.'
            )
        sets = [_field(p, *spec) for p, spec in zip(parts, FIELDS, strict=True)]
        cron = cls(
            expression=expression,
            tz=zone(tz),
            minutes=sets[0],
            hours=sets[1],
            days=sets[2],
            months=sets[3],
            weekdays=frozenset(d % 7 for d in sets[4]),
            any_day=parts[2].startswith('*'),
            any_weekday=parts[4].startswith('*'),
        )
        if cron.next_after(datetime(2000, 1, 1, tzinfo=UTC)) is None:
            raise CronError(f'cron "{expression}" never comes round (no such day).')
        return cron

    def _day_matches(self, day: date) -> bool:
        if day.month not in self.months:
            return False
        in_days = day.day in self.days
        in_weekdays = (day.isoweekday() % 7) in self.weekdays
        if self.any_day and self.any_weekday:
            return True
        if self.any_day:
            return in_weekdays
        if self.any_weekday:
            return in_days
        return in_days or in_weekdays

    def next_after(self, when: datetime) -> datetime | None:
        """The first fire strictly after `when` (aware), in UTC; None if there is none."""
        after = when.astimezone(UTC)
        start = after.astimezone(self.tz).date() - timedelta(days=1)  # a jump can move a day
        times = sorted(time(h, m) for h in self.hours for m in self.minutes)
        for offset in range(SEARCH_DAYS):
            day = start + timedelta(days=offset)
            if not self._day_matches(day):
                continue
            for wall in times:
                # fold=0: a doubled time is its first occurrence; a skipped one, the old offset.
                fire = datetime.combine(day, wall, self.tz).astimezone(UTC)
                if fire > after:
                    return fire
        return None
