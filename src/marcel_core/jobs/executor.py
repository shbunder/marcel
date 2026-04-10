"""Job executor — runs a job definition as a headless Marcel agent turn.

The executor:
1. Creates a MarcelDeps context for the job's user
2. Builds a system prompt from the job definition + user profile
3. Creates a Marcel agent with the job's model and tools
4. Runs the agent with the job's task message (non-streaming)
5. Captures output, logs the run, sends notifications
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from marcel_core.jobs import append_run
from marcel_core.jobs.models import JobDefinition, JobRun, NotifyPolicy, RunStatus

log = logging.getLogger(__name__)

# Credential prefixes to inject per skill reference in job definition.
# Maps a keyword found in job.skills or job.task to credential key prefixes.
_SKILL_CREDENTIAL_PREFIXES: dict[str, list[str]] = {
    'banking': ['ENABLEBANKING_'],
    'icloud': ['ICLOUD_'],
    'detijd': ['DETIJD_'],
    'tijd': ['DETIJD_'],
}


def _credential_block(job: JobDefinition) -> str:
    """Build a credentials section for injection into the job system prompt.

    Scans the job's skills list and task text for known keywords, then loads
    matching credentials from the user's encrypted vault.
    """
    from marcel_core.storage.credentials import load_credentials

    # Collect which credential prefixes are relevant
    prefixes: set[str] = set()
    search_text = ' '.join(job.skills).lower() + ' ' + job.task.lower() + ' ' + job.system_prompt.lower()
    for keyword, prefs in _SKILL_CREDENTIAL_PREFIXES.items():
        if keyword in search_text:
            prefixes.update(prefs)

    if not prefixes:
        return ''

    creds = load_credentials(job.user_slug)
    relevant = {k: v for k, v in creds.items() if any(k.startswith(p) for p in prefixes)}

    if not relevant:
        return ''

    lines = ['## Credentials (injected from vault)']
    for key, value in sorted(relevant.items()):
        lines.append(f'- **{key}**: `{value}`')
    return '\n'.join(lines)


async def execute_job(job: JobDefinition, trigger_reason: str = 'scheduled') -> JobRun:
    """Execute a single job and return the run record."""
    from marcel_core.harness.agent import create_marcel_agent
    from marcel_core.harness.context import MarcelDeps, build_instructions_async

    run = JobRun(
        job_id=job.id,
        trigger_reason=trigger_reason,
        status=RunStatus.RUNNING,
        started_at=datetime.now(UTC),
    )

    deps = MarcelDeps(
        user_slug=job.user_slug,
        conversation_id=f'job:{job.id}:{run.run_id}',
        channel='job',
        model=job.model,
        role='user',
    )

    # Inject credentials referenced by the job's skills into the system prompt
    cred_block = _credential_block(job)

    # Build system prompt: job's own prompt + credentials + user profile context
    base_instructions = await build_instructions_async(deps, query=job.task)
    parts = [job.system_prompt]
    if cred_block:
        parts.append(cred_block)
    parts.append(f'---\n\n{base_instructions}')
    system_prompt = '\n\n'.join(parts)

    agent = create_marcel_agent(job.model, system_prompt=system_prompt, role='user')

    try:
        result = await agent.run(job.task, deps=deps)
        run.output = result.output
        run.status = RunStatus.COMPLETED
    except Exception as exc:
        log.exception('Job %s (%s) failed for user %s', job.id, job.name, job.user_slug)
        run.error = str(exc)
        run.status = RunStatus.FAILED

    run.finished_at = datetime.now(UTC)
    append_run(job.user_slug, job.id, run)
    return run


async def execute_job_with_retries(job: JobDefinition, trigger_reason: str = 'scheduled') -> JobRun:
    """Execute a job with retry logic on failure."""
    run = await execute_job(job, trigger_reason)

    attempt = 0
    while run.status == RunStatus.FAILED and attempt < job.max_retries:
        attempt += 1
        log.info('Retrying job %s (%s), attempt %d/%d', job.id, job.name, attempt, job.max_retries)
        await asyncio.sleep(job.retry_delay_seconds)
        run = await execute_job(job, trigger_reason)
        run.retry_count = attempt

    # Notify based on policy
    await _notify_if_needed(job, run)

    return run


async def _notify_if_needed(job: JobDefinition, run: JobRun) -> None:
    """Send a notification to the user based on the job's notify policy."""
    should_notify = False

    if job.notify == NotifyPolicy.ALWAYS:
        should_notify = True
    elif job.notify == NotifyPolicy.ON_FAILURE and run.status == RunStatus.FAILED:
        should_notify = True
    elif job.notify == NotifyPolicy.ON_OUTPUT and run.output.strip():
        should_notify = True
    # SILENT: never notify

    if not should_notify:
        return

    # Build notification message
    if run.status == RunStatus.COMPLETED:
        message = run.output.strip() if run.output.strip() else f'Job "{job.name}" completed.'
    else:
        message = f'Job "{job.name}" failed: {run.error or "unknown error"}'

    if job.channel == 'telegram':
        await _notify_telegram(job.user_slug, message)
    else:
        log.info('[job-notify] channel=%s user=%s msg=%s', job.channel, job.user_slug, message[:100])


async def _notify_telegram(user_slug: str, message: str) -> None:
    """Send a notification via Telegram."""
    try:
        from marcel_core.channels.telegram import bot, sessions
        from marcel_core.channels.telegram.formatting import escape_html

        chat_id = sessions.get_chat_id(user_slug)
        if chat_id:
            await bot.send_message(int(chat_id), escape_html(message))
        else:
            log.warning('[job-notify] No Telegram chat ID for user %s', user_slug)
    except Exception:
        log.exception('[job-notify] Telegram notification failed for user %s', user_slug)
