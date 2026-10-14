#!/usr/bin/env python3
"""PreToolUse guard for Bash. Blocks broad staging, so every file in a commit is one someone named.

Blocked (see .claude/rules/git-staging.md):
- git add . | -A | --all | -u | --update | a bare directory "./" or ":/"
- git commit -a | --all (and combined short flags such as -am)

Stdin: JSON hook payload (tool_name, tool_input.command, ...).
Exit 0 allows the call; exit 2 blocks it and shows the stderr message to Claude.
"""

from __future__ import annotations

import json
import re
import shlex
import sys

BROAD_ADD = {'.', './', ':/', '-A', '--all', '-u', '--update', '--no-ignore-removal'}
# git's own options that take a value, so `git -C dir add .` is still seen as `add .`
GIT_VALUE_OPTIONS = {'-C', '-c', '--git-dir', '--work-tree', '--namespace'}


def segments(command: str) -> list[list[str]]:
    """The command split into simple commands at ;, &&, ||, | and newlines, each as words."""
    words: list[list[str]] = []
    for part in re.split(r'&&|\|\||[;|\n]', command):
        try:
            tokens = shlex.split(part)
        except ValueError:
            tokens = part.split()
        if tokens:
            words.append(tokens)
    return words


def git_subcommand(tokens: list[str]) -> tuple[str, list[str]] | None:
    """('add', [args]) for `git [opts] add args`, else None."""
    if not tokens or tokens[0].rsplit('/', 1)[-1] != 'git':
        return None
    i = 1
    while i < len(tokens) and tokens[i].startswith('-'):
        i += 2 if tokens[i] in GIT_VALUE_OPTIONS else 1
    if i >= len(tokens):
        return None
    return tokens[i], tokens[i + 1 :]


def broad(sub: str, args: list[str]) -> str | None:
    """The offending argument, or None when the call names its files."""
    if sub == 'add':
        return next((a for a in args if a in BROAD_ADD), None)
    if sub == 'commit':
        rest = iter(args)
        for a in rest:
            if a == '--':
                break  # paths follow
            if a in {'-m', '-F', '-C', '-c'}:
                next(rest, None)  # the message or file, not a flag
            elif a == '--all' or (re.fullmatch(r'-[a-zA-Z]+', a) and 'a' in a[1:]):
                return a
    return None


def main() -> int:
    # Fail open on malformed input so a broken hook never blocks all shell use.
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError, ValueError):
        return 0
    if data.get('tool_name') != 'Bash':
        return 0
    command = (data.get('tool_input') or {}).get('command') or ''

    for tokens in segments(command):
        found = git_subcommand(tokens)
        if not found:
            continue
        sub, args = found
        offending = broad(sub, args)
        if offending:
            sys.stderr.write(
                f'\n🛑 Blocked: `git {sub} {offending}` stages files you did not name.\n'
                '   Run `git status`, then `git add <path> <path> …` with each file by name.\n'
                '   See .claude/rules/git-staging.md.\n'
            )
            return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
