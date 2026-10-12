"""The committed sprite sheet is valid, complete and exactly what build.py makes."""

import importlib.util
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

PIXEL = Path(__file__).resolve().parents[1]
OUT = PIXEL / 'out'
SCHEMA = PIXEL.parents[2] / 'contracts' / 'avatar-sheet.schema.json'

# B-28: every avatar state, and the loop(s) that play it. working(n) shows n spots, n = 1..3.
B28_STATES = {
    'idle': ['idle'],
    'thinking': ['thinking'],
    'working(n)': ['working1', 'working2', 'working3'],
    'needs-you': ['needs_you'],
    'done': ['done'],
}

_spec = importlib.util.spec_from_file_location('avatar_build', PIXEL / 'build.py')
assert _spec and _spec.loader
build = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build)


@pytest.fixture(scope='module')
def sheet():
    return json.loads((OUT / 'giraffe.json').read_text())


@pytest.fixture(scope='module')
def rebuilt(tmp_path_factory):
    out = tmp_path_factory.mktemp('out')
    build.export(str(out))
    return out


def test_sheet_json_matches_the_contract(sheet):
    schema = json.loads(SCHEMA.read_text())
    Draft202012Validator.check_schema(schema)
    errors = [e.message for e in Draft202012Validator(schema).iter_errors(sheet)]
    assert errors == []


@pytest.mark.parametrize('state', B28_STATES)
def test_every_b28_state_has_a_loop(sheet, state):
    for name in B28_STATES[state]:
        loop = sheet['loops'].get(name)
        assert loop, f'{state}: loop {name!r} is missing'
        assert all(0 <= frame < sheet['frame_count'] for frame, _ in loop), name


def test_png_holds_every_frame_the_json_names(sheet):
    w, h, _ = build.read_png(OUT / sheet['image'])
    cols, fw, fh = sheet['columns'], sheet['frame']['width'], sheet['frame']['height']
    rows = -(-sheet['frame_count'] // cols)
    assert (w, h) == (cols * fw, rows * fh)


def test_committed_sheet_is_what_build_makes(rebuilt):
    """Fails when build.py changed but out/ was not rebuilt and committed (or the other way round)."""
    assert (rebuilt / 'giraffe.json').read_text() == (OUT / 'giraffe.json').read_text()
    # compare pixels, not bytes: zlib builds may compress the same pixels differently
    assert build.read_png(rebuilt / 'giraffe.png') == build.read_png(OUT / 'giraffe.png')
    made = sorted(p.name for p in rebuilt.iterdir())
    assert made == sorted(p.name for p in OUT.iterdir())
    for name in made:
        if name.endswith('.gif'):
            assert (rebuilt / name).read_bytes() == (OUT / name).read_bytes(), name
        elif name.endswith('.png'):
            assert build.read_png(rebuilt / name) == build.read_png(OUT / name), name


@pytest.mark.parametrize('n', [1, 2, 3])
def test_working_n_lights_n_spots_in_every_frame(n):
    for pose in build.working_timeline(n):
        assert sorted(pose['glow']) == sorted(build.WORKING_SPOTS[:n])


def test_glowing_spots_change_the_picture():
    """The count is only a status light if each extra spot shows: working1..3 frames all differ."""
    frames = [build.render(build.working_timeline(n)[8]) for n in (1, 2, 3)]
    glow = set(build.RAMPS['G'])
    lit = [sum(1 for c in f.values() if c in glow) for f in frames]
    assert lit[0] < lit[1] < lit[2]


def test_needs_you_shows_the_exclamation_and_hearts_every_frame():
    for pose in build.needs_you_timeline():
        shapes = [shape for shape, _, _ in pose['props']]
        assert 'bang' in shapes
        assert sum(s.startswith('heart') for s in shapes) == 2
    sizes = {tuple(s for s, _, _ in p['props'] if s.startswith('heart')) for p in build.needs_you_timeline()}
    assert len(sizes) == 2, 'the hearts should pulse (swap sizes), not sit still'


def test_done_nods_and_sparkles():
    ticks = build.done_timeline()
    assert max(p['head_dy'] for p in ticks) >= 2
    assert any(p.get('props') for p in ticks)


def test_thinking_dots_appear_one_by_one():
    counts = [len(p['props']) for p in build.thinking_timeline()]
    assert counts[0] == 0 and max(counts) == 3
    assert sorted(set(counts)) == [0, 1, 2, 3]
