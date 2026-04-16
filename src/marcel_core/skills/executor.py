"""HTTP, shell, and python executor for skills.

Supported skill types:
- http (default): Makes HTTP requests. Auth types: none, api_key.
  oauth2 returns a "not connected" message until Phase 3 adds the OAuth flow.
- shell: Runs a local shell command. Command string supports {param} substitution.
- python: Dispatches to a registered integration handler function.
"""

from __future__ import annotations

import asyncio
import json
import os

import httpx

from marcel_core.skills.registry import SkillConfig


async def run(config: SkillConfig, params: dict, user_slug: str) -> str:
    """Execute a skill and return the result as a plain string.

    Dispatches to the appropriate executor based on ``config.type``.

    Args:
        config: Typed skill configuration from the registry.
        params: Caller-supplied keyword arguments.
        user_slug: Slug of the requesting user (used for per-user auth lookup).

    Returns:
        Response body as a string, with response_transform applied if configured.
    """
    if config.type == 'python':
        return await _run_python(config, params, user_slug)
    if config.type == 'shell':
        return await _run_shell(config, params)

    # Default: HTTP skill
    auth = config.auth
    auth_type = auth.get('type', 'none')

    if auth_type == 'oauth2':
        provider = auth.get('provider', 'unknown').capitalize()
        return f'User has not connected {provider} yet. Ask them to connect it first.'

    headers: dict[str, str] = {}
    query_params: dict[str, str] = {}

    if auth_type == 'api_key':
        env_var = auth.get('env_var', '')
        key_value = os.environ.get(env_var, '') if env_var else ''
        if auth.get('location', 'header') == 'query':
            query_params[auth.get('param_name', 'api_key')] = key_value
        else:
            headers[auth.get('header_name', 'Authorization')] = key_value

    # Resolve query params from skill spec
    for param_name, spec in config.params.items():
        source: str = spec.get('from', '')
        if source.startswith('args.'):
            arg_key = source[5:]
            if arg_key in params:
                query_params[param_name] = str(params[arg_key])
            elif 'default' in spec:
                query_params[param_name] = str(spec['default'])
        elif 'default' in spec:
            query_params[param_name] = str(spec['default'])

    method = config.method.upper()
    url = config.url or ''

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.request(method, url, headers=headers, params=query_params)
        response.raise_for_status()
        body = response.text

    if config.response_transform:
        body = _apply_transform(config.response_transform, body)

    return body


async def _run_shell(config: SkillConfig, params: dict) -> str:
    """Execute a shell command and return combined stdout+stderr.

    The ``command`` field in the skill config supports ``{param}`` placeholders
    that are replaced with values from *params* (or defaults from the skill spec).
    """
    template: str = config.command or ''
    if not template:
        return 'Shell skill has no command configured.'

    # Resolve params with defaults
    resolved: dict[str, str] = {}
    for key, spec in config.params.items():
        if key in params:
            resolved[key] = str(params[key])
        elif 'default' in spec:
            resolved[key] = str(spec['default'])

    try:
        command = template.format(**resolved)
    except KeyError as exc:
        return f'Missing required parameter: {exc}'

    proc = await asyncio.create_subprocess_shell(
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
    output = stdout.decode().strip() if stdout else ''
    rc = proc.returncode

    if rc != 0:
        raise RuntimeError(f'Command exited with code {rc}.\n{output}')

    return output or '(command completed with no output)'


async def _run_python(config: SkillConfig, params: dict, user_slug: str) -> str:
    """Dispatch to a registered python integration handler."""
    from marcel_core.skills.integrations import get_handler

    handler_name: str = config.handler or ''
    handler = get_handler(handler_name)
    return await handler(params, user_slug)


def _apply_transform(transform: str, body: str) -> str:
    if not transform.startswith('jq:'):
        return body
    expr = transform[3:]
    try:
        import jq  # type: ignore[import]

        data = json.loads(body)
        result = jq.first(expr, data)
        return json.dumps(result, indent=2, ensure_ascii=False)
    except ImportError:
        return body  # jq not installed — return raw
    except Exception as exc:  # noqa: BLE001
        return f'Transform error: {exc}\n\n{body}'
