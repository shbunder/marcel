"""Builds the pixel-art giraffe (Marcel) from shape layers.

Pipeline, the way a pixel artist works, but repeatable:
  1. each body part is a mask on a 48x54 grid, tagged with a material (body pink, cream, spot, plum, ...);
  2. each part is shaded on its own with a hue-shifted ramp (light from the top left);
  3. parts are stacked back to front, with a dark line where a part overlaps another;
  4. props (thought dots, the "!", hearts, the done sparkle) are added as small flat shapes;
  5. a plum outline goes round the whole silhouette, props included (a lighter one on the lit top-left side);
  6. face details (eyes, shine, lashes, blush, mouth) go on last, unshaded.
Frames are built by moving whole parts by whole pixels (breathing, blinking, ear flicks, tail swish, a nod).

Run:  python3 build.py [out_dir]   -> giraffe.png + giraffe.json (contracts/avatar-sheet.schema.json),
                                      one preview GIF per loop and preview-states.png, in out/ by default.
No third-party packages: PNGs are written with zlib, GIFs with a small LZW encoder.
"""

import json
import math
import os
import struct
import sys
import zlib

W, H = 48, 54
OY = 3  # everything sits 3 px lower, leaving room above for bobbing ears and horns
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'out')
COLUMNS = 8  # frames per row in the sprite sheet

# ---------------------------------------------------------------- palette
# Ramps go deepest shadow -> shadow -> base -> light -> highlight, hue-shifted:
# shadows lean purple, highlights lean peach (never just "add black / add white").
RAMPS = {
    'B': ['#9b3f6b', '#c9577e', '#f08aa0', '#ffb2b6', '#ffdccf'],  # body pink (brand rose in the shadow)
    'C': ['#e0a3ab', '#f3c3c1', '#fde3da', '#fff3ea', '#ffffff'],  # cream muzzle and belly
    'S': ['#7a2850', '#9a3462', '#bd4873', '#d76589', '#e98aa2'],  # spots
    'K': ['#3f1838', '#5c2347', '#7d3258', '#a04b74', '#c46b90'],  # plum: horn knobs, hooves, tail tuft
    'I': ['#d9587c', '#ea6f8e', '#ff9fb3', '#ffc2cc', '#ffe0e4'],  # inside of the ears
    'G': ['#c9631f', '#e8862a', '#f6aa1c', '#ffcf5c', '#fff0b0'],  # glow (working)
    'T': ['#2c4545', '#3d5f5e', '#5e807f', '#86a9a6', '#bcdad5'],  # teal (laptop)
}
FIXED = {
    'o': '#4a1a3a',  # outline, shadow side
    'O': '#7d2f55',  # outline, lit side
    'e': '#2a1328',  # eye
    'w': '#ffffff',  # eye shine
    'b': '#f8698a',  # blush
    'r': '#8a2f52',  # mouth
    'n': '#b8456c',  # nostril
    'x': '#c8f3ea',  # laptop screen glow
    'X': '#ffffff',
    'h': '#f8698a',  # heart on the laptop
    'g': '#ffcf5c',  # sparkle arms
}
BACKDROP = '#faf5f8'
SHADOW = '#eedde5'  # soft ground shadow, tuned for the Snow background
HALO = '#ffc98a'  # warm light round a glowing spot


def hex_rgb(h):
    h = h.lstrip('#')
    return tuple(int(h[i : i + 2], 16) for i in (0, 2, 4))


# ---------------------------------------------------------------- masks
def ellipse(cx, cy, rx, ry, angle=0.0):
    ca, sa = math.cos(math.radians(angle)), math.sin(math.radians(angle))
    pts = set()
    for y in range(H):
        for x in range(W):
            dx, dy = x + 0.5 - cx, y + 0.5 - cy
            u = dx * ca + dy * sa
            v = -dx * sa + dy * ca
            if (u / rx) ** 2 + (v / ry) ** 2 <= 1.0:
                pts.add((x, y))
    return pts


def rect(x0, y0, x1, y1):
    return {(x, y) for y in range(y0, y1 + 1) for x in range(x0, x1 + 1)}


