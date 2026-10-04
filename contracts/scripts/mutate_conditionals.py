"""Delete each conditional (if/then) in app-api.yaml, one at a time, and check the suite goes red.

A conditional that survives deletion is a rule no test can fail (.claude/rules/inert-controls.md).
The lead runs this after any contract change: `make -C contracts mutants`. It is not part of the
gate, because it runs the whole suite once per conditional.
"""

from __future__ import annotations

import copy
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml

CONTRACTS = Path(__file__).resolve().parent.parent
Site = list[str | int]


def conditionals(node: Any, path: Site | None = None) -> list[Site]:
    path = path or []
    found: list[Site] = []
    if isinstance(node, dict):
        if 'if' in node and 'then' in node:
            found.append(path)
        for key, value in node.items():
            found += conditionals(value, [*path, key])
    elif isinstance(node, list):
        for i, value in enumerate(node):
            found += conditionals(value, [*path, i])
    return found


def suite_passes(workdir: Path) -> bool:
    pytest = CONTRACTS / '.venv' / 'bin' / 'pytest'
    result = subprocess.run(
        [str(pytest), '-q', '-x', '-p', 'no:cacheprovider'],
        cwd=workdir,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def main() -> int:
    api = yaml.safe_load((CONTRACTS / 'app-api.yaml').read_text())
    sites = conditionals(api)
    survivors: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / 'contracts'
        for site in sites:
            mutated = copy.deepcopy(api)
            target = mutated
            for key in site:
                target = target[key]
            del target['if'], target['then']
            shutil.rmtree(work, ignore_errors=True)
            ignore = shutil.ignore_patterns('.venv', '__pycache__', '.pytest_cache', '.ruff_cache')
            shutil.copytree(CONTRACTS, work, ignore=ignore)
            (work / 'app-api.yaml').write_text(yaml.safe_dump(mutated, sort_keys=False))
            label = '/'.join(map(str, site))
            if suite_passes(work):
                survivors.append(label)
                print(f'SURVIVED  {label}')
            else:
                print(f'caught    {label}')
    print(f'{len(sites)} conditionals; {len(survivors)} survived deletion.')
    return 1 if survivors else 0


if __name__ == '__main__':
    sys.exit(main())
