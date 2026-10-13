"""Sheets for the app validate against contracts/avatar-sheet.schema.json and hold every frame they name."""

import json

import pytest
from jsonschema import Draft202012Validator

from zoo import COLORS, LOOPS, PERSONALITIES, SPECIES, check_look, from_name, preview, sheet, slug
from zoo import png as pngmod
from zoo.__main__ import main

B28 = ['idle', 'thinking', 'working1', 'working2', 'working3', 'needs_you', 'done']


def _validate(schema: dict, meta: dict) -> list[str]:
    return [e.message for e in Draft202012Validator(schema).iter_errors(meta)]


@pytest.mark.parametrize('species', SPECIES)
def test_every_sheet_validates(schema, species):
    Draft202012Validator.check_schema(schema)
    colors = ['natural', *COLORS]
    for i, personality in enumerate(PERSONALITIES):
        image, meta = sheet({'species': species, 'personality': personality, 'color': colors[i % len(colors)]})
        assert _validate(schema, meta) == [], (species, personality)
        assert sorted(meta['loops']) == sorted(B28) == sorted(LOOPS)
        width, height, _ = pngmod.decode(image)
        rows = -(-meta['frame_count'] // meta['columns'])
        assert (width, height) == (meta['columns'] * 32, rows * 32)
        for steps in meta['loops'].values():
            assert all(0 <= f < meta['frame_count'] for f, _ in steps)
            assert sum(ms for _, ms in steps) == 16 * 140


def test_sheet_is_reproducible_and_frames_are_unique():
    look = {'species': 'giraffe', 'color': 'rose', 'personality': 'professional'}
    a, meta = sheet(look)
    b, _ = sheet(look)
    assert a == b
    _, _, pixels = pngmod.decode(a)
    frames = set()
    width = meta['columns'] * 32
    for n in range(meta['frame_count']):
        ox, oy = (n % meta['columns']) * 32, (n // meta['columns']) * 32
        frames.add(b''.join(pixels[((oy + y) * width + ox) * 4 : ((oy + y) * width + ox + 32) * 4] for y in range(32)))
    assert len(frames) == meta['frame_count']


def test_working_loops_differ_by_their_lamps():
    _, meta = sheet({'species': 'cat'})
    assert meta['loops']['working1'] != meta['loops']['working2'] != meta['loops']['working3']


def test_slug_and_names():
    assert slug(check_look({'species': 'frog', 'color': '#7A5CFF', 'personality': 'bold'})) == 'frog-7a5cff-bold'
    assert slug(check_look({'accessory': 'cap'})) == 'giraffe-natural-professional-cap'


@pytest.mark.parametrize(
    ('look', 'word'),
    [
        ({'species': 'unicorn'}, 'animal'),
        ({'color': 'mauve'}, 'colour'),
        ({'personality': 'x'}, 'personality'),
        ({'mood': 'x'}, 'mood'),
    ],
)
def test_unknown_choices_are_refused_in_plain_words(look, word):
    with pytest.raises(ValueError, match=f'Unknown {word}'):
        check_look(look)


def test_preview_is_a_png():
    w, h, _ = pngmod.decode(preview({'species': 'fox'}, scale=2))
    assert w == 8 * 64 and h > 0
    w, h, _ = pngmod.decode(preview({'species': 'fox'}, mood='laughing', scale=1))
    assert (w, h) == (8 * 32, 2 * 32)


def test_png_round_trip_and_bad_input():
    pixels = bytes(range(256)) * 4  # 16 x 16
    assert pngmod.decode(pngmod.encode(16, 16, pixels)) == (16, 16, pixels)
    with pytest.raises(ValueError):
        pngmod.encode(2, 2, b'\x00')
    with pytest.raises(ValueError):
        pngmod.decode(b'nope')


def test_png_reader_handles_every_filter():
    import struct
    import zlib

    w, h = 3, 5
    raw = b''.join(bytes([kind]) + bytes(range(kind * 12, kind * 12 + 12)) for kind in range(5))
    ihdr = struct.pack('>IIBBBBB', w, h, 8, 6, 0, 0, 0)

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))

    data = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', ihdr) + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b'')
    width, height, out = pngmod.decode(data)
    assert (width, height, len(out)) == (3, 5, 60)
    bad = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0))
    with pytest.raises(ValueError):
        pngmod.decode(bad)


def test_cli_sheet_and_preview(tmp_path, schema, capsys):
    assert (
        main(
            ['sheet', '--animal', 'giraffe', '--color', 'rose', '--personality', 'professional', '--out', str(tmp_path)]
        )
        == 0
    )
    meta = json.loads((tmp_path / 'giraffe-rose-professional.json').read_text())
    assert _validate(schema, meta) == []
    assert (tmp_path / meta['image']).exists()
    assert main(['sheet', '--from-name', 'Emma', '--out', str(tmp_path)]) == 0
    picked = from_name('Emma')
    assert (tmp_path / f'{slug(check_look(picked))}.png').exists()
    out = tmp_path / 'p.png'
    assert main(['preview', '--animal', 'frog', '--mood', 'happy', '--out', str(out)]) == 0
    assert out.exists()
    assert main(['preview', '--animal', 'unicorn']) == 2
    assert 'Unknown animal' in capsys.readouterr().err