def thick_path(points, r):
    """A soft tube along a polyline (for the tail)."""
    pts = set()
    for (ax, ay), (bx, by) in zip(points, points[1:], strict=False):
        steps = int(max(abs(bx - ax), abs(by - ay)) * 3) + 1
        for i in range(steps + 1):
            t = i / steps
            pts |= ellipse(ax + (bx - ax) * t, ay + (by - ay) * t, r, r)
    return pts


def shift(mask, dx, dy):
    return {(x + dx, y + dy) for (x, y) in mask}


# ---------------------------------------------------------------- shading
LIGHT = (-0.6, -0.8)  # light comes from the top left


def shade_region(region):
    """Return {pixel: ramp index} for one part, like hand cel-shading a round form."""
    if not region:
        return {}
    xs = [p[0] for p in region]
    ys = [p[1] for p in region]
    rx = (max(xs) - min(xs) + 1) / 2
    ry = (max(ys) - min(ys) + 1) / 2
    size = min(rx, ry)
    if size < 2.6:
        # small parts (spots, knobs, hooves) stay flat: base colour, one darker pixel row on the bottom edge
        flat = {}
        for x, y in region:
            flat[(x, y)] = 1 if ((x, y + 1) not in region and len(region) > 6) else 2
        return flat
    k_shadow = max(1, round(size * 0.38))
    k_deep = 1 if size >= 4.5 else 0
    k_light = max(1, round(size * 0.22)) if size >= 2 else 0
    away = (-LIGHT[0] / 0.8, -LIGHT[1] / 0.8)  # towards the bottom right
    toward = (LIGHT[0] / 0.8, LIGHT[1] / 0.8)  # towards the top left
    out = {}
    for x, y in region:

        def leaves(direction, k, x=x, y=y):
            for t in range(1, k + 1):
                q = (round(x + direction[0] * t), round(y + direction[1] * t))
                if q not in region:
                    return True
            return False

        idx = 2
        if k_deep and leaves(away, k_deep) and leaves((0, 1), 1):
            idx = 0
        elif leaves(away, k_shadow):
            idx = 1
        elif k_light and leaves(toward, k_light):
            idx = 3
        out[(x, y)] = idx
    # a small specular highlight inside the lit edge of big forms
    if size >= 5:
        cx = (max(xs) + min(xs)) / 2
        cy = (max(ys) + min(ys)) / 2
        spot = (round(cx + LIGHT[0] * rx * 0.55), round(cy + LIGHT[1] * ry * 0.55))
        for p in [spot, (spot[0] + 1, spot[1])]:
            if p in out and out[p] >= 2:
                out[p] = 4
    return out


# ---------------------------------------------------------------- props
# Small flat shapes drawn next to the giraffe. They get the same plum outline as the body.
# Coordinates are canvas pixels (OY already included). Each shape is rows of characters:
# "#" base, "+" light, "*" highlight, "-" shadow, "." empty.
PROP_SHAPES = {
    # thought dots, growing as they rise
    'dot2': ['*#', '#-'],
    'dot3': ['*##', '###', '##-'],
    'dot4': ['.*#.', '*###', '###-', '.--.'],
    # the "!" of needs-you
    'bang': ['+##', '+##', '+##', '+##', '.#.', '...', '+##', '+#-'],
    # hearts that pulse
    'heart_s': ['*#.##', '#####', '.###.', '..#..'],
    'heart_l': ['.*#.##.', '*##+###', '#######', '.#####.', '..##-..', '...-...'],
    # the done sparkle, growing and shrinking
    'star1': ['*'],
    'star2': ['.#.', '#*#', '.#.'],
    'star3': ['..#..', '..#..', '##*##', '..#..', '..#..'],
}
PROP_COLOURS = {
    'dot': {'#': RAMPS['C'][2], '+': RAMPS['C'][3], '*': '#ffffff', '-': RAMPS['C'][1]},
    'bang': {'#': RAMPS['G'][2], '+': RAMPS['G'][3], '*': RAMPS['G'][4], '-': RAMPS['G'][1]},
    'heart': {'#': FIXED['h'], '+': RAMPS['I'][3], '*': RAMPS['I'][4], '-': RAMPS['I'][0]},
    'star': {'#': RAMPS['G'][3], '+': RAMPS['G'][3], '*': '#ffffff', '-': RAMPS['G'][2]},
}


