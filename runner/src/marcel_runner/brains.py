"""`POST /brains`: make (or refresh) the folder one agent's brain runs in (S-17.7, B-38).

The hub runs in Docker and cannot render `brain/` or trust a folder, so it asks the runner, on the
host (`contracts/runner-api.yaml` `prepareBrain`). For agent `<slug>` the runner:

1. renders `brain/` into `~/marcel/brains/<slug>` with the agent's name and the person's name and
   personality (`brain/render.py`), and links its `MEMORY.md` and `memory/` to `agents/<slug>/`
   in the memory repo (the render makes them empty when missing and never overwrites them);
2. installs the marcel plugin there, at project scope, when it is not installed yet;
3. trusts the folder, as it does a workspace clone (`workspaces.accept_trust`).

Safe to repeat: the render rewrites the template files and its own links, and the install and the
trust are skipped once done. The hub then starts the brain with `cwd` set to the folder.
"""

import json
import os
import re
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from marcel_runner.claude_env import scrubbed_env
from marcel_runner.sessions import SessionError
from marcel_runner.workspaces import PLUGIN, accept_trust, trust_problem

SLUG = re.compile(r'^[a-z0-9][a-z0-9-]{0,39}$')
PERSONALITIES = (
    'professional', 'cheerful', 'curious', 'cozy', 'bold', 'cool', 'sporty', 'shy', 'grumpy',
)  # fmt: skip
TEMPLATE = (
    Path(__file__).resolve().parents[3] / 'brain'
)  # the repo's brain/ (the runner runs from source)
TIMEOUT = 120.0

Run = Callable[[list[str], Path], subprocess.CompletedProcess[str]]
Trust = Callable[[Path, Path, dict[str, str]], None]


def _bad(message: str) -> SessionError:
    return SessionError(400, 'bad_request', message)


def parse(raw: bytes) -> dict[str, Any]:
    """The `PrepareBrain` body, checked against the contract. 400 `bad_request` otherwise."""
    try:
        body = json.loads(raw or b'null')
    except (ValueError, UnicodeDecodeError):
        raise _bad('The request body is not valid JSON.') from None
    if not isinstance(body, dict) or set(body) - {'agent_slug', 'name', 'persona'}:
        raise _bad('Send a JSON object with agent_slug, name and persona only.')
    slug, name, persona = body.get('agent_slug'), body.get('name'), body.get('persona')
    if not isinstance(slug, str) or not SLUG.fullmatch(slug):
        raise _bad('agent_slug must be lowercase letters, digits and dashes, 40 at most.')
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 40:
        raise _bad('name must be 1 to 40 characters.')
    if not isinstance(persona, dict) or set(persona) - {'person_name', 'personality'}:
        raise _bad('persona must be an object with person_name and, optionally, personality.')
    person = persona.get('person_name')
    if not isinstance(person, str) or not 1 <= len(person.strip()) <= 40:
        raise _bad('persona.person_name must be 1 to 40 characters.')
    personality = persona.get('personality')
    if personality is not None and personality not in PERSONALITIES:
        raise _bad(f'persona.personality must be one of {", ".join(PERSONALITIES)}.')
    return {
        'slug': slug,
        'name': name.strip(),
        'person': person.strip(),
        'personality': personality,
    }


def _run(argv: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv, cwd=cwd, capture_output=True, text=True, timeout=TIMEOUT,
        stdin=subprocess.DEVNULL, env=scrubbed_env(os.environ), check=False,
    )  # fmt: skip


@dataclass
class Brains:
    claude: Path
    home: Path
    root: Path  # ~/marcel/brains
    memory_root: Path  # the hub's memory folder on the host (~/marcel/data/memory)
    marketplace: Path  # the deployed marcel-local marketplace (~/marcel/plugins)
    template: Path = TEMPLATE
    run: Run = field(default=_run)
    trust: Trust = field(default=accept_trust)

    def prepare(self, raw: bytes) -> dict[str, Any]:
        body = parse(raw)
        folder = self.root / body['slug']
        memory = self.memory_root / 'agents' / body['slug']
        self._render(folder, memory, body)
        self._install(folder)
        self._trust(folder)
        return {'agent_slug': body['slug'], 'cwd': str(folder), 'memory_dir': str(memory)}

    def route(self, method: str, raw: bytes) -> tuple[int, dict[str, Any]]:
        if method != 'POST':
            return 405, {'code': 'method_not_allowed', 'message': '/brains only answers POST.'}
        try:
            return 200, self.prepare(raw)
        except SessionError as e:
            return e.status, e.body()

    def _render(self, folder: Path, memory: Path, body: dict[str, Any]) -> None:
        values = {
            'AGENT_NAME': body['name'],
            'OWNER_NAME': body['person'],
            'MARKETPLACE_DIR': str(self.marketplace),
            'MEMORY_DIR': str(memory),
        }
        if body['personality']:
            values['PERSONALITY'] = body['personality']
        argv = [sys.executable, str(self.template / 'render.py'), '--out', str(folder)]
        for key, value in values.items():
            argv += ['--var', f'{key}={value}']
        folder.parent.mkdir(parents=True, exist_ok=True)
        p = self._exec(argv, self.template, 'render')
        if p.returncode != 0:
            said = (p.stderr or p.stdout).strip()
            raise SessionError(
                422, 'render_failed', f'The brain folder was not rendered. {said}', said
            )

    def _install(self, folder: Path) -> None:
        if self._installed(folder):
            return
        argv = [str(self.claude), 'plugin', 'install', PLUGIN, '--scope', 'project']
        p = self._exec(argv, folder, 'the plugin install')
        if p.returncode != 0:
            said = (p.stderr or p.stdout).strip()
            raise SessionError(
                422, 'clone_failed', f'The marcel plugin could not be installed in {folder}.', said
            )

    def _installed(self, folder: Path) -> bool:
        """Whether Claude Code lists the marcel plugin installed for this folder."""
        path = self.home / '.claude' / 'plugins' / 'installed_plugins.json'
        try:
            data: Mapping[str, Any] = json.loads(path.read_text('utf-8'))
        except (OSError, ValueError):
            return False
        rows = (data.get('plugins', data) if isinstance(data, dict) else {}).get(PLUGIN, [])
        return any(isinstance(r, dict) and r.get('projectPath') == str(folder) for r in rows)

    def _trust(self, folder: Path) -> None:
        if trust_problem(self.home, folder) is None:
            return
        try:
            self.trust(self.claude, folder, scrubbed_env(os.environ, self.home))
        except SessionError as e:
            raise SessionError(422, 'not_trusted', e.message, e.detail) from None

    def _exec(self, argv: list[str], cwd: Path, what: str) -> subprocess.CompletedProcess[str]:
        try:
            return self.run(argv, cwd)
        except FileNotFoundError:
            raise SessionError(
                503, 'claude_unavailable', f'{argv[0]} is missing, so {what} could not run.'
            ) from None
        except subprocess.TimeoutExpired:
            raise SessionError(
                503, 'claude_timeout', f'{what} did not finish within {TIMEOUT:g} s.'
            ) from None


def brains_from_env(claude: Path, home: Path, env: Mapping[str, str] | None = None) -> Brains:
    env = os.environ if env is None else env
    marcel = home / 'marcel'
    return Brains(
        claude=claude,
        home=home,
        root=Path(env.get('MARCEL_BRAINS') or marcel / 'brains'),
        memory_root=Path(env.get('MARCEL_MEMORY_ROOT') or marcel / 'data' / 'memory'),
        marketplace=Path(env.get('MARCEL_MARKETPLACE_DIR') or marcel / 'plugins'),
    )
