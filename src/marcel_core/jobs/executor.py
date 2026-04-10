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


def _resolve_job_skills(job: JobDefinition) -> list:
    """Load full SkillDoc objects for skills referenced by a job.

    Job skills may use integration IDs like ``"icloud.calendar"`` — the skill
    name is the part before the dot (or the whole string if no dot).
    """
    from marcel_core.skills.loader import load_skills

    all_skills = load_skills(job.user_slug)
    skill_map = {s.name: s for s in all_skills}

    # Extract unique skill names from job.skills (e.g. "icloud.calendar" -> "icloud")
    wanted: set[str] = set()
    for ref in job.skills:
        wanted.add(ref.split('.')[0])

    return [skill_map[name] for name in sorted(wanted) if name in skill_map]


def _build_job_context(job: JobDefinition) -> str:
    """Build the system prompt context for a job agent.

    Assembles: job system prompt + skill docs + credentials + channel prompt.
    Deliberately lean — no MARCEL.md, skill index, or memory selection.
    """
    from marcel_core.harness.context import load_channel_prompt
    from marcel_core.storage.credentials import load_credentials

    parts = [job.system_prompt]

    # Auto-inject full docs for referenced skills
    skills = _resolve_job_skills(job)
    if skills:
        skill_sections = []
        for skill in skills:
            if not skill.is_setup:
                skill_sections.append(f'### {skill.name}\n\n{skill.content}')
        if skill_sections:
            parts.append('## Skill reference\n\n' + '\n\n---\n\n'.join(skill_sections))

    # Inject credentials: from skill requirements + any referenced in system_prompt
    cred_keys: set[str] = set()
    for skill in skills:
        cred_keys.update(skill.credential_keys)

    # Also check vault for keys mentioned literally in the job text
    # (covers credentials not tied to a skill, e.g. DETIJD_ in the scraper prompt)
    all_creds = load_credentials(job.user_slug)
    job_text = job.system_prompt + ' ' + job.task
    for key in all_creds:
        if key in job_text:
            cred_keys.add(key)

    relevant = {k: all_creds[k] for k in sorted(cred_keys) if k in all_creds}
    if relevant:
        lines = ['## Credentials (injected from vault)']
        for key, value in sorted(relevant.items()):
            lines.append(f'- **{key}**: `{value}`')
        parts.append('\n'.join(lines))

    # Channel delivery guidance
    channel_prompt = load_channel_prompt('job')
    parts.append(f'## Channel\n{channel_prompt}')

    return '\n\n---\n\n'.join(parts)


async def execute_job(job: JobDefinition, trigger_reason: str = 'scheduled') -> JobRun:
    """Execute a single job and return the run record."""
    from marcel_core.harness.agent import create_marcel_agent
    from marcel_core.harness.context import MarcelDeps

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

    # Build lean system prompt: task + skill docs + credentials + channel
    system_prompt = _build_job_context(job)

    agent = create_marcel_agent(job.model, system_prompt=system_prompt, role='user')

    # Apply usage limits if configured on the job
    usage_limits = None
    if job.request_limit is not None:
        from pydantic_ai.usage import UsageLimits

        usage_limits = UsageLimits(request_limit=job.request_limit)

    try:
        result = await agent.run(job.task, deps=deps, usage_limits=usage_limits)
        run.output = result.output
        run.status = RunStatus.COMPLETED
    except Exception as exc:
        log.exception('%s-job: job %s (%s) failed', job.user_slug, job.id, job.name)
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
        log.info(
            '%s-job: retrying job %s (%s) attempt %d/%d', job.user_slug, job.id, job.name, attempt, job.max_retries
        )
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
        log.info('%s-job: notification channel=%s msg=%s', job.user_slug, job.channel, message[:100])


async def _notify_telegram(user_slug: str, message: str) -> None:
    """Send a notification via Telegram."""
    try:
        from marcel_core.channels.telegram import bot, sessions
        from marcel_core.channels.telegram.formatting import escape_html

        chat_id = sessions.get_chat_id(user_slug)
        if chat_id:
            await bot.send_message(int(chat_id), escape_html(message))
        else:
            log.warning('%s-job: no Telegram chat ID found', user_slug)
    except Exception:
        log.exception('%s-job: Telegram notification failed', user_slug)