def prop_pixels(props):
    """[(shape, x, y), ...] -> {pixel: hex}, top-left corner at (x, y)."""
    out = {}
    for shape, x0, y0 in props:
        colours = PROP_COLOURS[shape.rstrip('0123456789').split('_')[0]]
        for dy, row in enumerate(PROP_SHAPES[shape]):
            for dx, ch in enumerate(row):
                if ch != '.' and 0 <= x0 + dx < W and 0 <= y0 + dy < H:
                    out[(x0 + dx, y0 + dy)] = colours[ch]
    return out


# ---------------------------------------------------------------- the giraffe
def giraffe(pose):
    """List of layers back to front: (name, {pixel: material}, line_under) for one pose.

    pose keys: body_dy, head_dy, ear_l, ear_r (extra lift), horn_lag, tail (-1/0/1),
               blink (0 open, 1 half, 2 shut, 3 looking down, 4 looking up), look (-1/0/1),
               working, glow ({spot: 1|2}), tap (0/1/2), sparkles, props
    """
    p = dict(
        body_dy=0,
        head_dy=0,
        ear_l=0,
        ear_r=0,
        horn_lag=0,
        tail=0,
        blink=0,
        look=0,
        working=False,
        glow=(0, 0, 0),
        tap=0,
    )
    p.update(pose)
    layers = []

    def layer(name, parts, line=True):
        m = {}
        for mask, mat in parts:
            for px in mask:
                px = (px[0], px[1] + OY)
                if 0 <= px[0] < W and 0 <= px[1] < H:
                    m[px] = mat
        layers.append((name, m, line))

    bd, hd = p['body_dy'], p['head_dy']

    # tail, behind everything: curls up from the right hip, plum tuft at the end
    sway = p['tail']
    tail = thick_path(
        [(33.5, 39.5 + bd), (37.5, 38 + bd), (39.5 + sway * 0.6, 35 + bd), (40 + sway, 32.5 + bd)], 0.75
    )
    layer('tail', [(tail, 'B'), (ellipse(40.3 + sway, 31 + bd, 1.7, 2.2), 'K')])

    # body, sitting: round, cream belly, back legs folded at the sides
    body = ellipse(23.5, 36.5 + bd, 10.5, 8.5)
    layer('thigh-l', [(ellipse(14.5, 40.5, 4.2, 4.0), 'B'), (ellipse(12.8, 44.2, 2.8, 1.6), 'K')])
    layer('thigh-r', [(ellipse(32.5, 40.5, 4.2, 4.0), 'B'), (ellipse(34.2, 44.2, 2.8, 1.6), 'K')])
    spots_body = [
        ellipse(16.3, 33.5 + bd, 1.6, 1.3, 20),
        ellipse(31.0, 33.0 + bd, 2.1, 1.6, -15),
        ellipse(14.6, 39.8, 1.3, 1.1),
        ellipse(33.0, 39.6, 1.4, 1.2),
    ]
    layer(
        'body',
        [(body, 'B'), (ellipse(23.5, 38 + bd, 6.0, 6.2), 'C')]
        + [(spots_body[0], 'S'), (spots_body[1], 'S3')],
        line=False,
    )
    # put the thigh spots on top of the thighs
    layers[1][1].update({(x, y + OY): 'S' for (x, y) in spots_body[2] if (x, y + OY) in layers[1][1]})
    layers[2][1].update({(x, y + OY): 'S' for (x, y) in spots_body[3] if (x, y + OY) in layers[2][1]})

    # front legs, stubby, round plum hooves that poke out at the bottom.
    # While working they reach forward onto a keyboard (hidden behind the laptop lid) and tap.
    for i, x0 in enumerate((17, 27)):
        if p['working']:
            lift = 1 if p['tap'] == i + 1 else 0
            leg = rect(x0, 33 - lift, x0 + 3, 36 - lift) | ellipse(x0 + 2, 36.4 - lift, 2.6, 1.7)
            hoof = {(x, y) for (x, y) in leg if y >= 36 - lift}
        else:
            leg = rect(x0, 37, x0 + 3, 44) | ellipse(x0 + 2, 44.6, 2.6, 1.7)
            hoof = {(x, y) for (x, y) in leg if y >= 44}
        layer(f'leg-{x0}', [(leg, 'B'), (hoof, 'K')])

    # neck
    neck = rect(20, 20 + hd, 27, 31 + bd) | ellipse(23.5, 30 + bd, 4.5, 2.5)
    layer(
        'neck',
        [(neck, 'B'), (ellipse(21.8, 25.2 + hd, 1.7, 1.4), 'S1'), (ellipse(25.6, 28.8 + bd, 1.7, 1.4), 'S2')],
    )

    # ears (behind the head), leaf shapes pointing out and slightly down
    for side, lift in [(-1, p['ear_l']), (1, p['ear_r'])]:
        cx = 23.5 + side * 12.2
        cy = 12.5 + hd - lift
        ang = 18 * side + (-12 * side if lift else 0)
        ear = ellipse(cx, cy, 4.6, 2.0, ang)
        inner = ellipse(cx + side * 0.4, cy + 0.2, 3.0, 0.9, ang)
        layer(f'ear{side}', [(ear, 'B'), (inner, 'I')])

    # horns (ossicones): body-coloured stems, plum knobs; they trail the head by a frame
    hl = hd + p['horn_lag']
    for cx in (19.0, 28.0):
        stem = rect(int(cx) - 1, 3 + hl, int(cx), 8 + hl)
        knob = ellipse(cx, 3.0 + hl, 2.1, 1.9)
        layer(f'horn{cx}', [(stem, 'B'), (knob, 'K')])

    # head: big and round, cream muzzle that bulges a little below
    head = ellipse(23.5, 14.5 + hd, 10.8, 8.4)
    muzzle = ellipse(23.5, 19.6 + hd, 7.0, 4.3)
    layer(
        'head',
        [
            (head | muzzle, 'B'),
            (muzzle, 'C'),
            (ellipse(23.5, 8.6 + hd, 2.0, 1.2), 'S4'),
            (ellipse(14.6, 13.8 + hd, 1.0, 1.4), 'S'),
            (ellipse(32.4, 13.8 + hd, 1.0, 1.4), 'S'),
        ],
    )

    if p['working']:
        # the laptop, seen from behind its lid: teal, with a little heart on the back
        lid = rect(14, 40, 33, 46) | rect(15, 39, 32, 39)
        layer('laptop', [(lid, 'T')])

    # face details (fixed colours, drawn last)
    face = {}
    big = p.get('big_eyes', True)
    ew, eh, top = (5, 6, 10) if big else (4, 5, 11)
    for ex in (16, 27) if big else (17, 27):  # eyes: rounded, two shines, lashes on the outer corner
        lx = p['look']
        bottom = top + eh - 1
        outer = ex - 1 if ex < 24 else ex + ew
        lash_dir = -1 if ex < 24 else 1
        if p['blink'] in (0, 3, 4):
            for y in range(top, bottom + 1):
                for x in range(ex, ex + ew):
                    if y in (top, bottom) and x in (ex, ex + ew - 1):
                        continue
                    face[(x, y + hd)] = 'e'
            if p['blink'] == 0:
                sx = ex + 1 + lx
                for dx, dy in [(0, 1), (1, 1), (0, 2), (1, 2)] if big else [(0, 1), (1, 1), (0, 2)]:
                    face[(sx + dx, top + dy + hd)] = 'w'
                face[(ex + ew - 2 + min(lx, 0), bottom - 1 + hd)] = 'w'
            elif p['blink'] == 3:  # working: eyes open, looking down at the screen
                for dx, dy in [(1, 3), (2, 3), (1, 4), (2, 4)]:
                    face[(ex + dx, top + dy + hd)] = 'w'
            else:  # thinking: looking up and to the side, towards the thought dots
                for dx, dy in [(2, 0), (3, 0), (2, 1), (3, 1)]:
                    face[(ex + dx, top + dy + hd)] = 'w'
            face[(outer, top + 1 + hd)] = 'e'
            face[(outer + lash_dir, top + hd)] = 'e'
        elif p['blink'] == 1:
            for y in range(bottom - 2, bottom + 1):
                for x in range(ex, ex + ew):
                    if y == bottom and x in (ex, ex + ew - 1):
                        continue
                    face[(x, y + hd)] = 'e'
            face[(ex + 1, bottom - 2 + hd)] = 'w'
            face[(outer, bottom - 2 + hd)] = 'e'
        else:  # shut: a soft happy curve
            for x in range(ex + 1, ex + ew - 1):
                face[(x, bottom - 1 + hd)] = 'e'
            face[(ex, bottom - 2 + hd)] = 'e'
            face[(ex + ew - 1, bottom - 2 + hd)] = 'e'
            face[(outer, bottom - 3 + hd)] = 'e'
    for x in (14, 15, 16):
        face[(x, 17 + hd)] = 'b'
    for x in (31, 32, 33):
        face[(x, 17 + hd)] = 'b'
    face[(21, 19 + hd)] = 'n'
    face[(26, 19 + hd)] = 'n'
    for x, y in [(22, 21), (23, 22), (24, 22), (25, 21)]:
        face[(x, y + hd)] = 'r'
    if p['working']:
        heart = [
            (22, 40),
            (24, 40),
            (21, 41),
            (22, 41),
            (23, 41),
            (24, 41),
            (25, 41),
            (22, 42),
            (23, 42),
            (24, 42),
            (23, 43),
        ]
        for x, y in heart:
            face[(x + 1, y + 1)] = 'h'
    for x, y, kind in p.get('sparkles', []):
        face[(x, y)] = 'X'
        if kind == 2:
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                face[(x + dx, y + dy)] = 'g'
    face = {(x, y + OY): c for (x, y), c in face.items()}
    return layers, face


