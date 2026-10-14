# /// script
# requires-python = ">=3.12"
# dependencies = ["pillow==12.3.0"]
# ///
"""Draws Marcel's app icon: a puffy bow that tiles the plane, on rose; the centre bow white with a
teal knot (design 12, the owner's pick, ADR-008).

The bow is one closed shape of four cubic Beziers (two arcs above, two below, straight sides) that
tiles: each row is shifted half a bow, so a bow's waist fits its neighbour's wings. The tiling is
scaled 1.6x and its rows tilted 14 degrees, three rose shades with a darker seam; the centre bow is
white with a teal knot, the bow every Pixel Zoo avatar will wear. Every curve is sampled at 4x and
scaled down, so the edges stay smooth with Pillow alone. Writes the 1024 px icons (default and dark)
to out/, every size the iOS app lists to ios/Marcel/App/AppIcon/, and out/checksums.txt, which
AppIconTests reads.

The source of truth is the approved SVG, kept next to this script (design-12-tiling-x1.6.svg); this
script draws the same thing.

    uv run shared/brand/app-icon/generate.py           # redraw everything
    uv run shared/brand/app-icon/generate.py --check   # fail if a committed file is stale
"""

import hashlib
import io
import math
import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

HERE = Path(__file__).resolve().parent
OUT = HERE / 'out'
REPO = HERE.parents[2]
IOS_ICONS = REPO / 'ios' / 'Marcel' / 'App' / 'AppIcon'
S = 1024
SS = 4  # drawn at 4x, then scaled down

# The bow, in its own units (270 x 200): start point, then cubic segments (c1, c2, end) or a line.
BOW_START = (0.0, 0.0)
BOW_SEGMENTS = [
    ((57.16, 3.73), (77.84, 72.27), (135.0, 76.0)),
    ((192.16, 72.27), (212.84, 3.73), (270.0, 0.0)),
    ((270.0, 200.0),),
    ((212.84, 196.27), (192.16, 127.73), (135.0, 124.0)),
    ((77.84, 127.73), (57.16, 196.27), (0.0, 200.0)),
]
BOW_W, BOW_H = 270.0, 200.0
STEP_X, STEP_Y = 270.0, 124.0  # the next bow in a row; the next row, shifted half a bow
KNOT_X, KNOT_W = 116.0, 38.0  # the knot: a strip across the waist, inside the bow
SCALE = 1.5 * 1.6  # px per bow unit at 1024 px: design 12 at 1.6x
TILT = 14.0  # degrees, clockwise
SEAM = 14.0  # px at 1024

THEMES = {
    # PixelKit rose, three shades of it for the tiles; the vector avatar's bow teal for the knot.
    'default': dict(
        bg='#d87990',
        tiles=('#e693a6', '#d87990', '#df869c'),
        seam='#b9566f',
        bow='#ffffff',
        knot='#3f6b6a',
    ),
    # The giraffe's plum, three shades of it; the bow in the app's dark-mode rose, a lighter teal.
    'dark': dict(
        bg='#3f1838',
        tiles=('#4d1f45', '#3f1838', '#461b3e'),
        seam='#2a0f26',
        bow='#f08aa0',
        knot='#5f9a98',
    ),
}


# Every file the app lists (Config/Marcel-Info.plist), named the way iOS looks them up.
IOS_FILES = {f'AppIcon{p}x{p}@{s}x.png': p * s for p in (20, 29, 40, 60) for s in (2, 3)} | {
    'AppIcon-1024.png': 1024
}


def bow_outline(samples=48):
    """The bow's outline in bow units, each Bezier sampled `samples` times."""
    pts = [BOW_START]
    for seg in BOW_SEGMENTS:
        p0 = pts[-1]
        if len(seg) == 1:
            pts.append(seg[0])
            continue
        c1, c2, p3 = seg
        for n in range(1, samples + 1):
            t = n / samples
            u = 1 - t
            pts.append(
                tuple(
                    u**3 * p0[a] + 3 * u * u * t * c1[a] + 3 * u * t * t * c2[a] + t**3 * p3[a]
                    for a in (0, 1)
                )
            )
    return pts  # ends at the bottom-left corner; the left side closes back to the start


def placer(ss):
    """Bow units -> canvas pixels: centred bow at the icon's centre, scaled, tilted clockwise."""
    a = math.radians(TILT)
    ca, sa = math.cos(a), math.sin(a)
    k = SCALE * ss
    c = S * ss / 2

    def place(x, y, dx=0.0, dy=0.0):
        u, v = (x + dx - BOW_W / 2) * k, (y + dy - BOW_H / 2) * k
        return (c + u * ca - v * sa, c + u * sa + v * ca)

    return place


def tiles():
    """Offsets of every bow that can reach the icon, in paint order (the SVG's), and its shade."""
    n = int(9 / 1.6) + 3
    for j in range(-n, n + 1):
        for i in range(-n, n + 1):
            if (i, j) != (0, 0):
                yield i * STEP_X + j * STEP_X / 2, -j * STEP_Y, (i - j) % 3


def draw_bow(d, place, outline, fill, seam, width, dx=0.0, dy=0.0):
    """A bow's fill, then its seam on top (an SVG stroke: centred on the edge, round joins)."""
    pts = [place(x, y, dx, dy) for x, y in outline]
    d.polygon(pts, fill=fill)
    d.line([*pts, pts[0], pts[1]], fill=seam, width=width, joint='curve')


def render(theme_name):
    theme = THEMES[theme_name]
    big = S * SS
    img = Image.new('RGB', (big, big), theme['bg'])
    d = ImageDraw.Draw(img)
    place, outline = placer(SS), bow_outline()
    width = round(SEAM * SS)
    for dx, dy, shade in tiles():
        draw_bow(d, place, outline, theme['tiles'][shade], theme['seam'], width, dx, dy)
    draw_bow(d, place, outline, theme['bow'], theme['seam'], width)
    # The knot: a strip across the waist, clipped to the bow's inside (it covers the seam there).
    inside = Image.new('L', (big, big), 0)
    ImageDraw.Draw(inside).polygon([place(x, y) for x, y in outline], fill=255)
    strip = Image.new('L', (big, big), 0)
    corners = [(KNOT_X, 0), (KNOT_X + KNOT_W, 0), (KNOT_X + KNOT_W, BOW_H), (KNOT_X, BOW_H)]
    ImageDraw.Draw(strip).polygon([place(x, y) for x, y in corners], fill=255)
    img.paste(theme['knot'], (0, 0), ImageChops.multiply(inside, strip))
    return img.resize((S, S), Image.Resampling.LANCZOS)


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
