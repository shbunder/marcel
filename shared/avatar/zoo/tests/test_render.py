"""Every animal x personality x mood renders, and the drawing rules hold."""

import itertools

import pytest

from zoo import ACCESSORIES, COLORS, MOODS, PERSONALITIES, SPECIES, N, frame, loop, palette, to_rgba
from zoo.core import js_round


@pytest.mark.parametrize('species', SPECIES)
def test_every_personality_and_mood_renders(species):
    for personality, mood in itertools.product(PERSONALITIES, MOODS):
        for f in (0, 5):
            fr = frame({'species': species, 'personality': personality}, mood, f)
            assert len(fr['grid']) == N and all(len(r) == N for r in fr['grid'])
            used = {c for row in fr['grid'] for c in row if c}
            assert used <= set(fr['palette']), f'{species}/{personality}/{mood}: letters without a colour'


@pytest.mark.parametrize('accessory', ACCESSORIES)
def test_every_accessory_renders_on_every_animal(accessory):
    for species in SPECIES:
        assert frame({'species': species, 'accessory': accessory}, f=3)['grid']


def test_hex_colour_and_unknown_names_fall_back():
    assert frame({'species': 'cat', 'color': '#7a5cff'})['palette']['b'] == '#7a5cff'
    default = frame({})
    assert frame({'species': 'unicorn', 'personality': 'weird', 'mood': 'nope'})['grid'] == default['grid']


def test_loop_is_sixteen_frames_of_140_ms():
    frames = loop({'species': 'fox'})
    assert len(frames) == 16 and {f['ms'] for f in frames} == {140}


def test_working_lights_one_lamp_per_session():
    # frame 1: no lamp is in its "off" beat, so exactly `sessions` lamps are lit
    for n in (1, 2, 3):
        fr = frame({'species': 'bear', 'sessions': n}, 'working', 1)
        lamps = [fr['grid'][26][19 - i * 2] for i in range(3)]
        assert lamps.count('x') == n, lamps


def test_accessories_stand_out_from_the_fur():
    green = palette(COLORS['green'], None)
    assert green['g'] != palette(COLORS['rose'], None)['g']  # green animals get a rose bow tie
    assert palette(COLORS['lavender'], None)['v'] == '#2fb39a'  # violet animals get a teal cap


def test_shadow_is_see_through_and_background_blends():
    fr = frame({'species': 'pig'})
    plain = to_rgba(fr)
    on_white = to_rgba(fr, bg='#ffffff')
    alphas = set(plain[3::4])
    assert 0 in alphas and 41 in alphas and 255 in alphas  # empty, shadow, solid
    assert set(on_white[3::4]) == {255}
    big = to_rgba(fr, scale=2)
    assert len(big) == (N * 2) ** 2 * 4


def test_js_round_rounds_halves_up_like_math_round():
    assert (js_round(0.5), js_round(1.5), js_round(2.5), js_round(-0.5)) == (1, 2, 3, 0)


def test_sessions_count_like_js():
    def lamps(sessions):
        fr = frame({'species': 'bear', 'sessions': sessions}, 'working', 1)
        return [fr['grid'][26][19 - i * 2] for i in range(3)].count('x')

    assert (lamps('2'), lamps(None), lamps('lots'), lamps(9), lamps(0)) == (2, 1, 1, 3, 1)