def render(pose):
    """Compose one frame -> {pixel: hex colour}; pixels not in the dict are transparent."""
    layers, face = giraffe(pose)
    canvas = {}  # pixel -> hex
    owner = {}  # pixel -> layer index (for overlap lines)
    glow = pose.get('glow', {})

    def colour(m, idx):
        level = glow.get(m, 0)
        if level:
            return RAMPS['G'][min(4, idx + level - 1)]
        return RAMPS[m[0]][idx]

    for li, (_name, mat, line) in enumerate(layers):
        part = set(mat)
        # light the part as one shape; spots, muzzle and hooves take their colour from the same light
        shaded = {px: colour(mat[px], idx) for px, idx in shade_region(part).items()}
        for px, m in mat.items():
            if glow.get(m, 0) >= 2:
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    q = (px[0] + dx, px[1] + dy)
                    if q in shaded and not glow.get(mat[q], 0):
                        shaded[q] = HALO
        for px, col in shaded.items():
            canvas[px] = col
            owner[px] = li
        if line:
            # a dark line where this part sits over an earlier part (below or beside it)
            for x, y in part:
                for dx, dy in ((0, 1), (1, 0), (-1, 0)):
                    q = (x + dx, y + dy)
                    if q not in part and q in owner and owner[q] < li:
                        m = mat[(x, y)]
                        canvas[(x, y)] = RAMPS[m[0]][0]
                        break
    canvas.update(prop_pixels(pose.get('props', [])))
    # outline round the silhouette, lighter on the lit (top-left) side
    solid = set(canvas)
    for y in range(H):
        for x in range(W):
            if (x, y) in solid:
                continue
            n = [(x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)]
            if any(q in solid for q in n):
                lit = ((x + 1, y) in solid or (x, y + 1) in solid) and not (
                    (x - 1, y) in solid or (x, y - 1) in solid
                )
                canvas[(x, y)] = FIXED['O'] if lit and y < 30 else FIXED['o']
    for px, c in face.items():
        canvas[px] = FIXED[c]
    for x, y in ellipse(23.5, 47.6 + OY, 14.5, 1.6):
        if (x, y) not in canvas:
            canvas[(x, y)] = SHADOW
    return canvas


