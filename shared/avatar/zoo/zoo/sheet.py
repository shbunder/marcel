"""Sprite sheets for the app: one PNG of 32x32 frames plus the JSON of `contracts/avatar-sheet.schema.json`."""

from __future__ import annotations

import re
from typing import Any

from . import png
from .core import ACCESSORIES, COLORS, MOODS, PERSONALITIES, SPECIES, N, frame, js_round, to_rgba

COLUMNS = 8
FRAMES_PER_LOOP = 16
MS = 140

# The app's states (B-28) and the mood each one plays. `idle` shows the personality's resting face, so a
# grumpy agent looks grumpy while it waits; working1..3 light 1..3 lamps on the laptop, one per session.
LOOPS: dict[str, dict[str, Any]] = {
    'idle': {'mood': None},
    'thinking': {'mood': 'thinking'},
    'working1': {'mood': 'working', 'sessions': 1},
    'working2': {'mood': 'working', 'sessions': 2},
    'working3': {'mood': 'working', 'sessions': 3},
    'needs_you': {'mood': 'surprised'},
    'done': {'mood': 'happy'},
}


def check_look(look: dict[str, Any]) -> dict[str, Any]:
    """The look with defaults filled in; a clear error for an unknown animal, colour, personality or mood."""
    for key in ('species', 'color', 'personality', 'accessory', 'mood'):
        if look.get(key) is not None and not isinstance(look[key], str):
            raise ValueError(f'The {key} must be a word, not {look[key]!r}.')
    out = {
        'species': look.get('species') or 'giraffe',
        'color': look.get('color') or 'natural',
        'personality': look.get('personality') or 'professional',
        'accessory': look.get('accessory') or None,
    }
    if out['species'] not in SPECIES:
        raise ValueError(f'Unknown animal {out["species"]!r}. Pick one of: {", ".join(SPECIES)}.')
    color = out['color']
    if color != 'natural' and color not in COLORS and not re.fullmatch(r'#[0-9a-fA-F]{6}', color):
        raise ValueError(f'Unknown colour {color!r}. Use "natural", #rrggbb or one of: {", ".join(COLORS)}.')
    if out['personality'] not in PERSONALITIES:
        raise ValueError(f'Unknown personality {out["personality"]!r}. Pick one of: {", ".join(PERSONALITIES)}.')
    if out['accessory'] and out['accessory'] not in ACCESSORIES:
        raise ValueError(f'Unknown accessory {out["accessory"]!r}. Pick one of: {", ".join(ACCESSORIES)}.')
    mood = look.get('mood')
    if mood and mood not in MOODS:
        raise ValueError(f'Unknown mood {mood!r}. Pick one of: {", ".join(MOODS)}.')
    return out


def slug(look: dict[str, Any]) -> str:
    """A file name for a look, e.g. giraffe-rose-professional (it must match ^[a-z0-9-]+$)."""
    parts = [look['species'], str(look['color']).lstrip('#').lower(), look['personality']]
    if look.get('accessory'):
        parts.append(look['accessory'])
    return re.sub(r'[^a-z0-9-]', '', '-'.join(parts))


def sheet(look: dict[str, Any]) -> tuple[bytes, dict[str, Any]]:
    """Build the sheet for a look: (PNG bytes, the JSON as a dict).

    Each loop is 16 frames at 140 ms. Frames that look the same are stored once, and a frame that repeats
    straight after itself becomes one longer step.
    """
    look = check_look(look)
    frames: list[bytes] = []
    index: dict[bytes, int] = {}
    loops: dict[str, list[list[int]]] = {}
    for name, extra in LOOPS.items():
        steps: list[list[int]] = []
        for f in range(FRAMES_PER_LOOP):
            pixels = to_rgba(frame({**look, **extra}, f=f))
            if pixels not in index:
                index[pixels] = len(frames)
                frames.append(pixels)
            i = index[pixels]
            if steps and steps[-1][0] == i:
                steps[-1][1] += MS
            else:
                steps.append([i, MS])
        if len(steps) > 1 and steps[0][0] == steps[-1][0]:
            # the loop wraps: fold the last step into the first so a still frame is one step
            steps[0][1] += steps.pop()[1]
        loops[name] = steps
    name = slug(look)
    meta = {
        'version': 1,
        'name': look['species'],
        'palette': str(look['color']),
        'image': f'{name}.png',
        'frame': {'width': N, 'height': N},
        'columns': COLUMNS,
        'frame_count': len(frames),
        'loops': loops,
    }
    return _tile(frames, COLUMNS), meta


def _tile(frames: list[bytes], columns: int, scale: int = 1, bg: tuple[int, int, int, int] | None = None) -> bytes:
    """Lay 32x32 RGBA frames out left to right, top to bottom, into one PNG."""
    rows = -(-len(frames) // columns)
    size = N * scale
    width, height = columns * size, rows * size
    out = bytearray(bytes(bg) * (width * height) if bg else bytes(width * height * 4))
    for n, pixels in enumerate(frames):
        ox, oy = (n % columns) * size, (n // columns) * size
        for y in range(size):
            src = (y // scale) * N * 4
            line = b''.join(_blend(pixels[src + x * 4 : src + x * 4 + 4], bg) * scale for x in range(N))
            start = ((oy + y) * width + ox) * 4
            out[start : start + size * 4] = line
    return png.encode(width, height, bytes(out))


def _blend(px: bytes, bg: tuple[int, int, int, int] | None) -> bytes:
    if not bg or px[3] == 255:
        return px
    a = px[3] / 255
    return bytes([js_round(bg[i] * (1 - a) + px[i] * a) for i in range(3)] + [255])


def preview(look: dict[str, Any], mood: str | None = None, scale: int = 4) -> bytes:
    """A PNG for people to look at: every mood's first frame, or all 16 frames of one mood, on a light ground."""
    look = check_look({**look, 'mood': mood})
    if mood:
        frames = [to_rgba(frame(look, mood, f)) for f in range(FRAMES_PER_LOOP)]
    else:
        frames = [to_rgba(frame(look, m)) for m in MOODS]
    return _tile(frames, COLUMNS, scale, (246, 239, 243, 255))
