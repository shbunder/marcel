"""Render the brain workspace template into a real workspace (default `~/marcel/brain`).

    python3 brain/render.py --out ~/marcel/brain \
        --var OWNER_NAME=Shaun \
        --var MARKETPLACE_DIR=/home/shbunder/marcel/plugins \
        --var MEMORY_DIR=/home/shbunder/marcel/data/memory

What it does:
- copies the template files (`CLAUDE.md`, `.claude/settings.json`), filling every `{{NAME}}` with
  its value (JSON-escaped inside a `.json` file) and dropping the template's leading `<!-- … -->` note;
- links `MEMORY.md` and `memory/` to the hub's memory folder (creating an empty `MEMORY.md` and
  `memory/` there when they are missing), so the brain reads and writes the same
  files the hub commits.

It refuses, in one plain sentence, an unknown or missing variable, a relative path, a value over one
line, a placeholder left in the output, and a real file where a link should go. Standard library only:
deploy runs it with the host's `python3`.
"""

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Variable:
    help: str
    default: str | None = None
    path: bool = False  # must be an absolute path


# Every deploy variable the template uses. brain/README.md documents the same list.
VARIABLES: dict[str, Variable] = {
    'AGENT_NAME': Variable("The agent's name, as the family calls it.", default='Marcel'),
    'OWNER_NAME': Variable('The first name of the person this brain works for; it addresses them by it.'),
    'ANIMAL': Variable("The agent's animal, as it speaks of itself.", default='pink giraffe'),
    'PERSONALITY': Variable(
        "The look's personality (app-api `LookPersonality`), for the tone of its answers.",
        default='professional',
    ),
    'MARKETPLACE_DIR': Variable(
        'The deployed `marcel-local` marketplace: a folder with `.claude-plugin/marketplace.json` '
        'that lists a released copy of `plugins/marcel/` (never a working tree).',
        path=True,
    ),
    'MEMORY_DIR': Variable(
        "The hub's memory folder on the host (`<data_dir>/memory`, bind-mounted into the hub as "
        '`/data/memory`).',
        path=True,
    ),
}

# The template files, relative to brain/. Everything else in brain/ (README, renderer, tests) stays behind.
TEMPLATE_FILES = ('CLAUDE.md', '.claude/settings.json')
# Links in the workspace → a path inside MEMORY_DIR.
MEMORY_LINKS = {'MEMORY.md': 'MEMORY.md', 'memory': 'memory'}

PLACEHOLDER = re.compile(r'\{\{\s*([A-Za-z0-9_]*)\s*\}\}')
LEADING_NOTE = re.compile(r'\A<!--.*?-->\s*\n', re.S)


class RenderError(Exception):
    """The template cannot be rendered with these values; the message says why."""


def resolve(given: dict[str, str]) -> dict[str, str]:
    """Check the given values and fill in defaults."""
    unknown = sorted(set(given) - set(VARIABLES))
    if unknown:
        raise RenderError(f'Unknown variable {unknown[0]}. Known: {", ".join(VARIABLES)}.')
    values: dict[str, str] = {}
    for name, var in VARIABLES.items():
        value = given.get(name, var.default)
        if value is None or not value.strip():
            raise RenderError(f'{name} is not set. {var.help}')
        if '\n' in value or '\r' in value:
            raise RenderError(f'{name} must be one line.')
        if var.path and not os.path.isabs(value):
            raise RenderError(f'{name} must be an absolute path, not "{value}".')
        values[name] = value
    return values


def fill(text: str, values: dict[str, str], *, as_json: bool = False) -> str:
    """Replace every `{{NAME}}`; refuse a placeholder with no value."""

    def one(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in values:
            raise RenderError(f'The template uses {{{{{name}}}}}, which is not a known variable.')
        value = values[name]
        return json.dumps(value)[1:-1] if as_json else value

    out = PLACEHOLDER.sub(one, LEADING_NOTE.sub('', text, count=1))
    if '{{' in out or '}}' in out:
        raise RenderError('A placeholder was left in the output.')
    return out


def render_text(rel: str, values: dict[str, str], template_dir: Path = HERE) -> str:
    """One template file, filled."""
    text = (template_dir / rel).read_text(encoding='utf-8')
    out = fill(text, values, as_json=rel.endswith('.json'))
    if rel.endswith('.json'):
        json.loads(out)  # a filled JSON file is still JSON
    return out


def link(path: Path, target: Path) -> None:
    """Point `path` at `target`, replacing an old link but never a real file."""
    if path.is_symlink():
        path.unlink()
    elif path.exists():
        raise RenderError(f'{path} is a real file or folder, not a link. Move it away and render again.')
    path.symlink_to(target)


def ensure_memory(memory_dir: Path) -> None:
    """Make sure both link targets exist, so a first deploy never links to nothing.

    Creates `memory/` and an empty `MEMORY.md` only when they are missing; never touches a file
    that is there. The hub commits them on its next save.
    """
    (memory_dir / 'memory').mkdir(parents=True, exist_ok=True)
    index = memory_dir / 'MEMORY.md'
    if not index.exists():
        index.write_text('', encoding='utf-8')


def render(out: Path, given: dict[str, str], template_dir: Path = HERE) -> list[Path]:
    """Write the workspace into `out` and return the paths written."""
    values = resolve(given)
    rendered = {rel: render_text(rel, values, template_dir) for rel in TEMPLATE_FILES}
    written: list[Path] = []
    for rel, text in rendered.items():
        dest = out / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding='utf-8')
        written.append(dest)
    memory_dir = Path(values['MEMORY_DIR'])
    ensure_memory(memory_dir)
    for name, inside in MEMORY_LINKS.items():
        link(out / name, memory_dir / inside)
        written.append(out / name)
    return written


def parse_vars(pairs: list[str]) -> dict[str, str]:
    given: dict[str, str] = {}
    for pair in pairs:
        name, sep, value = pair.partition('=')
        if not sep:
            raise RenderError(f'"{pair}" is not NAME=value.')
        given[name.strip()] = value
    return given


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Render the brain workspace.')
    parser.add_argument('--out', type=Path, default=Path.home() / 'marcel' / 'brain')
    parser.add_argument('--var', action='append', default=[], metavar='NAME=value')
    args = parser.parse_args(argv)
    try:
        written = render(args.out.expanduser(), parse_vars(args.var))
    except RenderError as err:
        print(f'Brain workspace not rendered: {err}', file=sys.stderr)
        return 1
    print(f'Brain workspace rendered in {args.out}: {len(written)} files and links.')
    return 0


if __name__ == '__main__':  # pragma: no cover
    sys.exit(main())
