"""HTTP executor for skills.

Supported auth types: none, api_key.
oauth2 returns a "not connected" message until Phase 3 adds the OAuth flow.
"""

from __future__ import annotations

import json
import os

import httpx


async def run(config: dict, params: dict, user_slug: str) -> str:  # noqa: ARG001
    """Execute a skill HTTP call and return the result as a plain string.

    Args:
        config: Skill config dict from the registry.
        params: Caller-supplied keyword arguments.
        user_slug: Slug of the requesting user (used for per-user auth lookup in future phases).

    Returns:
        Response body as a string, with response_transform applied if configured.
    """
    auth = config.get('auth', {})
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
    for param_name, spec in config.get('params', {}).items():
        source: str = spec.get('from', '')
        if source.startswith('args.'):
            arg_key = source[5:]
            if arg_key in params:
                query_params[param_name] = str(params[arg_key])
            elif 'default' in spec:
                query_params[param_name] = str(spec['default'])
        elif 'default' in spec:
            query_params[param_name] = str(spec['default'])

    method = config.get('method', 'GET').upper()
    url: str = config['url']

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.request(method, url, headers=headers, params=query_params)
        response.raise_for_status()
        body = response.text

    transform: str = config.get('response_transform', '')
    if transform:
        body = _apply_transform(transform, body)

    return body


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
