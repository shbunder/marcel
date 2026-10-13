# /// script
# requires-python = ">=3.12"
# dependencies = ["pillow==12.3.0"]
# ///
"""Draws Marcel's app icon: a white pixel-art M on rose, wearing a soft teal bow tie (design 5b).

The M is the app's own lettering: Silkscreen's M (5 x 5 font pixels, the face PixelKit uses
for labels and headings), each font pixel a 120-px square, centred. The bow is a smooth vector shape
drawn at 4x and scaled down, on purpose: the two styles are mixed. Writes the 1024 px icons
(default and dark) to out/, every size the iOS app lists to ios/Marcel/App/AppIcon/, and
out/checksums.txt, which AppIconTests reads.

    uv run shared/brand/app-icon/generate.py           # redraw everything
    uv run shared/brand/app-icon/generate.py --check   # fail if a committed file is stale
"""

import hashlib
import io
import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
OUT = HERE / 'out'
REPO = HERE.parents[2]
IOS_ICONS = REPO / 'ios' / 'Marcel' / 'App' / 'AppIcon'
S = 1024

# Palette: PixelKit rose, the pixel giraffe's plum and pinks, the vector avatar's bow teal.
THEMES = {
    'default': dict(bg='#d87990', m='#ffffff', bow='#3f6b6a', fold='#2a4b4a', shine='#5a8a88'),
    'dark': dict(bg='#3f1838', m='#f08aa0', bow='#5f9a98', fold='#3f6b6a', shine='#7fb5b2'),
}

# The M, cell by cell, as Silkscreen draws it at its native 8-px size (ios/PixelKit/.../Fonts/
# Silkscreen-Regular.ttf, SIL Open Font License 1.1, Silkscreen-OFL.txt next to it). Redrawn here
# rather than rasterised at run time, so the icon does not depend on a font renderer.
M_ROWS = [
    'X...X',
    'XX.XX',
    'X.X.X',
    'X...X',
    'X...X',
]
CELL = 120
W, H = len(M_ROWS[0]), len(M_ROWS)

# The bow (design 5b): puffy wings, 15 % larger than the first draft, tilted 10 degrees,
# low on the left leg.
# BOW_ROW: the bow's centre, in M cells from the top of the letter.
BOW_LENGTH, BOW_HEIGHT, BOW_TILT, BOW_ROW = 120 * 1.15, 150 * 1.15, -10, 3.4
PUFFY = [
    (0.10, -0.10),
    (0.30, -0.42),
    (0.62, -0.58),
    (0.92, -0.48),
    (1.05, -0.10),
    (1.00, 0.30),
    (0.78, 0.52),
    (0.45, 0.48),
    (0.18, 0.20),
]
# The left wing is the right one mirrored with small wobbles, so it looks drawn by hand.
WOBBLE_LEFT = [
    (0, 0),
    (0.02, -0.03),
    (-0.03, 0.02),
    (0.02, 0.03),
    (-0.04, 0.0),
    (0.0, 0.04),
    (0.03, -0.02),
    (0, 0.02),
    (0, 0),
]
SHINE = [(0.30, -0.30), (0.55, -0.42), (0.78, -0.36), (0.62, -0.24), (0.40, -0.18)]
KNOT = [
    (-0.15, -0.20),
    (0.0, -0.25),
    (0.15, -0.19),
    (0.18, 0.02),
    (0.14, 0.21),
    (0.0, 0.25),
    (-0.15, 0.20),
    (-0.18, 0.0),
]

# Every file the app lists (Config/Marcel-Info.plist), named the way iOS looks them up.
IOS_FILES = {f'AppIcon{p}x{p}@{s}x.png': p * s for p in (20, 29, 40, 60) for s in (2, 3)} | {
    'AppIcon-1024.png': 1024
}


def m_cells():
    return {(x, y) for y, row in enumerate(M_ROWS) for x, ch in enumerate(row) if ch == 'X'}


