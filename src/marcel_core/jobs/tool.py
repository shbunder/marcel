"""Agent tools for conversational job management.

These tools are registered on the Marcel agent so users can create, list,
update, and manage background jobs through natural conversation.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from pydantic_ai import RunContext

from marcel_core.harness.context import MarcelDeps
from marcel_core.jobs.models import (
    JobDefinition,
    JobStatus,
    NotifyPolicy,
    TriggerSpec,
    TriggerType,
)

log = logging.getLogger(__name__)


async def create_job(
    ctx: RunContext[MarcelDeps],
    name: str,
    task: str,
    trigger_type: str,
    system_prompt: str,
    *,
    template: str | None = None,
    cron: str | None = None,
    interval_hours: float | None = None,
    after_job: str | None = None,
    timezone: str | None = None,
    notify: str = 'on_output',
    model: str | None = None,
    channel: str | None = None,
    skills: list[str] | None = None,
    timeout_minutes: float | None = None,
    users: list[str] | None = None,
) -> str:
    """Create a new background job.

    Use this when the user asks to set up a recurring task, monitor something,
    or create an automated workflow.

    Always confirm the job configuration with the user before calling this tool.

    Args:
        ctx: Agent context with user information.
        name: Human-readable job name (e.g. "Bank sync", "Morning digest").
        task: The task message the job agent will receive each run.
        trigger_type: One of: "cron", "interval", "event", "oneshot".
        system_prompt: Instructions for the job agent (its role and behavior).
        template: Optional template name used: "sync", "check", "scrape", "digest".
        cron: Cron expression for cron triggers (e.g. "0 7 * * *").
        interval_hours: Hours between runs for interval triggers.
        after_job: Job ID that triggers this job (for event triggers).
        timezone: IANA timezone for cron expressions (e.g. "Europe/Brussels"). If not set, cron runs in UTC.
        notify: Notification policy: "always", "on_failure", "on_output", "silent".
        model: Fully-qualified pydantic-ai model string
            (default: ``anthropic:claude-haiku-4-5-20251001``).
        channel: Notification channel (default: telegram).
        skills: List of skill names the job uses (documentation only).
        timeout_minutes: Max minutes the job can run before being killed (default: 10).
        users: Users this job runs for. Defaults to ``[current_user]``. Pass an
            empty list ``[]`` to create a system-scope job that runs without
            a user context (no per-user credentials, memories, or
            notifications) — useful for shared work like news scraping.

    Returns:
        Confirmation message with the job ID and next run time.
    """
    from marcel_core.jobs import save_job
    from marcel_core.jobs.scheduler import scheduler

    trigger = TriggerSpec(
        type=TriggerType(trigger_type),
        cron=cron,
        interval_seconds=int(interval_hours * 3600) if interval_hours else None,
        after_job=after_job,
        timezone=timezone,
    )

    try:
        notify_policy = NotifyPolicy(notify)
    except ValueError:
        notify_policy = NotifyPolicy.ON_OUTPUT

    job_users = [ctx.deps.user_slug] if users is None else list(users)

    job = JobDefinition(
        name=name,
        description=task,
        users=job_users,
        trigger=trigger,
        system_prompt=system_prompt,
        task=task,
        model=model or 'anthropic:claude-haiku-4-5-20251001',
        skills=skills or [],
        notify=notify_policy,
        channel=channel or 'telegram',
        template=template,
        timeout_seconds=int(timeout_minutes * 60) if timeout_minutes else 600,
    )

    save_job(job)
    scheduler.schedule_job(job)

    next_run = scheduler._schedule.get(job.id)
    next_run_str = next_run.strftime('%Y-%m-%d %H:%M UTC') if next_run else 'on event'

    log.info('[jobs] Created job %s (%s) for user %s', job.id, job.name, ctx.deps.user_slug)
    return (
        f'Job created: **{job.name}** (ID: `{job.id}`)\n'
        f'Trigger: {trigger_type}\n'
        f'Next run: {next_run_str}\n'
        f'Notify: {notify}'
    )


async def list_jobs(ctx: RunContext[MarcelDeps]) -> str:
    """List background jobs visible to the current user.

    Includes both the user's own jobs (``ctx.deps.user_slug`` in ``job.users``)
    and system-scope jobs (``users: []``), since system jobs deliver shared
    value to everyone.

    Args:
        ctx: Agent context with user information.

    Returns:
        Formatted list of jobs, or a message if no jobs exist.
    """
    from marcel_core.jobs import list_jobs as _list_jobs, list_system_jobs
    from marcel_core.jobs.scheduler import scheduler

    jobs = _list_jobs(ctx.deps.user_slug) + list_system_jobs()
    if not jobs:
        return 'No background jobs configured.'

    lines: list[str] = []
    for job in jobs:
        next_run = scheduler._schedule.get(job.id)
        next_str = next_run.strftime('%Y-%m-%d %H:%M UTC') if next_run else 'n/a'
        status_icon = {'active': '\u2705', 'paused': '\u23f8\ufe0f', 'disabled': '\u26d4'}.get(job.status.value, '')
        lines.append(
            f'{status_icon} **{job.name}** (`{job.id}`)\n'
            f'  Trigger: {job.trigger.type.value} | Status: {job.status.value} | Next: {next_str}'
        )

    return '\n\n'.join(lines)


async def get_job(ctx: RunContext[MarcelDeps], job_id: str) -> str:
    """Get detailed information about a specific job including recent runs.

    The caller must be targeted by the job (``ctx.deps.user_slug in job.users``)
    or the job must be system-scope (``users: []``).

    Args:
        ctx: Agent context with user information.
        job_id: The job identifier.

    Returns:
        Detailed job information with recent run history.
    """
    from marcel_core.jobs import SYSTEM_USER, load_job, read_runs
    from marcel_core.jobs.scheduler import scheduler

    job = load_job(job_id)
    if not job or (job.users and ctx.deps.user_slug not in job.users):
        return f'Job `{job_id}` not found.'

    next_run = scheduler._schedule.get(job.id)
    next_str = next_run.strftime('%Y-%m-%d %H:%M UTC') if next_run else 'n/a'

    lines = [
        f'**{job.name}** (`{job.id}`)',
        f'Status: {job.status.value}',
        f'Trigger: {job.trigger.type.value}',
        f'Next run: {next_str}',
        f'Model: {job.model}',
        f'Notify: {job.notify.value}',
        f'Timeout: {job.timeout_seconds // 60}m',
        f'Template: {job.template or "custom"}',
    ]

    if job.consecutive_errors > 0:
        lines.append(f'\u26a0\ufe0f Consecutive errors: {job.consecutive_errors}')

    lines += [
        '',
        f'**Task:** {job.task}',
        '',
        '**Recent runs:**',
    ]

    run_user = ctx.deps.user_slug if job.users else SYSTEM_USER
    runs = read_runs(job_id, run_user, limit=5)
    if not runs:
        lines.append('No runs yet.')
    else:
        for run in runs:
            ts = run.started_at.strftime('%Y-%m-%d %H:%M') if run.started_at else '?'
            status_icon = {
                'completed': '\u2705',
                'failed': '\u274c',
                'timed_out': '\u23f0',
                'running': '\u23f3',
                'pending': '\u23f3',
            }.get(run.status.value, '')
            detail = run.error[:80] if run.error else (run.output[:80] if run.output else '')
            lines.append(f'  {status_icon} {ts} — {run.status.value} {detail}')

    return '\n'.join(lines)


async def update_job(
    ctx: RunContext[MarcelDeps],
    job_id: str,
    *,
    name: str | None = None,
    status: str | None = None,
    cron: str | None = None,
    interval_hours: float | None = None,
    task: str | None = None,
    system_prompt: str | None = None,
    notify: str | None = None,
    model: str | None = None,
    timezone: str | None = None,
    timeout_minutes: float | None = None,
) -> str:
    """Update a job's configuration.

    Only the provided fields are changed; others remain as-is.

    Args:
        ctx: Agent context with user information.
        job_id: The job identifier.
        name: New job name.
        status: New status: "active", "paused", "disabled".
        cron: New cron expression (for cron triggers).
        interval_hours: New interval in hours (for interval triggers).
        task: New task message.
        system_prompt: New system prompt.
        notify: New notify policy.
        model: New model name.
        timezone: IANA timezone for cron expressions (e.g. "Europe/Brussels").
        timeout_minutes: Max minutes the job can run before being killed.

    Returns:
        Confirmation of the update.
    """
    from marcel_core.jobs import load_job, save_job
    from marcel_core.jobs.scheduler import scheduler

    job = load_job(job_id)
    if not job or (job.users and ctx.deps.user_slug not in job.users):
        return f'Job `{job_id}` not found.'

    if name is not None:
        job.name = name
    if status is not None:
        job.status = JobStatus(status)
    if cron is not None:
        job.trigger.cron = cron
    if interval_hours is not None:
        job.trigger.interval_seconds = int(interval_hours * 3600)
    if task is not None:
        job.task = task
    if system_prompt is not None:
        job.system_prompt = system_prompt
    if notify is not None:
        job.notify = NotifyPolicy(notify)
    if model is not None:
        job.model = model
    if timezone is not None:
        job.trigger.timezone = timezone
    if timeout_minutes is not None:
        job.timeout_seconds = int(timeout_minutes * 60)

    job.updated_at = datetime.now(UTC)
    save_job(job)
    scheduler.schedule_job(job)

    log.info('[jobs] Updated job %s for user %s', job_id, ctx.deps.user_slug)
    return f'Job `{job.id}` (**{job.name}**) updated.'


async def delete_job(ctx: RunContext[MarcelDeps], job_id: str) -> str:
    """Delete a job and all its run history.

    The caller must be targeted by the job (or the job must be system-scope).

    Args:
        ctx: Agent context with user information.
        job_id: The job identifier.

    Returns:
        Confirmation of deletion.
    """
    from marcel_core.jobs import delete_job as _delete_job, load_job
    from marcel_core.jobs.scheduler import scheduler

    job = load_job(job_id)
    if not job or (job.users and ctx.deps.user_slug not in job.users):
        return f'Job `{job_id}` not found.'

    deleted = _delete_job(job_id)
    if not deleted:
        return f'Job `{job_id}` not found.'

    scheduler.unschedule_job(job_id)
    log.info('[jobs] Deleted job %s for user %s', job_id, ctx.deps.user_slug)
    return f'Job `{job_id}` deleted.'


async def run_job_now(ctx: RunContext[MarcelDeps], job_id: str) -> str:
    """Manually trigger a job for immediate execution.

    The job runs in the background. Results will be delivered via the job's
    notification channel.

    Args:
        ctx: Agent context with user information.
        job_id: The job identifier.

    Returns:
        Confirmation that the job has been queued.
    """
    import asyncio

    from marcel_core.jobs import SYSTEM_USER, load_job
    from marcel_core.jobs.executor import execute_job_with_retries
    from marcel_core.jobs.scheduler import scheduler

    job = load_job(job_id)
    if not job or (job.users and ctx.deps.user_slug not in job.users):
        return f'Job `{job_id}` not found.'

    run_user = ctx.deps.user_slug if job.users else SYSTEM_USER

    async def _run() -> None:
        run = await execute_job_with_retries(job, trigger_reason='manual', user_slug=run_user)
        await scheduler.emit_event(run_user, job.id, run.status.value)

    asyncio.create_task(_run())
    log.info('[jobs] Manual run triggered for job %s by user %s', job_id, ctx.deps.user_slug)
    return f'Job **{job.name}** has been queued for immediate execution. Results will be delivered via {job.channel}.'


async def job_templates(ctx: RunContext[MarcelDeps]) -> str:
    """List available job templates.

    Templates provide pre-configured defaults for common job patterns.

    Args:
        ctx: Agent context.

    Returns:
        Formatted list of available templates.
    """
    from marcel_core.jobs.templates import list_templates

    templates = list_templates()
    lines = ['**Available job templates:**', '']
    for tpl in templates:
        lines.append(f'- **{tpl["name"]}**: {tpl["description"]}')
    return '\n'.join(lines)


async def job_cache_write(ctx: RunContext[MarcelDeps], key: str, data: str) -> str:
    """Store data in the job cache for other jobs to read later.

    Use this to share data between jobs. For example, a scraping job can
    cache news articles, and a digest job can read them later.

    Args:
        ctx: Agent context.
        key: Cache key name (e.g. "news", "bank_summary").
        data: JSON string of data to store.

    Returns:
        Confirmation message.
    """
    import json as _json

    from marcel_core.jobs.cache import write_cache

    try:
        parsed = _json.loads(data)
    except (ValueError, TypeError):
        parsed = data

    write_cache(ctx.deps.user_slug, key, parsed)
    return f'Cached data under key "{key}".'


async def job_cache_read(ctx: RunContext[MarcelDeps], key: str) -> str:
    """Read cached data written by another job.

    Use this to retrieve data stored by a previous job run (e.g. scraped
    news articles, sync summaries).

    Args:
        ctx: Agent context.
        key: Cache key name to read.

    Returns:
        The cached data as JSON, or an error if the key doesn't exist.
    """
    import json as _json

    from marcel_core.jobs.cache import read_cache

    entry = read_cache(ctx.deps.user_slug, key)
    if entry is None:
        return f'No cached data found for key "{key}".'

    return _json.dumps(entry, ensure_ascii=False, indent=2)
