"""The Python zoo draws exactly what zoo.js draws (S-19.1): 50 fixtures exported from the JS."""

import pytest

from zoo import frame, from_name, name_hash, rgba_sha256


def _rows(fr: dict) -> list[str]:
    return [''.join(c or '.' for c in row) for row in fr['grid']]


def test_there_are_fifty_fixtures(js):
    assert len(js['fixtures']) == 50


@pytest.mark.parametrize('n', range(50))
def test_frame_is_pixel_identical_to_zoo_js(js, n):
    fx = js['fixtures'][n]
    fr = frame(fx['look'], f=fx['frame'])
    assert fr['palette'] == fx['palette'], f'palette differs for {fx["look"]}'
    assert _rows(fr) == fx['grid'], f'pixels differ for {fx["look"]} frame {fx["frame"]}'
    assert rgba_sha256(fr) == fx['rgba_sha256']


def test_fixtures_cover_every_animal_mood_and_personality(js):
    looks = [fx['look'] for fx in js['fixtures']]
    from zoo import MOODS, PERSONALITIES, SPECIES

    assert {lk['species'] for lk in looks} == set(SPECIES)
    assert {lk['mood'] for lk in looks} == set(MOODS)
    assert {lk['personality'] for lk in looks} == set(PERSONALITIES)


@pytest.mark.parametrize('n', range(10))
def test_name_picks_the_same_look_as_zoo_js(js, n):
    case = js['names'][n]
    look = from_name(case['name'])
    assert (look['species'], look['color'], look['personality']) == (
        case['species'],
        case['color'],
        case['personality'],
    )
    assert name_hash(case['name'].strip().lower()) == case['hash']


def test_same_name_same_look_and_fixed_choices_win():
    assert {**from_name('Emma'), 'name': ''} == {**from_name('  emma '), 'name': ''}
    assert from_name('Emma', {'species': 'frog', 'mood': 'happy'})['species'] == 'frog'
    assert from_name('Emma', {'mood': 'happy'})['mood'] == 'happy'
