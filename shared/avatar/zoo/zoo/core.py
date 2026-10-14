"""Pixel Zoo: old-school (Game Boy Zelda) avatars, built from parts.

A Python port of `scratch/spikes/SP5-avatar/zoo/zoo.js`, pixel for pixel (the tests compare both on fixtures
exported from the JS). An avatar is a *look* (animal x colour x personality, optionally an accessory) shown in a
*mood*. A name can pick the look: the same name always gives the same animal.

How a frame is drawn, the way a pixel artist would by hand:
  1. parts (ears, head, body, tail...) are small fill maps on a 32x32 grid, stacked back to front;
  2. each part gets a 1-px dark outline where it meets the empty canvas or a part behind it;
  3. one shade on the bottom/right edge, one highlight on the top-left;
  4. the face (eyes, brows, mouth, cheeks) and effects (!, ?, Zz, tears) are stamped on last.

Only the standard library is used. Where JS and Python differ (rounding, `%` on floats, 32-bit integer maths,
UTF-16 strings), the helpers below copy JS's behaviour so both make the same pixels.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Callable
from typing import Any

N = 32

Grid = list[list[str | None]]
Part = dict[str, Any]


# ------------------------------------------------------------------ JS-compatible maths
def js_round(v: float) -> int:
    """Math.round: halves go up (Python's round() goes to the even number)."""
    return math.floor(v + 0.5)


def js_mod(a: float, b: float) -> float:
    """JS `%`: the result keeps the sign of `a`."""
    return math.fmod(a, b)


def js_sign(v: float) -> float:
    return 0.0 if v == 0 else (1.0 if v > 0 else -1.0)


def imul(a: int, b: int) -> int:
    """Math.imul followed by `>>> 0`."""
    return (a * b) & 0xFFFFFFFF


# ------------------------------------------------------------------ colour
def hex_to_hsl(hex_: str) -> tuple[float, float, float]:
    h = hex_.replace('#', '')
    r, g, b = (int(h[i : i + 2], 16) / 255 for i in (0, 2, 4))
    mx, mn = max(r, g, b), min(r, g, b)
    light = (mx + mn) / 2
    if mx == mn:
        return 0.0, 0.0, light
    d = mx - mn
    s = d / (2 - mx - mn) if light > 0.5 else d / (mx + mn)
    if mx == r:
        hu = (g - b) / d + (6 if g < b else 0)
    elif mx == g:
        hu = (b - r) / d + 2
    else:
        hu = (r - g) / d + 4
    return hu * 60, s, light


def _clamp(v: float) -> float:
    return max(0.0, min(1.0, v))


def hsl_to_hex(h: float, s: float, light: float) -> str:
    h = js_mod(js_mod(h, 360) + 360, 360)
    s, light = _clamp(s), _clamp(light)
    a = s * min(light, 1 - light)

    def k(n: float) -> float:
        return js_mod(n + h / 30, 12)

    def f(n: float) -> float:
        return light - a * max(-1, min(k(n) - 3, min(9 - k(n), 1)))

    return '#' + ''.join(f'{js_round(v * 255):02x}' for v in (f(0), f(8), f(4)))


def towards(h: float, target: float, deg: float) -> float:
    """Move a hue a few degrees towards a target (shadows lean cool and purple, highlights warm)."""
    d = js_mod(target - h + 540, 360) - 180
    return h + js_sign(d) * min(abs(d), deg)


def palette(base: str, accent_hex: str | None) -> dict[str, str]:
    """One base colour -> the whole palette of an avatar. Letters are what the parts are drawn with."""
    h, s, light = hex_to_hsl(base)
    grey = s < 0.12
    sh = s if grey else s * 0.85
    pal = {
        'b': base,  # body
        'd': hsl_to_hex(towards(h, 265, 12), sh, light - 0.13),  # shade
        'l': hsl_to_hex(towards(h, 55, 10), s, min(0.95, light + 0.1)),  # highlight
        'o': hsl_to_hex(towards(h, 285, 30), 0.14 if grey else 0.4, 0.15),  # outline and eyes
        'c': '#fff4e4',  # cream (muzzle, belly)
        'C': '#f2d6c0',
        'i': '#ff9db0',  # inside of ears, noses
        'p': '#ff7896',  # blush
        'w': '#ffffff',
        'r': '#a8324e',  # mouth
        'R': '#ff8fa6',  # tongue
        't': '#a5e4ff',  # tears
        'T': '#4aa8e8',
        'y': '#ffd83d',  # stars, sparkles, crown
        'Y': '#e89a1c',
        'h': '#ff4d6d',  # hearts, anger
        'H': '#c2253f',
        'g': '#2fb39a',  # teal accessories (bow tie)
        'G': '#1c7d6c',
        'v': '#9a7bff',  # violet accessories (cap, scarf)
        'V': '#6a4fd1',
        'q': '#4f7f7c',  # laptop lid, its shade, screen light
        'Q': '#355a58',
        'x': '#c8f3ea',
        'e': '#2fb39a',  # the signature bow every avatar wears, and its knot (Marcel's teal)
        'E': '#3f6b6a',
        'k': '#2b2433',  # sunglasses
        'S': 'rgba(60,30,60,0.16)',  # ground shadow
    }

    # an accessory must stand out from the fur: green and teal animals get a rose bow tie, violet ones a teal cap
    def near(a: float, b: float, r: float) -> bool:
        return not grey and s > 0.25 and abs(js_mod(a - b + 540, 360) - 180) < r

    if near(h, 150, 55):
        pal['g'], pal['G'] = '#f0607e', '#b83a5c'
        pal['e'], pal['E'] = '#f0607e', '#b83a5c'
    if near(h, 260, 45) or near(h, 300, 30):
        pal['v'], pal['V'] = '#2fb39a', '#1c7d6c'
    if accent_hex:
        ah, as_, al = hex_to_hsl(accent_hex)
        pal['a'] = accent_hex
        pal['A'] = hsl_to_hex(towards(ah, 265, 14), as_, al - 0.1)
    else:
        pal['a'] = hsl_to_hex(towards(h, 330, 14), s * 0.6, light - 0.2)  # spots, stripes, markings
        pal['A'] = hsl_to_hex(towards(h, 300, 22), s * 0.55, light - 0.3)
    return pal


SHADE = {'b': 'd', 'c': 'C', 'a': 'A', 'y': 'Y', 'g': 'G', 'v': 'V', 'h': 'H', 'q': 'Q'}


# ------------------------------------------------------------------ shapes
def blob(w: int, h: int, ch: str, top: list[int] | None = None, bottom: list[int] | None = None) -> list[str]:
    """A filled block w x h with rounded corners; top/bottom list how far the first/last rows are pulled in."""
    top = top or []
    bottom = bottom or top[::-1]
    rows = []
    for y in range(h):
        ins = top[y] if y < len(top) else 0
        from_end = h - 1 - y
        if from_end < len(bottom):
            ins = max(ins, bottom[len(bottom) - 1 - from_end])
        rows.append('.' * ins + ch * max(0, w - 2 * ins) + '.' * ins)
    return rows


def mirror(rows: list[str]) -> list[str]:
    return [r[::-1] for r in rows]


# A part: rows of letters at (x, y). ol: "full" (outline all round, drawn over what is behind),
# "outer" (outline only against empty canvas: a patch that merges into its part), "none".
def L(x: int, y: int, rows: list[str], **opt: Any) -> Part:  # noqa: N802
    return {'x': x, 'y': y, 'rows': rows, 'ol': 'full', 'shade': True, 'group': 'body', **opt}


def H(x: int, y: int, rows: list[str], **opt: Any) -> Part:  # noqa: N802
    return L(x, y, rows, **{'group': 'head', **opt})


def P(x: int, y: int, rows: list[str], **opt: Any) -> Part:  # noqa: N802
    """A painted detail: no outline, no shading."""
    return H(x, y, rows, **{'ol': 'none', 'shade': False, **opt})


# ------------------------------------------------------------------ species
# Face layout: eyeL/eyeR = top-left pixel of each 2-wide eye; mouth = centre line and top row;
# cheeks = blush; fx = where symbols float; top/neck/flower = where accessories sit.
ROUND_FACE: dict[str, Any] = {
    'eyeL': (11, 12),
    'eyeR': (19, 12),
    'mouth': (16, 16),
    'cheekL': (8, 15),
    'cheekR': (22, 15),
    'fx': (27, 0),
    'top': (16, 6),
    'neck': (16, 20),
    'flower': (8, 6),
}


def ROUND_HEAD() -> Part:  # noqa: N802
    return H(6, 6, blob(20, 15, 'b', [4, 2, 1, 1], [1, 1, 2, 4]))


def BODY(limb: str = 'b') -> list[Part]:  # noqa: N802
    return [
        L(9, 19, blob(14, 10, 'b', [3, 1], [1])),  # torso
        L(12, 22, blob(8, 6, 'c', [2], [1, 2]), ol='none'),  # belly
        L(8, 26, blob(6, 4, limb, [1], [0])),  # feet
        L(18, 26, blob(6, 4, limb, [1], [0])),
        L(6, 21, blob(4, 5, limb, [1], [1])),  # arms
        L(22, 21, blob(4, 5, limb, [1], [1])),
    ]


SHADOW = L(7, 29, blob(18, 3, 'S', [3], [3]), ol='none', shade=False, group='shadow')
EAR_TRI = ['b.....', 'bb....', 'bbb...', 'bbbb..', 'bbbbb.', 'bbbbbb', 'bbbbbb']
FOX_EAR = ['b......', 'bb.....', 'bbb....', 'bbbb...', 'bbbbb..', 'bbbbbb.', 'bbbbbbb', 'bbbbbbb']
PIG_EAR = ['bb....', 'bbbb..', 'bbbbb.', '.bbbbb', '..bbb.']
WHISKERS = ['ooo', '...', 'ooo']


def _giraffe() -> list[Part]:
    return [
        SHADOW,
        L(8, 20, blob(16, 9, 'b', [3, 1], [1])),  # body
        P(9, 22, ['aa', 'aa']),
        P(21, 22, ['aa', 'aa']),
        P(10, 25, ['aa']),
        P(20, 25, ['aa']),
        L(10, 22, blob(4, 8, 'b', [0], [0])),  # front legs
        L(18, 22, blob(4, 8, 'b', [0], [0])),
        P(10, 29, ['aaaa']),
        P(18, 29, ['aaaa']),
        P(11, 24, ['a']),
        P(19, 25, ['a']),
        L(13, 11, blob(6, 12, 'b')),  # neck
        P(13, 14, ['aa....', 'aa..aa', '....aa', 'a.....', 'a..aa.', '...aa.', 'a.....']),
        H(11, 0, ['aa', 'aa', 'bb', 'bb'], shade=False, front=True),  # horns
        H(19, 0, ['aa', 'aa', 'bb', 'bb'], shade=False, front=True),
        H(4, 5, blob(6, 3, 'b', [1], [0])),  # ears, sticking out
        H(22, 5, blob(6, 3, 'b', [1], [0])),
        P(5, 6, ['iiii']),
        P(23, 6, ['iiii']),
        H(9, 2, blob(14, 14, 'b', [3, 1, 0], [0, 1, 2, 4])),  # head
        H(11, 9, blob(10, 7, 'c', [1, 0], [0, 1, 3]), ol='none', light=False),  # muzzle
        P(17, 3, ['aa', 'aa']),  # head spots
        P(10, 6, ['a', 'a']),
        P(13, 9, ['d....d']),  # nostrils
    ]


def _crocodile() -> list[Part]:
    return [
        SHADOW,
        L(21, 20, ['....b.', '...bbb', '..bbbb', 'bbbbb.', 'bbbb..']),  # tail
        *BODY(),
        P(13, 22, ['CCCCCC', '......', 'CCCCCC', '......']),
        H(5, 6, blob(22, 8, 'b', [2, 0], [0])),  # skull
        H(7, 10, blob(18, 10, 'b', [0], [0, 1, 3]), ol='outer', light=False),  # long snout
        H(7, 2, blob(7, 7, 'b', [2, 0], [0]), ol='outer', shade=False),  # eye bumps, merged into the skull
        H(18, 2, blob(7, 7, 'b', [2, 0], [0]), ol='outer', shade=False),
        P(14, 13, ['d..d']),  # nostrils
    ]


def _frog() -> list[Part]:
    return [
        SHADOW,
        *BODY(),
        H(7, 3, blob(8, 7, 'b', [2, 1], [0])),
        H(17, 3, blob(8, 7, 'b', [2, 1], [0])),
        H(4, 7, blob(24, 13, 'b', [4, 1, 0], [0, 1, 3])),
        P(8, 4, blob(6, 4, 'c', [1], [0])),
        P(18, 4, blob(6, 4, 'c', [1], [0])),
        P(7, 8, ['b' * 8]),
        P(17, 8, ['b' * 8]),
    ]


def _cat() -> list[Part]:
    inner = ['i...', 'ii..', 'iii.']
    return [
        SHADOW,
        L(23, 17, ['....bb', '....bb', '...bb.', '...bb.', '..bb..', '.bb...', 'bb....']),  # tail
        *BODY(),
        H(6, 1, EAR_TRI),
        H(20, 1, mirror(EAR_TRI)),
        P(7, 3, inner),
        P(21, 3, mirror(inner)),
        ROUND_HEAD(),
        P(13, 6, ['a.a.a.', 'a.a.a.', '..a...']),  # tabby stripes
        P(15, 15, ['ii']),  # nose
        P(3, 15, WHISKERS),
        P(26, 15, WHISKERS),
    ]


def _fox() -> list[Part]:
    tip = ['o...', 'oc..', 'occ.', 'occc']
    return [
        SHADOW,
        L(24, 12, ['..ccc..', '.ccccc.', '.bbbbbb', 'bbbbbbb', 'bbbbbb.', '.bbbbb.', '..bbbb.', '...bbb.', '...bb..']),
        *BODY(),
        H(5, 0, FOX_EAR),
        H(20, 0, mirror(FOX_EAR)),
        P(6, 2, tip),
        P(22, 2, mirror(tip)),
        ROUND_HEAD(),
        H(
            6,
            15,
            [
                'ccccc..........ccccc',
                'cccccccc....cccccccc',
                '.cccccccccccccccccc.',
                '..cccccccccccccccc..',
                '....cccccccccccc....',
            ],
            ol='outer',
        ),
        P(15, 14, ['oo']),
    ]


def _bear() -> list[Part]:
    return [
        SHADOW,
        *BODY(),
        H(5, 3, blob(6, 6, 'b', [2, 1], [1, 2])),
        H(21, 3, blob(6, 6, 'b', [2, 1], [1, 2])),
        P(7, 5, ['cc', 'cc']),
        P(23, 5, ['cc', 'cc']),
        ROUND_HEAD(),
        H(12, 14, blob(8, 6, 'c', [2, 0], [0, 2]), ol='none'),
        P(14, 14, ['oooo', '.oo.']),
    ]


def _panda() -> list[Part]:
    return [
        SHADOW,
        *BODY('a'),
        H(5, 3, blob(6, 6, 'a', [2, 1], [1, 2])),
        H(21, 3, blob(6, 6, 'a', [2, 1], [1, 2])),
        ROUND_HEAD(),
        P(9, 11, ['.aaa', 'aaaa', 'aaaa', '.aa.']),
        P(19, 11, ['aaa.', 'aaaa', 'aaaa', '.aa.']),
        P(14, 15, ['oooo', '.oo.']),
    ]


def _rabbit() -> list[Part]:
    return [
        SHADOW,
        *BODY(),
        H(8, -1, blob(5, 10, 'b', [1], [0])),
        H(19, -1, blob(5, 10, 'b', [1], [0])),
        P(10, 1, ['i'] * 6),
        P(21, 1, ['i'] * 6),
        ROUND_HEAD(),
        P(15, 15, ['ii']),
    ]


def _dog() -> list[Part]:
    return [
        SHADOW,
        L(23, 18, ['..b', '.bb', 'bb.', 'b..']),
        *BODY(),
        ROUND_HEAD(),
        P(18, 9, blob(6, 6, 'a', [1], [1])),  # eye patch
        H(3, 7, blob(5, 10, 'a', [1, 0], [0, 1, 2])),
        H(24, 7, blob(5, 10, 'a', [1, 0], [0, 1, 2])),
        H(12, 14, blob(8, 6, 'c', [2, 0], [0, 2]), ol='none'),
        P(14, 14, ['oooo', '.oo.']),
    ]


def _pig() -> list[Part]:
    return [
        SHADOW,
        *BODY(),
        ROUND_HEAD(),
        H(5, 3, PIG_EAR),
        H(21, 3, mirror(PIG_EAR)),
        H(12, 14, blob(8, 4, 'l', [1], [1]), shade=False),
        P(14, 15, ['d..d', 'd..d']),
    ]


def _mouse() -> list[Part]:
    return [
        SHADOW,
        L(23, 22, ['....b', '...b.', '..b..', 'bb...'], shade=False),
        *BODY(),
        H(2, 1, blob(9, 9, 'b', [3, 1, 0], [0, 1, 3])),
        H(21, 1, blob(9, 9, 'b', [3, 1, 0], [0, 1, 3])),
        P(4, 3, blob(5, 5, 'i', [1], [1])),
        P(23, 3, blob(5, 5, 'i', [1], [1])),
        ROUND_HEAD(),
        P(15, 15, ['ii']),
        P(3, 15, WHISKERS),
        P(26, 15, WHISKERS),
    ]


SPECIES: dict[str, dict[str, Any]] = {
    'giraffe': {
        'label': 'Giraffe',
        'color': '#f08aa0',
        'face': {
            'eyeL': (12, 6),
            'eyeR': (18, 6),
            'mouth': (16, 11),
            'cheekL': (10, 9),
            'cheekR': (20, 9),
            'fx': (27, 0),
            'top': (16, 5),
            'crown': (16, 3),
            'neck': (16, 19),
            'flower': (10, 3),
        },
        'small': ['crown', 'cap'],
        'parts': _giraffe,
    },
    'crocodile': {
        'label': 'Crocodile',
        'color': '#4fae5a',
        'face': {
            'eyeL': (10, 4),
            'eyeR': (20, 4),
            'mouth': (16, 15),
            'cheekL': (7, 12),
            'cheekR': (23, 12),
            'fx': (28, 0),
            'top': (16, 6),
            'neck': (16, 20),
            'flower': (5, 5),
            'crownBehind': (16, 4),
        },
        'small': ['cap', 'crownBehind'],
        'wideMouth': 3,
        'teeth': True,
        'reptileEyes': True,
        'parts': _crocodile,
    },
    'frog': {
        'label': 'Frog',
        'color': '#8fd14f',
        'wideMouth': 1,
        'face': {
            'eyeL': (10, 6),
            'eyeR': (20, 6),
            'mouth': (16, 15),
            'cheekL': (7, 13),
            'cheekR': (23, 13),
            'fx': (28, 0),
            'top': (16, 7),
            'neck': (16, 20),
            'flower': (5, 7),
            'crownBehind': (16, 4),
        },
        'small': ['cap', 'crownBehind'],
        'parts': _frog,
    },
    'cat': {'label': 'Cat', 'color': '#f2a65a', 'face': ROUND_FACE, 'parts': _cat},
    'fox': {'label': 'Fox', 'color': '#f07a35', 'face': ROUND_FACE, 'parts': _fox},
    'bear': {'label': 'Bear', 'color': '#b5794c', 'face': ROUND_FACE, 'parts': _bear, 'mouthDy': 1},
    'panda': {
        'label': 'Panda',
        'color': '#f1edf2',
        'accent': '#3a3340',
        'keepAccent': True,
        'face': ROUND_FACE,
        'parts': _panda,
        'limb': 'a',
        'eyeLight': True,  # the eyes sit on dark patches, so they are drawn light
        'mouthDy': 1,
    },
    'rabbit': {'label': 'Rabbit', 'color': '#ece4f2', 'face': ROUND_FACE, 'parts': _rabbit},
    'dog': {
        'label': 'Dog',
        'color': '#e0b07a',
        'accent': '#8a5a3a',
        'face': ROUND_FACE,
        'parts': _dog,
        'mouthDy': 1,
    },
    'pig': {
        'label': 'Pig',
        'color': '#f9a9bd',
        'face': {**ROUND_FACE, 'eyeL': (11, 11), 'eyeR': (19, 11), 'mouth': (16, 18)},
        'parts': _pig,
    },
    'mouse': {'label': 'Mouse', 'color': '#b3aec2', 'face': ROUND_FACE, 'parts': _mouse},
}

# ------------------------------------------------------------------ faces
# Eye stamps are drawn for the LEFT eye, from its top-left pixel. The right eye is the mirror image, unless
# `same` is set (a shine stays on the same side). `o` is the eye colour.
EYES: dict[str, dict[str, Any]] = {
    'open': {'rows': ['ow', 'oo', 'oo'], 'same': True},
    'small': {'rows': ['oo', 'oo'], 'dy': 1},
    'blink': {'rows': ['oo'], 'dy': 2},
    'happy': {'rows': ['.oo.', 'o..o'], 'dx': -1, 'dy': 1},
    'calm': {'rows': ['o..o', '.oo.'], 'dx': -1, 'dy': 1},
    'sleepy': {'rows': ['oooo', '.oo.'], 'dx': -1, 'dy': 1},
    'half': {'rows': ['oooo', '.oo.'], 'dx': -1, 'dy': 1},
    'wide': {'rows': ['.oo.', 'owwo', 'owoo', '.oo.'], 'dx': -1, 'dy': -1, 'same': True},
    'dot': {'rows': ['o'], 'dy': 1},
    'star': {'rows': ['..y..', '.yyy.', 'yyyyy', '.y.y.'], 'dx': -2, 'dy': -1},
    'heart': {'rows': ['hh.hh', 'hhhhh', '.hhh.', '..h..'], 'dx': -2, 'dy': -1},
    'cross': {'rows': ['o.o', '.o.', 'o.o'], 'dx': -1},
    'spiral': {'rows': ['ooo.', 'o..o', 'o.oo', 'o...'], 'dx': -1, 'dy': -1},
    'closed': {'rows': ['oooo'], 'dx': -1, 'dy': 1},
    'angry': {'rows': ['oo', 'oo'], 'dy': 1},
    'teary': {'rows': ['ow', 'oo', 'tt'], 'same': True},
    'look': {'rows': ['wo', 'oo', 'oo'], 'same': True},
}
# Crocodiles keep their eyes under a heavy lid whenever the eyes are open.
REPTILE: dict[str, dict[str, Any]] = {
    'open': {'rows': ['aaaa', '.ow.', '.oo.', '.oo.'], 'dx': -1, 'same': True},
    'look': {'rows': ['aaaa', '.wo.', '.oo.', '.oo.'], 'dx': -1, 'same': True},
    'teary': {'rows': ['aaaa', '.ow.', '.oo.', '.tt.'], 'dx': -1, 'same': True},
    'small': {'rows': ['aaaa', '....', '.oo.', '.oo.'], 'dx': -1},
    'blink': {'rows': ['aaaa', '....', '....', '.oo.'], 'dx': -1},
    'half': {'rows': ['aaaa', 'aaaa', '.oo.', '.oo.'], 'dx': -1},
    'angry': {'rows': ['aaaa', '..aa', '.oo.', '.oo.'], 'dx': -1},
}
# Brows sit above the eye. Drawn for the left eye; the right one is mirrored.
BROWS: dict[str, dict[str, Any]] = {
    'angry': {'rows': ['oo..', '..oo'], 'dx': -1, 'dy': -2},
    'sad': {'rows': ['..oo', 'oo..'], 'dx': -1, 'dy': -2},
    'raised': {'rows': ['.oo.'], 'dx': -1, 'dy': -3},
    'flat': {'rows': ['oooo'], 'dx': -1, 'dy': -1},
    'worried': {'rows': ['...o', '.oo.'], 'dx': -1, 'dy': -3},
}
# Mouths are centred on the face's middle line; all even widths.
MOUTHS: dict[str, list[str]] = {
    'smile': ['.o..o.', '..oo..'],
    'flat': ['..oo..'],
    'grin': ['.oooo.', '.orro.', '..oo..'],
    'laugh': ['oooooo', 'orrrro', '.oRRo.', '..oo..'],
    'laugh2': ['.oooo.', 'orrrro', '.oRRo.', '..oo..'],
    'open': ['..oo..', '.orro.', '..oo..'],
    'O': ['..oo..', '.orro.', '.orro.', '..oo..'],
    'frown': ['..oo..', '.o..o.'],
    'wavy': ['.o.o..', 'o.o.o.'],
    'tongue': ['.o..o.', '..oo..', '..RR..'],
    'smirk': ['....o.', '.ooo..'],
    'teeth': ['oooooo', 'owwwwo', 'oooooo'],
    'small_o': ['..oo..', '..oo..'],
    'kiss': ['..o...', '...o..', '..o...'],
}


def widen(rows: list[str], k: int) -> list[str]:
    """A wider mouth (frogs, crocodiles): repeat the two middle columns."""
    out = []
    for r in rows:
        m = len(r) // 2
        out.append(r[:m] + r[m - 1 : m + 1] * k + r[m:])
    return out


# Effects above the head; f is the frame number so they can move.
def _fx(name: str, f: int) -> dict[str, Any]:
    a = f % 4 < 2
    if name == 'alert':
        return {'rows': ['yy', 'yy', 'yy', '..', 'yy'], 'dy': 0 if a else -1}
    if name == 'question':
        return {'rows': ['.yy.', 'y..y', '..y.', '....', '..y.'], 'dy': 0 if a else -1}
    if name == 'zzz':
        if f % 8 < 4:
            return {'rows': ['...yyy', '.....y', '....y.', '...yyy', 'yy....', '.y....', 'yy....']}
        return {'rows': ['......', 'yyy...', '..y...', '.y....', 'yyy...', '......', '......']}
    if name == 'heart':
        return {'rows': ['hh.hh', 'hhhhh', '.hhh.', '..h..'] if a else ['.....', '.h.h.', '.hhh.', '..h..']}
    if name == 'dots':
        n = (f // 2) % 4
        middle = 'ww' + 'w'.join('o' if i < n else 'w' for i in range(3)) + 'ww'
        return {'rows': ['.wwwwwww.', middle, '.wwwwwww.', 'ww.......']}
    if name == 'anger':
        if a:
            return {'rows': ['.h.h.', 'hh.hh', '.....', 'hh.hh', '.h.h.']}
        return {'rows': ['.....', '.h.h.', 'hh.hh', '.h.h.', '.....']}
    if name == 'sparkle':
        return {'rows': ['..y..', '.yyy.', '..y..'] if a else ['y...y', '.....', 'y...y']}
    if name == 'note':
        return {'rows': ['.yyy', '.y.y', '.y.y', 'yy.y', 'yy..'], 'dy': 0 if a else -1}
    if name == 'sweat':
        return {'rows': ['.t.', 'ttt', 'tTt', '.t.'], 'at': 'side', 'dy': 0 if a else 1}
    if name == 'tears':
        return {'tears': True}
    if name == 'dizzy':
        if a:
            rows = ['.y...........', 'yyy..........', '.y.........y.', '...........yy', '...........y.']
        else:
            rows = ['...........y.', '..........yyy', '.y.........y.', 'yyy..........', '.y...........']
        return {'rows': rows, 'at': 'crown'}
    if name == 'bulb':
        rows = ['.yyy.', 'yyyyy', 'yyyyy', '.yyy.', '.ooo.']
        return {'rows': [r if a or i > 3 else r.replace('y', 'Y') for i, r in enumerate(rows)]}
    raise KeyError(name)


FX_NAMES = ('alert', 'question', 'zzz', 'heart', 'dots', 'anger', 'sparkle', 'note', 'sweat', 'tears', 'dizzy', 'bulb')


def _m(label: str, eyes: str, mouth: str | list[str], **more: Any) -> dict[str, Any]:
    return {'label': label, 'eyes': eyes, 'mouth': mouth, **more}


# The moods. A mouth list animates; `fx` floats a symbol over the head.
MOODS: dict[str, dict[str, Any]] = {
    'neutral': _m('Neutral', 'open', 'smile'),
    'happy': _m('Happy', 'happy', 'grin', blush=True),
    'laughing': _m('Laughing', 'happy', ['laugh', 'laugh2'], blush=True),
    'love': _m('In love', 'heart', 'smile', blush=True, fx='heart'),
    'excited': _m('Excited', 'star', 'grin', blush=True, fx='sparkle'),
    'wink': _m('Wink', 'open', 'tongue', eyesR='closed'),
    'proud': _m('Proud', 'calm', 'smirk', brows='raised'),
    'calm': _m('Calm', 'calm', 'smile', blush=True),
    'thinking': _m('Thinking', 'look', 'flat', brows='worried', fx='dots'),
    'idea': _m('Idea', 'wide', 'open', fx='bulb'),
    'focused': _m('Focused', 'half', 'flat', brows='flat'),
    'working': _m('Working', 'small', 'smile', prop='laptop'),
    'surprised': _m('Surprised', 'wide', 'O', fx='alert'),
    'confused': _m('Confused', 'open', 'wavy', eyesR='small', brows='worried', fx='question'),
    'worried': _m('Worried', 'open', 'wavy', brows='sad', fx='sweat'),
    'sad': _m('Sad', 'teary', 'frown', brows='sad'),
    'crying': _m('Crying', 'closed', ['open', 'O'], brows='sad', fx='tears'),
    'angry': _m('Angry', 'angry', 'teeth', brows='angry', fx='anger'),
    'grumpy': _m('Grumpy', 'half', 'frown', brows='flat'),
    'sleepy': _m('Sleepy', 'sleepy', 'small_o', fx='zzz'),
    'dizzy': _m('Dizzy', 'spiral', 'wavy', fx='dizzy'),
    'embarrassed': _m('Embarrassed', 'happy', 'wavy', blush=True, fx='sweat'),
    'singing': _m('Singing', 'calm', ['open', 'small_o'], blush=True, fx='note'),
    'shocked': _m('Shocked', 'dot', 'O', fx='alert'),
    'kiss': _m('Kiss', 'calm', 'kiss', blush=True, fx='heart'),
}

# Marcel's agent states map onto moods (B-28).
STATES = {'idle': 'neutral', 'thinking': 'thinking', 'working': 'working', 'needs_you': 'surprised', 'done': 'happy'}

# ------------------------------------------------------------------ the signature bow
# Every avatar wears a bow (Marcel's signature): a bow tie at the neck, like Marcel's (behind the laptop while working,
# as a real one would be); a hair bow on the head when a scarf already wraps the neck.

BOW = {'rows': ['ee..ee', 'eeEEee', 'ee..ee'], 'dx': -3}

# ------------------------------------------------------------------ accessories and personalities
_CAP = ['...vvvv...', '..vvvvvv..', '.vvvvvvvv.', 'vvvvvvvvvv', 'VVVVVVVVVVvv']
ACCESSORIES: dict[str, dict[str, Any]] = {
    'none': {'label': 'None'},
    'bowtie': {'label': 'Bow tie', 'at': 'neck', 'rows': ['gg..gg', 'ggGGgg', 'gg..gg'], 'dx': -3, 'dy': 0},
    'glasses': {'label': 'Glasses', 'glasses': True},
    'shades': {'label': 'Sunglasses', 'shades': True},
    'crown': {
        'label': 'Crown',
        'at': 'top',
        'rows': ['y..y..y', 'yy.y.yy', 'yyyyyyy', 'yhyyyhy', 'yyyyyyy'],
        'small': ['y.y.y', 'yyhyy', 'yyyyy'],
        'behind': ['y.yy.y', 'yyyyyy', 'yyhhyy', 'yyyyyy', 'yyyyyy'],
        'behindSmall': ['y.yy.y', 'yyyyyy', 'yyhhyy', 'yyyyyy'],
    },
    'flower': {'label': 'Flower', 'at': 'flower', 'rows': ['.hh.', 'hyyh', 'hyyh', '.hh.'], 'dx': -2, 'dy': -2},
    'scarf': {
        'label': 'Scarf',
        'at': 'neck',
        'rows': ['vvvvvvvvvv', 'VVVVVVVVVV', '......vv..', '......vv..'],
        'dx': -5,
        'dy': 0,
    },
    'cap': {
        'label': 'Cap',
        'at': 'top',
        'rows': [r.ljust(12, '.') for r in _CAP],
        'wide': 10,
        'small': ['..vvvv..', '.vvvvvv.', 'vvvvvvvv', 'VVVVVVVVvv'],
        'smallWide': 8,
    },
}


def _p(label: str, accessory: str, rest: str, bob: int, blink: int, **more: Any) -> dict[str, Any]:
    return {'label': label, 'accessory': accessory, 'rest': rest, 'bob': bob, 'blink': blink, **more}


PERSONALITIES: dict[str, dict[str, Any]] = {
    'professional': _p('Professional', 'bowtie', 'neutral', 1, 1),
    'cheerful': _p('Cheerful', 'flower', 'happy', 2, 1),
    'curious': _p('Curious', 'glasses', 'neutral', 1, 2),
    'cozy': _p('Cozy', 'scarf', 'calm', 1, 1),
    'bold': _p('Bold', 'crown', 'proud', 1, 1),
    'cool': _p('Cool', 'shades', 'proud', 1, 0),
    'sporty': _p('Sporty', 'cap', 'happy', 2, 1),
    'shy': _p('Shy', 'none', 'embarrassed', 1, 2, blush=True),
    'grumpy': _p('Grumpy', 'none', 'grumpy', 0, 1),
}

COLORS: dict[str, str] = {
    'rose': '#f08aa0',
    'coral': '#ff8a6e',
    'tangerine': '#f5953f',
    'honey': '#f2c14e',
    'lime': '#a8d65a',
    'green': '#5fb85a',
    'teal': '#3fb8a8',
    'sky': '#6cc3f0',
    'blue': '#5b8def',
    'lavender': '#ab93e8',
    'plum': '#b56aa8',
    'cocoa': '#a87650',
    'sand': '#e0c49a',
    'slate': '#8f97a8',
    'snow': '#eeeaf2',
    'ink': '#5a5a78',
}


# ------------------------------------------------------------------ name -> look (stable)
def name_hash(text: str) -> int:
    """FNV-1a over UTF-16 code units, then a final mix, exactly as zoo.js's `hash`."""
    h = 2166136261
    units = text.encode('utf-16-le', 'surrogatepass')  # JS strings may hold lone surrogates; so may JSON
    for i in range(0, len(units), 2):
        h ^= units[i] | (units[i + 1] << 8)
        h = imul(h, 16777619)
    h ^= h >> 16
    h = imul(h, 0x85EBCA6B)
    h ^= h >> 13
    h = imul(h, 0xC2B2AE35)
    h ^= h >> 16
    return h


_JS_SPACE = ' \t\n\v\f\r                 　﻿'


def from_name(name: str, fixed: dict[str, str] | None = None) -> dict[str, Any]:
    """Same name, same look. Anything in `fixed` wins over the pick."""
    fixed = fixed or {}
    h = name_hash(str(name or '').strip(_JS_SPACE).lower())
    species, colors, personalities = list(SPECIES), list(COLORS), list(PERSONALITIES)
    color = 'natural' if (h >> 7) % 3 == 0 else colors[(h >> 11) % len(colors)]
    return {
        'name': name,
        'species': fixed.get('species') or species[h % len(species)],
        'color': fixed.get('color') or color,
        'personality': fixed.get('personality') or personalities[(h >> 19) % len(personalities)],
        'mood': fixed.get('mood') or None,
    }


# ------------------------------------------------------------------ drawing
def _grid() -> Grid:
    return [[None] * N for _ in range(N)]


def _in(x: int, y: int) -> bool:
    return 0 <= x < N and 0 <= y < N


_SIDES = ((1, 0), (-1, 0), (0, 1), (0, -1))
_AROUND = (*_SIDES, (1, 1), (-1, -1), (1, -1), (-1, 1))


def draw_part(g: Grid, part: Part, dy: int) -> None:
    fill: dict[tuple[int, int], str] = {}
    px0, py0 = part['x'], part['y'] + dy
    for y, row in enumerate(part['rows']):
        for x, ch in enumerate(row):
            if ch != '.':
                fill[(px0 + x, py0 + y)] = ch
    ol = part['ol']
    if ol != 'none':
        outer = ol == 'outer'
        for x, y in fill:
            for ax, ay in _SIDES:
                nx, ny = x + ax, y + ay
                if not (0 <= nx < N and 0 <= ny < N) or (nx, ny) in fill:
                    continue
                if outer and g[ny][nx] is not None and g[ny][nx] != 'S':
                    continue
                g[ny][nx] = 'o'
    xs = [p[0] for p in fill]
    ys = [p[1] for p in fill]
    shade = part['shade'] and max(xs) - min(xs) >= 4 and max(ys) - min(ys) >= 3
    light = part.get('light', True) is not False
    for (x, y), ch in fill.items():
        if not (0 <= x < N and 0 <= y < N):
            continue
        c = ch
        if shade and ch in SHADE:
            if (x, y + 1) not in fill or (x + 1, y) not in fill:
                c = SHADE[ch]  # one shade, bottom and right
            elif ch == 'b' and light and ((x, y - 1) not in fill or (x - 1, y) not in fill) and (x + 1, y + 1) in fill:
                c = 'l'  # rim light, top-left
        g[y][x] = c


def stamp(g: Grid, rows: list[str], x0: int, y0: int, mapper: Callable[[str], str] | None = None) -> None:
    for y, row in enumerate(rows):
        for x, ch in enumerate(row):
            if ch == '.' or not _in(x0 + x, y0 + y):
                continue
            g[y0 + y][x0 + x] = mapper(ch) if mapper else ch


def float_stamp(g: Grid, rows: list[str], x0: int, y0: int) -> None:
    """A free-floating stamp (symbols over the head) gets its own outline so it reads on any background."""
    on = {(x0 + x, y0 + y) for y, row in enumerate(rows) for x, ch in enumerate(row) if ch != '.'}
    for x, y in on:
        for ax, ay in _AROUND:
            nx, ny = x + ax, y + ay
            if _in(nx, ny) and (nx, ny) not in on and g[ny][nx] in (None, 'S'):
                g[ny][nx] = 'o'
    stamp(g, rows, x0, y0)


def place_eye(
    g: Grid, d: dict[str, Any] | None, ex: int, ey: int, right: bool, mapper: Callable[[str], str] | None
) -> None:
    if not d:
        return
    w = max(len(r) for r in d['rows'])
    rows = [r.ljust(w, '.') for r in d['rows']]
    dx = d.get('dx', 0)
    x = ex + dx
    if right and not d.get('same'):
        rows = mirror(rows)
        x = ex + 2 - w - dx
    stamp(g, rows, x, ey + d.get('dy', 0), mapper)


def draw_laptop(g: Grid, sp: dict[str, Any], f: int, dy: int, sessions: int) -> None:
    """The laptop on the lap, lid towards us with a heart on it; paws type over the top edge.
    `sessions` (1-3) lights that many small lamps on the lid, one per running Claude Code session."""
    limb = sp.get('limb', 'b')
    draw_part(g, L(8, 27, blob(16, 2, 'Q')), dy)  # keyboard base
    draw_part(g, L(9, 21, blob(14, 7, 'q', [0], [0])), dy)  # lid
    stamp(g, ['h..h', 'hhhh', '.hh.'], 14, 23 + dy)
    for i in range(3):
        stamp(g, ['x' if i < sessions and (f + i) % 4 != 0 else 'Q'], 19 - i * 2, 26 + dy)
    typing = (f // 2) % 2  # paws take turns
    draw_part(g, L(10, 19 + (0 if typing else 1), blob(3, 2, limb)), dy)
    draw_part(g, L(19, 19 + (1 if typing else 0), blob(3, 2, limb)), dy)


def _sessions(value: Any) -> int:
    """1-3 lamps; like JS, "2" counts as 2. Anything that is not a number counts as 1."""
    try:
        n = int(value or 1)
    except (TypeError, ValueError):
        n = 1
    return max(1, min(3, n))


def frame(look: dict[str, Any], mood: str | None = None, f: int = 0) -> dict[str, Any]:
    """Frame f of an avatar: {"grid": 32 rows of palette letters (None = empty), "palette": letter -> colour}.

    `look` keys: species, color (a name from COLORS, "natural" or "#rrggbb"), personality, and optionally
    accessory and sessions (1-3, lights on the laptop while working). `mood` (or look["mood"]) picks the face;
    without one the personality's resting face shows.
    """
    if mood:
        look = {**look, 'mood': mood}
    sp = SPECIES.get(look.get('species') or '') or SPECIES['giraffe']
    per = PERSONALITIES.get(look.get('personality') or '') or PERSONALITIES['professional']
    face_mood = MOODS.get(look.get('mood') or per['rest']) or MOODS['neutral']
    color = look.get('color')
    natural = not color or color == 'natural'
    base = sp['color'] if natural else (COLORS.get(color) or color)
    pal = palette(base, sp.get('accent') if natural or sp.get('keepAccent') else None)
    face = sp['face']
    g = _grid()

    # breathing: the head sinks a pixel for half the loop; lively personalities hop
    c8 = f % 8
    hop = -1 if per['bob'] == 2 and c8 in (2, 3) else 0
    hy = (0 if per['bob'] == 0 else (1 if c8 >= 4 else 0)) + hop

    def dy_of(group: str) -> int:
        return hy if group == 'head' else 0 if group == 'shadow' else hop

    acc_key = look.get('accessory') or per['accessory']
    acc = ACCESSORIES.get(acc_key) or ACCESSORIES['none']
    # frogs and crocodiles have their eyes on top of the head: the crown sits behind the eye bumps
    crown_behind = acc is ACCESSORIES['crown'] and face.get('crownBehind')
    if crown_behind:
        tx, ty = face['crownBehind']
        rows = acc['behindSmall'] if 'crownBehind' in sp.get('small', []) else acc['behind']
        draw_part(g, H(tx - len(rows[0]) // 2, ty - len(rows) + 1, rows), hy)
    for p in sp['parts']():
        draw_part(g, p, dy_of(p['group']))

    if acc.get('at') == 'neck' and acc is not ACCESSORIES['bowtie']:  # the bow tie is the signature bow, below
        nx, ny = face['neck']
        draw_part(g, L(nx + acc['dx'], ny + acc['dy'], acc['rows']), hop if look.get('species') == 'giraffe' else hy)

    # the signature bow: at the neck (the laptop, drawn next, sits in front of it), or on the head when a scarf has
    # the neck
    if acc.get('at') == 'neck' and acc is not ACCESSORIES['bowtie']:
        bx, by = face['flower']
        draw_part(g, H(bx - len(BOW['rows'][0]) // 2, by - 1, BOW['rows']), hy)
    else:
        nx, ny = face['neck']
        draw_part(g, L(nx + BOW['dx'], ny, BOW['rows']), hop if look.get('species') == 'giraffe' else hy)

    if face_mood.get('prop') == 'laptop':
        draw_laptop(g, sp, f, hop, _sessions(look.get('sessions')))

    # ---- face
    eye_map: Callable[[str], str] | None = None
    if sp.get('eyeLight'):

        def eye_map(ch: str) -> str:
            return 'w' if ch == 'o' else 'o' if ch == 'w' else ch

    blinks = per['blink'] and face_mood['eyes'] in ('open', 'teary', 'look', 'small')
    blinking = blinks and (f % 16 == 13 or (per['blink'] == 2 and f % 16 == 6))
    eyes_l = 'blink' if blinking else face_mood['eyes']
    eyes_r = 'blink' if blinking else (face_mood.get('eyesR') or face_mood['eyes'])
    lx, ly = face['eyeL']
    rx, ry = face['eyeR']
    if face_mood.get('blush') or per.get('blush'):
        stamp(g, ['pp'], face['cheekL'][0], face['cheekL'][1] + hy)
        stamp(g, ['pp'], face['cheekR'][0], face['cheekR'][1] + hy)
    shift = 1 if face_mood['eyes'] == 'look' else 0

    def eye_def(k: str) -> dict[str, Any] | None:
        return (sp.get('reptileEyes') and REPTILE.get(k)) or EYES.get(k)

    place_eye(g, eye_def(eyes_l), lx + shift, ly + hy, False, eye_map)
    place_eye(g, eye_def(eyes_r), rx + shift, ry + hy, True, eye_map)
    if face_mood.get('brows'):
        b = BROWS[face_mood['brows']]
        w = max(len(r) for r in b['rows'])
        stamp(g, b['rows'], lx + b['dx'], ly + b['dy'] + hy, eye_map)
        stamp(g, mirror([r.ljust(w, '.') for r in b['rows']]), rx + 2 - w - b['dx'], ry + b['dy'] + hy, eye_map)
    mouth_key = (
        face_mood['mouth'][(f // 2) % len(face_mood['mouth'])]
        if isinstance(face_mood['mouth'], list)
        else face_mood['mouth']
    )
    mouth = widen(MOUTHS[mouth_key], sp['wideMouth']) if sp.get('wideMouth') else MOUTHS[mouth_key]
    mw = len(mouth[0])
    mx0 = face['mouth'][0] - mw // 2
    my0 = face['mouth'][1] + hy + sp.get('mouthDy', 0)
    stamp(g, mouth, mx0, my0)
    if sp.get('teeth'):
        # a crocodile grin: a tooth under every other pixel of the top lip, wherever there is skin below it
        for x in range(0, mw, 2):
            y = next((i for i, r in enumerate(mouth) if r[x] == 'o'), -1)
            px, py = mx0 + x, my0 + y + 1
            if y >= 0 and py < N and (g[py][px] or '-') in 'bdlr':
                g[py][px] = 'w'

    # ---- accessories on the head
    if acc.get('glasses'):
        for ex in (lx, rx):
            stamp(g, ['oooo', 'o..o', 'o..o', 'oooo'], ex - 1, ly - 1 + hy)
        stamp(g, ['o' * (rx - lx - 3)], lx + 3, ly + hy)
    elif acc.get('shades'):
        for ex in (lx, rx):
            stamp(g, ['kkkk', 'kkkk', '.kk.'], ex - 1, ly + hy)
        stamp(g, ['k' * (rx - lx - 3)], lx + 3, ly + hy)
        stamp(g, ['w'], lx, ly + hy)
        stamp(g, ['w'], rx, ly + hy)
    elif acc.get('at') == 'top' and not crown_behind:
        # centred on the head, its bottom row resting on the head's top edge
        small = acc_key in sp.get('small', []) and acc.get('small')
        rows = acc['small'] if small else acc['rows']
        w = (acc.get('smallWide') if small else acc.get('wide')) or len(rows[0])
        tx, ty = (acc is ACCESSORIES['crown'] and face.get('crown')) or face['top']
        draw_part(g, H(tx - w // 2, ty - len(rows) + 1, rows), hy)
        for p in sp['parts']():
            if p.get('front'):
                draw_part(g, p, dy_of(p['group']))  # giraffe horns poke through
    elif acc.get('at') == 'flower':
        tx, ty = face['flower']
        draw_part(g, H(tx + acc['dx'], ty + acc['dy'], acc['rows']), hy)

    # ---- effects
    if face_mood.get('fx'):
        fx = _fx(face_mood['fx'], f)
        if fx.get('tears'):
            drop = f % 4
            for ex in (lx, rx + 1):
                stamp(g, ['t', 't', 'T'][: 1 + min(drop, 2)], ex, ly + 2 + hy + drop)
        elif fx.get('at') == 'side':
            float_stamp(g, fx['rows'], face['cheekR'][0] + 2, face['eyeR'][1] - 5 + hy + fx.get('dy', 0))
        elif fx.get('at') == 'crown':
            float_stamp(g, fx['rows'], face['top'][0] - 6, face['top'][1] - 2 + hy)
        else:
            w = max(len(r) for r in fx['rows'])
            float_stamp(g, fx['rows'], min(N - 1 - w, face['fx'][0] - w // 2), face['fx'][1] + 1 + fx.get('dy', 0))
    return {'grid': g, 'palette': pal}


def loop(look: dict[str, Any]) -> list[dict[str, Any]]:
    """The avatar's loop: 16 frames at 140 ms."""
    return [{**frame(look, f=f), 'ms': 140} for f in range(16)]


_RGBA = re.compile(r'[\d.]+')


def _rgba(c: str) -> tuple[int, int, int, int]:
    if c.startswith('rgba'):
        p = [float(v) for v in _RGBA.findall(c)]
        return int(p[0]), int(p[1]), int(p[2]), js_round(p[3] * 255)
    return int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16), 255


def to_rgba(fr: dict[str, Any], scale: int = 1, bg: str | None = None) -> bytes:
    """Paint a frame into RGBA bytes (row by row), `scale` screen pixels per avatar pixel."""
    bgc = _rgba(bg) if bg else (0, 0, 0, 0)

    def pixel(k: str | None) -> bytes:
        c = _rgba(fr['palette'].get(k, '#ff00ff')) if k else bgc
        if bg and c[3] < 255:
            a = c[3] / 255
            c = (*(js_round(bgc[i] * (1 - a) + c[i] * a) for i in range(3)), 255)
        return bytes(c) * scale

    colors: dict[str | None, bytes] = {}
    lines = []
    for row in fr['grid']:
        line = b''.join(colors[k] if k in colors else colors.setdefault(k, pixel(k)) for k in row)
        lines.append(line * scale)
    return b''.join(lines)


def rgba_sha256(fr: dict[str, Any]) -> str:
    return hashlib.sha256(to_rgba(fr)).hexdigest()