def spline(pts, n, closed=True):
    """Catmull-Rom spline through pts."""
    pts = pts if closed else [pts[0], *pts, pts[-1]]
    k = len(pts)
    spans = range(k) if closed else range(1, k - 2)
    out = []
    for i in spans:
        p0, p1, p2, p3 = pts[i - 1], pts[i], pts[(i + 1) % k], pts[(i + 2) % k]
        for j in range(n + (0 if closed else 1)):
            t = j / n
            out.append(
                tuple(
                    0.5
                    * (
                        2 * p1[a]
                        + (p2[a] - p0[a]) * t
                        + (2 * p0[a] - 5 * p1[a] + 4 * p2[a] - p3[a]) * t * t
                        + (3 * p1[a] - p0[a] - 3 * p2[a] + p3[a]) * t**3
                    )
                    for a in (0, 1)
                )
            )
    return out


def draw_bow(d, ss, cx, cy, theme):
    a = math.radians(BOW_TILT)
    ca, sa = math.cos(a), math.sin(a)
    L, Hb = BOW_LENGTH, BOW_HEIGHT

    def p(x, y):
        return ((cx + x * ca - y * sa) * ss, (cy + x * sa + y * ca) * ss)

    for sign, wobble in ((-1, WOBBLE_LEFT), (1, [(0, 0)] * len(PUFFY))):
        wing = [
            ((px + wx) * L * sign, (py + wy) * Hb)
            for (px, py), (wx, wy) in zip(PUFFY, wobble, strict=True)
        ]
        d.polygon([p(x, y) for x, y in spline(wing, 24)], fill=theme['bow'])
        d.polygon([p(x * L * sign, y * Hb) for x, y in spline(SHINE, 10)], fill=theme['shine'])
        for fy in (-0.16, 0.14):  # the pinch: two fold lines from the knot into the wing
            fold = spline([(0.16, fy * 0.4), (0.36, fy * 1.1), (0.55, fy * 1.6)], 12, closed=False)
            d.line(
                [p(x * L * sign, y * Hb) for x, y in fold],
                fill=theme['fold'],
                width=int(0.045 * Hb * ss),
                joint='curve',
            )
    d.polygon([p(x * L, y * Hb) for x, y in spline(KNOT, 12)], fill=theme['fold'])


def render(theme_name):
    theme = THEMES[theme_name]
    img = Image.new('RGB', (S, S), theme['bg'])
    ox, oy = (S - W * CELL) // 2, (S - H * CELL) // 2  # the M exactly centred; the bow may overhang
    d = ImageDraw.Draw(img)
    for x, y in m_cells():
        d.rectangle(
            [ox + x * CELL, oy + y * CELL, ox + (x + 1) * CELL - 1, oy + (y + 1) * CELL - 1],
            fill=theme['m'],
        )
    ss = 4
    layer = Image.new('RGBA', (S * ss, S * ss), (0, 0, 0, 0))
    draw_bow(ImageDraw.Draw(layer), ss, ox + CELL / 2, oy + BOW_ROW * CELL, theme)
    layer = layer.resize((S, S), Image.Resampling.LANCZOS)
    img.paste(layer, (0, 0), layer)
    return img


def png(img):
    buf = io.BytesIO()
    img.save(buf, format='PNG', optimize=True)
    return buf.getvalue()


def files():
    """Every file this script owns, as {path: bytes}."""
    light, dark = render('default'), render('dark')
    out = {OUT / 'app-icon-1024.png': png(light), OUT / 'app-icon-dark-1024.png': png(dark)}
    for name, side in IOS_FILES.items():
        img = light if side == S else light.resize((side, side), Image.Resampling.LANCZOS)
        out[IOS_ICONS / name] = png(img)
    sums = ''.join(
        f'{hashlib.sha256(data).hexdigest()}  {path.relative_to(REPO)}\n'
        for path, data in sorted(out.items())
    )
    out[OUT / 'checksums.txt'] = sums.encode()
    return out


def main(argv):
    wanted = files()
    if '--check' in argv:
        stale = [
            str(p.relative_to(REPO))
            for p, data in wanted.items()
            if not p.exists() or p.read_bytes() != data
        ]
        if stale:
            print(
                'The app icon is out of date. Run `uv run shared/brand/app-icon/generate.py`'
                ' and commit:'
            )
            print('\n'.join(f'  {s}' for s in stale))
            return 1
        print(f'The app icon files match the generator ({len(wanted)} files).')
        return 0
    OUT.mkdir(exist_ok=True)
    for path, data in wanted.items():
        path.write_bytes(data)
    print(f'Wrote {len(wanted)} files.')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
