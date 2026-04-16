"""
Seed initial jobs for a user.

Usage:
    uv run python scripts/seed_jobs.py [user_slug]

Defaults to 'shaun' if no user slug is provided.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta

from marcel_core.jobs import list_jobs, save_job
from marcel_core.jobs.models import (
    JobDefinition,
    NotifyPolicy,
    TriggerSpec,
    TriggerType,
)


def _has_job(user_slug: str, name: str) -> str | None:
    """Return job ID if a job with this name already exists, else None."""
    for j in list_jobs(user_slug):
        if j.name == name:
            return j.id
    return None


def seed(user_slug: str) -> None:
    created: list[str] = []

    # ── 1. Bank sync (every 8h) ──────────────────────────────────────────
    bank_sync_id = _has_job(user_slug, 'Bank sync')
    if not bank_sync_id:
        job = JobDefinition(
            name='Bank sync',
            description='Sync bank transactions and balances every 8 hours',
            users=[user_slug],
            trigger=TriggerSpec(type=TriggerType.INTERVAL, interval_seconds=8 * 60 * 60),
            system_prompt=(
                'You are a background sync worker for Marcel. '
                'Call banking.sync to sync all linked bank accounts. '
                'Report a brief summary of what was synced (number of transactions, any warnings). '
                'If there are consent expiry warnings, include them prominently.'
            ),
            task='Run banking.sync to sync all linked bank accounts, then report results.',
            model='claude-haiku-4-5-20251001',
            skills=['banking.sync'],
            notify=NotifyPolicy.ON_FAILURE,
            channel='telegram',
            template='sync',
        )
        save_job(job)
        bank_sync_id = job.id
        created.append(f'  Bank sync: {job.id}')
    else:
        print(f'  Bank sync already exists: {bank_sync_id}')

    # ── 2. News sync (VRT NWS + deTijd, every 8h) ────────────────────────
    if not _has_job(user_slug, 'News sync'):
        job = JobDefinition(
            name='News sync',
            description='Scrape VRT NWS and De Tijd for latest articles every 8 hours',
            users=[user_slug],
            trigger=TriggerSpec(type=TriggerType.INTERVAL, interval_seconds=8 * 60 * 60),
            system_prompt=(
                'You are a news scraper for Marcel. Your job is to fetch the latest headlines '
                'and article summaries from Belgian news sources.\n\n'
                'Steps:\n'
                '1. Navigate to https://www.vrt.be/vrtnws/nl/ and extract the top 10 headlines '
                '   with their URLs and a one-line summary each.\n'
                '2. Navigate to https://www.tijd.be and extract the top 10 headlines '
                '   with their URLs and a one-line summary each.\n'
                '3. Return a structured JSON summary with both sources.\n\n'
                'Use browser_navigate and browser_snapshot to read the pages. '
                'Do NOT use browser_screenshot unless the snapshot is insufficient. '
                'Close the browser when done with browser_close.\n\n'
                'If De Tijd requires login, skip it and note that in the output.'
            ),
            task=(
                'Scrape the latest news from VRT NWS (https://www.vrt.be/vrtnws/nl/) '
                'and De Tijd (https://www.tijd.be). Return headlines with URLs and summaries.'
            ),
            model='claude-haiku-4-5-20251001',
            skills=['browser'],
            notify=NotifyPolicy.SILENT,
            channel='telegram',
            template='scrape',
        )
        save_job(job)
        created.append(f'  News sync: {job.id}')
    else:
        print('  News sync already exists')

    # ── 3. Morning digest (news + calendar, daily at 7:00) ───────────────
    if not _has_job(user_slug, 'Good morning'):
        job = JobDefinition(
            name='Good morning',
            description='Morning digest with calendar events and news highlights',
            users=[user_slug],
            trigger=TriggerSpec(type=TriggerType.CRON, cron='0 7 * * *'),
            system_prompt=(
                "You are Marcel's morning digest composer. Compose a warm, concise "
                '"good morning" message for the user.\n\n'
                'Steps:\n'
                "1. Call icloud.calendar with days_ahead=1 to get today's events.\n"
                '2. Navigate to https://www.vrt.be/vrtnws/nl/ and read the top 5 headlines.\n'
                '3. Navigate to https://www.tijd.be and read the top 3 financial headlines.\n'
                '4. Compose a single message with:\n'
                '   - A friendly greeting\n'
                "   - Today's calendar events (times, titles, locations)\n"
                '   - Top news highlights (3-5 most interesting items with links)\n'
                '   - A brief financial/markets note if relevant\n\n'
                'Keep it scannable — bullet points, not paragraphs. '
                'Use the notify tool to send the final message to the user. '
                'Close the browser when done.'
            ),
            task=("Compose the morning digest: today's calendar + top news from VRT NWS and De Tijd. Send via notify."),
            model='claude-sonnet-4-6',
            skills=['icloud.calendar', 'browser'],
            notify=NotifyPolicy.ALWAYS,
            channel='telegram',
            template='digest',
        )
        save_job(job)
        created.append(f'  Good morning: {job.id}')
    else:
        print('  Good morning already exists')

    # ── 4. Test job (runs in 10 minutes) ─────────────────────────────────
    if not _has_job(user_slug, 'Test signal'):
        run_at = datetime.now(UTC) + timedelta(minutes=10)
        job = JobDefinition(
            name='Test signal',
            description='One-time test job to verify the scheduler sends a Telegram notification',
            users=[user_slug],
            trigger=TriggerSpec(type=TriggerType.ONESHOT, run_at=run_at),
            system_prompt=(
                'You are a test worker for Marcel. Your only job is to produce a short '
                'confirmation message proving that the job system works.'
            ),
            task=(
                'This is a test job. Send a notification to the user saying: '
                '"Marcel job system is working! This message was sent automatically '
                'by a scheduled test job." Include the current date and time.'
            ),
            model='claude-haiku-4-5-20251001',
            skills=[],
            notify=NotifyPolicy.ALWAYS,
            channel='telegram',
            template=None,
        )
        save_job(job)
        created.append(f'  Test signal: {job.id} (runs at {run_at.strftime("%H:%M UTC")})')
    else:
        print('  Test signal already exists')

    if created:
        print(f'Created {len(created)} jobs for user "{user_slug}":')
        for line in created:
            print(line)
    else:
        print('All jobs already exist.')


if __name__ == '__main__':
    slug = sys.argv[1] if len(sys.argv) > 1 else 'shaun'
    seed(slug)