# ---------------------------------------------------------------- PNG output
def write_png(path, w, h, pixels):
    raw = b''.join(b'\x00' + bytes(pixels[y * w * 4 : (y + 1) * w * 4]) for y in range(h))

    def chunk(t, d):
        return struct.pack('>I', len(d)) + t + d + struct.pack('>I', zlib.crc32(t + d) & 0xFFFFFFFF)

    png = (
        b'\x89PNG\r\n\x1a\n'
        + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 6, 0, 0, 0))
        + chunk(b'IDAT', zlib.compress(raw, 9))
        + chunk(b'IEND', b'')
    )
    with open(path, 'wb') as f:
        f.write(png)


def read_png(path):
    """Read a PNG this script wrote -> (width, height, RGBA bytes). Only handles our own output."""
    with open(path, 'rb') as f:
        data = f.read()
    assert data[:8] == b'\x89PNG\r\n\x1a\n', 'not a PNG'
    pos, idat, w, h = 8, b'', 0, 0
    while pos < len(data):
        (length,) = struct.unpack('>I', data[pos : pos + 4])
        kind, body = data[pos + 4 : pos + 8], data[pos + 8 : pos + 8 + length]
        if kind == b'IHDR':
            w, h, depth, ctype = struct.unpack('>IIBB', body[:10])
            assert (depth, ctype) == (8, 6), 'expected 8-bit RGBA'
        elif kind == b'IDAT':
            idat += body
        pos += 12 + length
    raw = zlib.decompress(idat)
    stride = w * 4 + 1
    assert all(raw[y * stride] == 0 for y in range(h)), 'expected filter type 0 on every row'
    return w, h, b''.join(raw[y * stride + 1 : (y + 1) * stride] for y in range(h))


