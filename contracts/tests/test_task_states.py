from __future__ import annotations

import re
from pathlib import Path

from conftest import CONTRACTS, load_api

DOC = CONTRACTS / 'task-states.md'
FINAL = {'stopped'}


def table(heading: str, path: Path = DOC) -> list[list[str]]:
    section = path.read_text().split(f'## {heading}\n', 1)[1].split('\n## ', 1)[0]
    rows = [line for line in section.splitlines() if line.startswith('|')]
    return [[cell.strip() for cell in row.strip('|').split('|')] for row in rows[2:]]


def states_in(cell: str) -> set[str]:
    return {s.strip() for s in cell.split(',')} - {'—'}


def documented_states() -> set[str]:
    return {re.sub(r'`', '', row[0]) for row in table('States')}


def transitions() -> list[tuple[set[str], set[str]]]:
    return [(states_in(row[0]), states_in(row[1])) for row in table('Transitions')]


def test_states_table_matches_the_api_enum():
    enum = set(load_api()['components']['schemas']['TaskState']['enum'])
    assert documented_states() == enum


def test_transitions_use_only_known_states():
    known = documented_states()
    for sources, targets in transitions():
        assert sources <= known and targets <= known, (sources, targets)


def test_every_state_is_reachable_from_a_new_task():
    reachable = {t for sources, targets in transitions() if not sources for t in targets}
    changed = True
    while changed:
        changed = False
        for sources, targets in transitions():
            if sources & reachable and not targets <= reachable:
                reachable |= targets
                changed = True
    assert reachable == documented_states()


def test_only_final_states_have_no_way_out():
    has_exit = {s for sources, _ in transitions() for s in sources}
    assert documented_states() - has_exit == FINAL
