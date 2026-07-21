"""Jobs tool-bundle capability (FEAT-260721-51f9e3).

Domain-owns-factory: the jobs domain declares its own tool tier and packages
the job CRUD tools as one ``Capability`` bundle. Aggregated into
``_TOOL_REGISTRY`` by ``harness/agent.py`` for the role gate; attached by
``composition.build_capabilities`` narrowed by ``tool_filter``.
"""

from __future__ import annotations

from collections.abc import Callable

from pydantic_ai.capabilities import Capability

from marcel_core.harness.context import MarcelDeps
from marcel_core.jobs import tool as job_tools

JOB_TOOLS: list[tuple[str, Callable, str | None]] = [
    ('create_job', job_tools.create_job, None),
    ('list_jobs', job_tools.list_jobs, None),
    ('get_job', job_tools.get_job, None),
    ('update_job', job_tools.update_job, None),
    ('delete_job', job_tools.delete_job, None),
    ('run_job_now', job_tools.run_job_now, None),
    ('job_templates', job_tools.job_templates, None),
    ('job_cache_write', job_tools.job_cache_write, None),
    ('job_cache_read', job_tools.job_cache_read, None),
]


def build_jobs_tool_capability(role: str, tool_filter: set[str] | None) -> Capability[MarcelDeps] | None:
    """Job scheduling CRUD bundle, role-gated and narrowed (None when empty)."""
    from marcel_core.tools.capability import build_tool_bundle

    return build_tool_bundle('job-tools', 'Scheduled-job management tools.', JOB_TOOLS, role, tool_filter)