def paint(frames, cols, scale, backdrop=None, gap=0):
    """Lay frames out in a grid -> (width, height, RGBA bytearray)."""
    n = len(frames)
    rows = (n + cols - 1) // cols
    cols = min(cols, n)
    w = (cols * W + (cols - 1) * gap) * scale
    h = (rows * H + (rows - 1) * gap) * scale
    bg = bytes(hex_rgb(backdrop) + (255,)) if backdrop else bytes(4)
    buf = bytearray(bg * (w * h))
    for fi, canvas in enumerate(frames):
        ox = (fi % cols) * (W + gap) * scale
        oy = (fi // cols) * (H + gap) * scale
        for (x, y), col in canvas.items():
            if not (0 <= x < W and 0 <= y < H):
                continue
            px = bytes(hex_rgb(col) + (255,)) * scale
            for yy in range(oy + y * scale, oy + (y + 1) * scale):
                start = (yy * w + ox + x * scale) * 4
                buf[start : start + 4 * scale] = px
    return w, h, buf


# ---------------------------------------------------------------- choreography (100 ms ticks)
TICK_MS = 100
BREATH = [0, 0, 0, 0, 0, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0]  # body sinks a pixel (exhale) and rises again


def breath(t):
    return BREATH[t % len(BREATH)]


def base_pose(t):
    """Breathing with follow-through: body first, head one tick later, horns one tick after the head."""
    bd, hd, hd_prev = breath(t), breath(t - 1), breath(t - 2)
    return {
        'body_dy': bd,
        'head_dy': hd,
        'horn_lag': hd_prev - hd,
        'tail': round(math.sin(2 * math.pi * t / 32)),
    }


def idle_timeline():
    ticks = []
    blinks = {10: 1, 11: 2, 12: 1, 44: 1, 45: 2, 46: 1, 48: 1, 49: 2, 50: 1}  # one blink, then a double blink
    for t in range(64):
        p = base_pose(t)
        p['blink'] = blinks.get(t, 0)
        if t in (26, 27):
            p['ear_l'] = 1
        if t in (58, 59):
            p['ear_r'] = 1
        if 30 <= t <= 38:
            p['look'] = 1
        ticks.append(p)
    return ticks


def thinking_timeline():
    """Eyes up towards three thought dots that appear one by one, hold, then clear (3.2 s)."""
    dots = [('dot2', 33, 3), ('dot2', 38, 2), ('dot2', 43, 1)]
    ticks = []
    for t in range(32):
        p = base_pose(t)
        p['blink'] = {22: 1, 23: 2, 24: 1}.get(t, 4)
        shown = 0 if t < 3 else 1 if t < 7 else 2 if t < 11 else 3 if t < 28 else 0
        p['props'] = dots[:shown]
        if t in (14, 15):
            p['ear_r'] = 1
        ticks.append(p)
    return ticks


# Spots that light up while working, in order: top of the neck, bottom of the neck, the crown of the head.
# (The shoulder spot S3 glowed in the SP5 spike, but the typing arm hides most of it, so 3 read as 2.)
WORKING_SPOTS = ('S1', 'S2', 'S4')


def working_timeline(n):
    """At the laptop, typing; n spots glow (one per active thread) and flare in turn (3.2 s)."""
    ticks = []
    taps = [1, 0, 2, 0, 1, 0, 2, 0, 0, 0, 0, 0, 1, 2, 1, 2, 1, 0, 0, 0, 2, 0, 1, 0, 2, 0, 0, 0, 0, 0, 1, 2]
    sparkle_at = {'S1': (16, 22), 'S2': (31, 25), 'S4': (23, 1)}
    for t in range(32):
        p = base_pose(t)
        p.update(working=True, tap=taps[t], blink={20: 1, 21: 2, 22: 1}.get(t, 3))
        glow, sparkles = {}, []
        for k, sid in enumerate(WORKING_SPOTS[:n]):
            d = (t - k * 32 // n) % 32  # every lit spot stays warm; each flares in turn
            glow[sid] = 3 if 2 <= d <= 5 else 2
            if d in (2, 3):
                x, y = sparkle_at[sid]
                sparkles.append((x, y, 1 if d == 2 else 2))
        p['glow'] = glow
        p['sparkles'] = sparkles
        ticks.append(p)
    return ticks


def needs_you_timeline():
    """Ears up, looking straight at you, a hopping "!" above the head, two hearts pulsing (1.6 s)."""
    ticks = []
    for t in range(16):
        p = base_pose(t)
        p.update(ear_l=1, ear_r=1, blink={10: 1, 11: 2, 12: 1}.get(t, 0))
        hop = 1 if t in (0, 1, 8, 9) else 0
        beat = (t // 4) % 2
        p['props'] = [
            ('bang', 38, 2 - hop),
            ('heart_l' if beat == 0 else 'heart_s', 2 if beat == 0 else 3, 22 if beat == 0 else 23),
            ('heart_s' if beat == 0 else 'heart_l', 41 if beat == 0 else 40, 19 if beat == 0 else 18),
        ]
        ticks.append(p)
    return ticks


def done_timeline():
    """A happy nod (eyes closed), then a small sparkle pops beside the head (1.6 s)."""
    nod = {2: 1, 3: 2, 4: 2, 5: 1}
    sparkle = {5: 'star1', 6: 'star2', 7: 'star3', 8: 'star3', 9: 'star2', 10: 'star1'}
    ticks = []
    for t in range(16):
        p = base_pose(0)
        prev = nod.get(t - 1, 0)
        p['head_dy'] = nod.get(t, 0)
        p['horn_lag'] = prev - p['head_dy']
        p['blink'] = 2 if 2 <= t <= 6 else 0
        if t in sparkle:
            s = sparkle[t]
            size = int(s[-1])
            p['props'] = [(s, 40 - size // 2, 7 - size // 2)]
        ticks.append(p)
    return ticks


def loops():
    """Every avatar state (B-28) -> its list of poses, one per 100 ms tick."""
    return {
        'idle': idle_timeline(),
        'thinking': thinking_timeline(),
        'working1': working_timeline(1),
        'working2': working_timeline(2),
        'working3': working_timeline(3),
        'needs_you': needs_you_timeline(),
        'done': done_timeline(),
    }


def key(p):
    return json.dumps(p, sort_keys=True)


# ---------------------------------------------------------------- GIF (for showing the animation to people)
def write_gif(path, frames, delays_cs, scale, backdrop):
    colours = {hex_rgb(backdrop): 0}
    for f in frames:
        for c in f.values():
            colours.setdefault(hex_rgb(c), len(colours))
    assert len(colours) <= 256, len(colours)
    table = sorted(colours, key=lambda c: colours[c]) + [(0, 0, 0)] * (256 - len(colours))
    w, h = W * scale, H * scale

    def lzw(data):
        clear, eoi = 256, 257
        out, bitbuf, bitcount = bytearray(), 0, 0

        def emit(code, size):
            nonlocal bitbuf, bitcount
            bitbuf |= code << bitcount
            bitcount += size
            while bitcount >= 8:
                out.append(bitbuf & 0xFF)
                bitbuf >>= 8
                bitcount -= 8

        size, nxt = 9, 258
        table_ = {bytes([i]): i for i in range(256)}
        emit(clear, size)
        cur = b''
        for byte in data:
            nk = cur + bytes([byte])
            if nk in table_:
                cur = nk
                continue
            emit(table_[cur], size)
            if nxt < 4096:
                table_[nk] = nxt
                nxt += 1
                if nxt > (1 << size) and size < 12:
                    size += 1
            else:
                emit(clear, size)
                table_ = {bytes([i]): i for i in range(256)}
                size, nxt = 9, 258
            cur = bytes([byte])
        if cur:
            emit(table_[cur], size)
        emit(eoi, size)
        if bitcount:
            out.append(bitbuf & 0xFF)
        return bytes(out)

    g = bytearray(b'GIF89a' + struct.pack('<HHBBB', w, h, 0xF7, 0, 0))
    for c in table:
        g += bytes(c)
    g += b'\x21\xff\x0bNETSCAPE2.0\x03\x01\x00\x00\x00'
    for f, d in zip(frames, delays_cs, strict=True):
        idx = bytearray(w * h)
        for (x, y), c in f.items():
            if 0 <= x < W and 0 <= y < H:
                ci = colours[hex_rgb(c)]
                for yy in range(y * scale, (y + 1) * scale):
                    idx[yy * w + x * scale : yy * w + (x + 1) * scale] = bytes([ci]) * scale
        g += b'\x21\xf9\x04\x04' + struct.pack('<H', d) + b'\x00\x00'
        g += b'\x2c' + struct.pack('<HHHHB', 0, 0, w, h, 0) + b'\x08'
        data = lzw(bytes(idx))
        for i in range(0, len(data), 255):
            block = data[i : i + 255]
            g += bytes([len(block)]) + block
        g += b'\x00'
    g += b'\x3b'
    with open(path, 'wb') as fh:
        fh.write(g)


# ---------------------------------------------------------------- export
def sheet_json(frame_count, timelines):
    """The sheet's JSON (contracts/avatar-sheet.schema.json), one loop per line so diffs stay readable."""
    head = {
        'version': 1,
        'name': 'giraffe',
        'palette': 'marcel-rose',
        'image': 'giraffe.png',
        'frame': {'width': W, 'height': H},
        'columns': COLUMNS,
        'frame_count': frame_count,
    }
    lines = [f'  {json.dumps(k)}: {json.dumps(v)},' for k, v in head.items()]
    loop_lines = [f'    {json.dumps(k)}: {json.dumps(v)}' for k, v in timelines.items()]
    return '{\n' + '\n'.join(lines) + '\n  "loops": {\n' + ',\n'.join(loop_lines) + '\n  }\n}\n'


def export(out_dir=OUT):
    """Sprite sheet + JSON for the app; a preview GIF per loop and a key-frame sheet for people."""
    os.makedirs(out_dir, exist_ok=True)
    unique, index, timelines = [], {}, {}
    for name, ticks in loops().items():
        seq = []
        for p in ticks:
            k = key(p)
            if k not in index:
                index[k] = len(unique)
                unique.append(render(p))
            i = index[k]
            if seq and seq[-1][0] == i:
                seq[-1][1] += TICK_MS
            else:
                seq.append([i, TICK_MS])
        timelines[name] = seq
    write_png(os.path.join(out_dir, 'giraffe.png'), *paint(unique, COLUMNS, 1))
    with open(os.path.join(out_dir, 'giraffe.json'), 'w') as fh:
        fh.write(sheet_json(len(unique), timelines))
    for name, seq in timelines.items():
        write_gif(
            os.path.join(out_dir, f'preview-{name}.gif'),
            [unique[i] for i, _ in seq],
            [d // 10 for _, d in seq],
            4,
            BACKDROP,
        )
    # one telling frame per state, side by side, at 1x (app size) and 4x, for review
    telling = {
        'idle': 0,
        'thinking': 14,
        'working1': 3,
        'working2': 3,
        'working3': 3,
        'needs_you': 0,
        'done': 7,
    }
    picks = [render(loops()[name][t]) for name, t in telling.items()]
    for scale in (1, 4):
        write_png(
            os.path.join(out_dir, f'preview-states-{scale}x.png'),
            *paint(picks, len(picks), scale, BACKDROP, 2),
        )
    return len(unique), {k: sum(d for _, d in v) for k, v in timelines.items()}


if __name__ == '__main__':
    frames, lengths = export(sys.argv[1] if len(sys.argv) > 1 else OUT)
    print(f'{frames} unique frames; loop lengths in ms: {lengths}')
