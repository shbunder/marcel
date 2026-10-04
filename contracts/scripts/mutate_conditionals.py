"""Delete each conditional (if/then) in every contract, one at a time, and check the suite goes red.

A conditional that survives deletion is a rule no test can fail (.claude/rules/inert-controls.md).
The lead runs this after any contract change: `make -C contracts mutants`. It is not part of the
gate, because it runs the whole suite once per conditional.
"""

from __future__ import annotations

import copy
import json
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


DOCS = (
    'app-api.yaml',
    'runner-api.yaml',
    'session-api.yaml',
    'transcript.schema.json',
    'channel.schema.json',
)


def load(name: str) -> Any:
    text = (CONTRACTS / name).read_text()
    return json.loads(text) if name.endswith('.json') else yaml.safe_load(text)


def dump(name: str, data: Any) -> str:
    return (
        json.dumps(data, indent=1)
        if name.endswith('.json')
        else yaml.safe_dump(data, sort_keys=False)
    )


def fresh_copy(work: Path, ignore: Any, docs: dict[str, Any]) -> None:
    shutil.rmtree(work, ignore_errors=True)
    shutil.copytree(CONTRACTS, work, ignore=ignore)
    for name, data in docs.items():
        (work / name).write_text(dump(name, data))


def main() -> int:
    docs = {name: load(name) for name in DOCS}
    survivors: list[str] = []
    total = 0
    ignore = shutil.ignore_patterns('.venv', '__pycache__', '.pytest_cache', '.ruff_cache')
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / 'contracts'
        # Baseline: the unmutated copy must pass, or every "caught" below means nothing.
        fresh_copy(work, ignore, docs)
        if not suite_passes(work):
            print('The unmutated contracts fail in the scratch copy; fix that first.')
            return 2
        for name, data in docs.items():
            for site in conditionals(data):
                total += 1
                mutated = copy.deepcopy(data)
                target = mutated
                for key in site:
                    target = target[key]
                del target['if'], target['then']
                fresh_copy(work, ignore, {**docs, name: mutated})
                label = f'{name}:{"/".join(map(str, site))}'
                if suite_passes(work):
                    survivors.append(label)
                    print(f'SURVIVED  {label}')
                else:
                    print(f'caught    {label}')
    print(f'{total} conditionals; {len(survivors)} survived deletion.')
    return 1 if survivors else 0


if __name__ == '__main__':
    sys.exit(main())
