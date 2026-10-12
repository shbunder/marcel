"""Builds the pixel-art giraffe (Marcel) from shape layers.

Pipeline, the way a pixel artist works, but repeatable:
  1. each body part is a mask on a 48x48 grid, tagged with a material (body pink, cream, spot, plum, ...);
  2. each part is shaded on its own with a hue-shifted ramp (light from the top left);
  3. parts are stacked back to front, with a dark line where a part overlaps another;
  4. a plum outline goes round the whole silhouette (a lighter one on the lit top-left side);
  5. face details (eyes, shine, lashes, blush, mouth) go on last, unshaded.
Frames are built by moving whole parts by whole pixels (breathing, blinking, ear flicks, tail swish).

Run:  python3 build.py            -> out/preview-*.png (big, for review) and sheets for the app
No third-party packages: PNGs are written with zlib.
"""
import json
import math
import os
import struct
import sys
import zlib

W, H = 48, 54
OY = 3   # everything sits 3 px lower, leaving room above for bobbing ears and horns
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")

# ---------------------------------------------------------------- palette
# Ramps go deepest shadow -> shadow -> base -> light -> highlight, hue-shifted:
# shadows lean purple, highlights lean peach (never just "add black / add white").
RAMPS = {
    "B": ["#9b3f6b", "#c9577e", "#f08aa0", "#ffb2b6", "#ffdccf"],  # body pink (brand rose sits in the shadow)
    "C": ["#e0a3ab", "#f3c3c1", "#fde3da", "#fff3ea", "#ffffff"],  # cream muzzle and belly
    "S": ["#7a2850", "#9a3462", "#bd4873", "#d76589", "#e98aa2"],  # spots
    "K": ["#3f1838", "#5c2347", "#7d3258", "#a04b74", "#c46b90"],  # plum: horn knobs, hooves, tail tuft
    "I": ["#d9587c", "#ea6f8e", "#ff9fb3", "#ffc2cc", "#ffe0e4"],  # inside of the ears
    "G": ["#c9631f", "#e8862a", "#f6aa1c", "#ffcf5c", "#fff0b0"],  # glow (working)
    "T": ["#2c4545", "#3d5f5e", "#5e807f", "#86a9a6", "#bcdad5"],  # teal (laptop)
}
FIXED = {
    "o": "#4a1a3a",   # outline, shadow side
    "O": "#7d2f55",   # outline, lit side
    "e": "#2a1328",   # eye
    "w": "#ffffff",   # eye shine
    "b": "#f8698a",   # blush
    "r": "#8a2f52",   # mouth
    "n": "#b8456c",   # nostril
    "x": "#c8f3ea",   # laptop screen glow
    "X": "#ffffff",
    "h": "#f8698a",   # heart on the laptop
    "g": "#ffcf5c",   # sparkle arms
}
BACKDROP = "#faf5f8"
SHADOW = "#eedde5"
HALO = "#ffc98a"     # warm light round a glowing spot   # soft ground shadow, tuned for the Snow background


def hex_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


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
    for (ax, ay), (bx, by) in zip(points, points[1:]):
        steps = int(max(abs(bx - ax), abs(by - ay)) * 3) + 1
        for i in range(steps + 1):
            t = i / steps
            pts |= ellipse(ax + (bx - ax) * t, ay + (by - ay) * t, r, r)
    return pts


def shift(mask, dx, dy):
    return {(x + dx, y + dy) for (x, y) in mask}


# ---------------------------------------------------------------- shading
LIGHT = (-0.6, -0.8)   # light comes from the top left


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
        for (x, y) in region:
            flat[(x, y)] = 1 if ((x, y + 1) not in region and len(region) > 6) else 2
        return flat
    k_shadow = max(1, round(size * 0.38))
    k_deep = 1 if size >= 4.5 else 0
    k_light = max(1, round(size * 0.22)) if size >= 2 else 0
    out = {}
    for (x, y) in region:
        def leaves(direction, k):
            for t in range(1, k + 1):
                q = (round(x + direction[0] * t), round(y + direction[1] * t))
                if q not in region:
                    return True
            return False
        away = (-LIGHT[0] / 0.8, -LIGHT[1] / 0.8)      # towards the bottom right
        toward = (LIGHT[0] / 0.8, LIGHT[1] / 0.8)      # towards the top left
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


# ---------------------------------------------------------------- the giraffe
def giraffe(pose):
    """List of layers back to front: (name, {pixel: material}, line_under) for one pose.

    pose keys: body_dy, head_dy, ear_l, ear_r (extra lift), horn_lag, tail (-1/0/1),
               blink (0 open, 1 half, 2 shut), look (-1/0/1), working, glow (0..1 per spot list), tap (0/1)
    """
    p = dict(body_dy=0, head_dy=0, ear_l=0, ear_r=0, horn_lag=0, tail=0, blink=0, look=0,
             working=False, glow=(0, 0, 0), tap=0)
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

    bd, hd = p["body_dy"], p["head_dy"]

    # tail, behind everything: curls up from the right hip, plum tuft at the end
    sway = p["tail"]
    tail = thick_path([(33.5, 39.5 + bd), (37.5, 38 + bd), (39.5 + sway * 0.6, 35 + bd), (40 + sway, 32.5 + bd)], 0.75)
    layer("tail", [(tail, "B"), (ellipse(40.3 + sway, 31 + bd, 1.7, 2.2), "K")])

    # body, sitting: round, cream belly, back legs folded at the sides
    body = ellipse(23.5, 36.5 + bd, 10.5, 8.5)
    layer("thigh-l", [(ellipse(14.5, 40.5, 4.2, 4.0), "B"), (ellipse(12.8, 44.2, 2.8, 1.6), "K")])
    layer("thigh-r", [(ellipse(32.5, 40.5, 4.2, 4.0), "B"), (ellipse(34.2, 44.2, 2.8, 1.6), "K")])
    spots_body = [ellipse(16.3, 33.5 + bd, 1.6, 1.3, 20), ellipse(31.0, 33.0 + bd, 2.1, 1.6, -15),
                  ellipse(14.6, 39.8, 1.3, 1.1), ellipse(33.0, 39.6, 1.4, 1.2)]
    layer("body", [(body, "B"), (ellipse(23.5, 38 + bd, 6.0, 6.2), "C")] +
          [(spots_body[0], "S"), (spots_body[1], "S3")], line=False)
    # put the thigh spots on top of the thighs
    layers[1][1].update({(x, y + OY): "S" for (x, y) in spots_body[2] if (x, y + OY) in layers[1][1]})
    layers[2][1].update({(x, y + OY): "S" for (x, y) in spots_body[3] if (x, y + OY) in layers[2][1]})

    # front legs, stubby, round plum hooves that poke out at the bottom.
    # While working they reach forward onto a keyboard (hidden behind the laptop lid) and tap.
    for i, x0 in enumerate((17, 27)):
        if p["working"]:
            lift = 1 if p["tap"] == i + 1 else 0
            leg = rect(x0, 33 - lift, x0 + 3, 36 - lift) | ellipse(x0 + 2, 36.4 - lift, 2.6, 1.7)
            hoof = {(x, y) for (x, y) in leg if y >= 36 - lift}
        else:
            leg = rect(x0, 37, x0 + 3, 44) | ellipse(x0 + 2, 44.6, 2.6, 1.7)
            hoof = {(x, y) for (x, y) in leg if y >= 44}
        layer(f"leg-{x0}", [(leg, "B"), (hoof, "K")])

    # neck
    neck = rect(20, 20 + hd, 27, 31 + bd) | ellipse(23.5, 30 + bd, 4.5, 2.5)
    layer("neck", [(neck, "B"), (ellipse(21.8, 25.2 + hd, 1.7, 1.4), "S1"), (ellipse(25.6, 28.8 + bd, 1.7, 1.4), "S2")])

    # ears (behind the head), leaf shapes pointing out and slightly down
    for side, lift in [(-1, p["ear_l"]), (1, p["ear_r"])]:
        cx = 23.5 + side * 12.2
        cy = 12.5 + hd - lift
        ang = 18 * side + (-12 * side if lift else 0)
        ear = ellipse(cx, cy, 4.6, 2.0, ang)
        inner = ellipse(cx + side * 0.4, cy + 0.2, 3.0, 0.9, ang)
        layer(f"ear{side}", [(ear, "B"), (inner, "I")])

    # horns (ossicones): body-coloured stems, plum knobs; they trail the head by a frame
    hl = hd + p["horn_lag"]
    for cx in (19.0, 28.0):
        stem = rect(int(cx) - 1, 3 + hl, int(cx), 8 + hl)
        knob = ellipse(cx, 3.0 + hl, 2.1, 1.9)
        layer(f"horn{cx}", [(stem, "B"), (knob, "K")])

    # head: big and round, cream muzzle that bulges a little below
    head = ellipse(23.5, 14.5 + hd, 10.8, 8.4)
    muzzle = ellipse(23.5, 19.6 + hd, 7.0, 4.3)
    layer("head", [(head | muzzle, "B"), (muzzle, "C"), (ellipse(23.5, 8.6 + hd, 2.0, 1.2), "S"),
                   (ellipse(14.6, 13.8 + hd, 1.0, 1.4), "S"), (ellipse(32.4, 13.8 + hd, 1.0, 1.4), "S")])

    if p["working"]:
        # the laptop, seen from behind its lid: teal, with a little heart on the back
        lid = rect(14, 40, 33, 46) | rect(15, 39, 32, 39)
        layer("laptop", [(lid, "T")])

    # face details (fixed colours, drawn last)
    face = {}
    big = p.get("big_eyes", True)
    ew, eh, top = (5, 6, 10) if big else (4, 5, 11)
    for ex in ((16, 27) if big else (17, 27)):   # eyes: rounded, two shines, lashes on the outer corner
        lx = p["look"]
        bottom = top + eh - 1
        outer = ex - 1 if ex < 24 else ex + ew
        lash_dir = -1 if ex < 24 else 1
        if p["blink"] == 0:
            for y in range(top, bottom + 1):
                for x in range(ex, ex + ew):
                    if y in (top, bottom) and x in (ex, ex + ew - 1):
                        continue
                    face[(x, y + hd)] = "e"
            sx = ex + 1 + lx
            for (dx, dy) in ([(0, 1), (1, 1), (0, 2), (1, 2)] if big else [(0, 1), (1, 1), (0, 2)]):
                face[(sx + dx, top + dy + hd)] = "w"
            face[(ex + ew - 2 + min(lx, 0), bottom - 1 + hd)] = "w"
            face[(outer, top + 1 + hd)] = "e"; face[(outer + lash_dir, top + hd)] = "e"
        elif p["blink"] == 3:                     # working: eyes open, looking down at the screen
            for y in range(top, bottom + 1):
                for x in range(ex, ex + ew):
                    if y in (top, bottom) and x in (ex, ex + ew - 1):
                        continue
                    face[(x, y + hd)] = "e"
            for (dx, dy) in [(1, 3), (2, 3), (1, 4), (2, 4)]:
                face[(ex + dx, top + dy + hd)] = "w"
            face[(outer, top + 1 + hd)] = "e"; face[(outer + lash_dir, top + hd)] = "e"
        elif p["blink"] == 1:
            for y in range(bottom - 2, bottom + 1):
                for x in range(ex, ex + ew):
                    if y == bottom and x in (ex, ex + ew - 1):
                        continue
                    face[(x, y + hd)] = "e"
            face[(ex + 1, bottom - 2 + hd)] = "w"
            face[(outer, bottom - 2 + hd)] = "e"
        else:                                      # shut: a soft happy curve
            for x in range(ex + 1, ex + ew - 1):
                face[(x, bottom - 1 + hd)] = "e"
            face[(ex, bottom - 2 + hd)] = "e"; face[(ex + ew - 1, bottom - 2 + hd)] = "e"
            face[(outer, bottom - 3 + hd)] = "e"
    for x in (14, 15, 16):
        face[(x, 17 + hd)] = "b"
    for x in (31, 32, 33):
        face[(x, 17 + hd)] = "b"
    face[(21, 19 + hd)] = "n"; face[(26, 19 + hd)] = "n"
    for (x, y) in [(22, 21), (23, 22), (24, 22), (25, 21)]:
        face[(x, y + hd)] = "r"
    if p["working"]:
        heart = [(22, 40), (24, 40), (21, 41), (22, 41), (23, 41), (24, 41), (25, 41), (22, 42), (23, 42), (24, 42), (23, 43)]
        for (x, y) in heart:
            face[(x + 1, y + 1)] = "h"
    for (x, y, kind) in p.get("sparkles", []):
        face[(x, y)] = "X"
        if kind == 2:
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                face[(x + dx, y + dy)] = "g"
    face = {(x, y + OY): c for (x, y), c in face.items()}
    return layers, face


def render(pose):
    """Compose one frame -> 2D list of hex colours (None = transparent)."""
    layers, face = giraffe(pose)
    canvas = {}       # pixel -> hex
    owner = {}        # pixel -> layer index (for overlap lines)
    for li, (name, mat, line) in enumerate(layers):
        part = set(mat)
        # light the part as one shape; spots, muzzle and hooves take their colour from the same light
        glow = pose.get("glow", {})
        def colour(m, idx):
            level = glow.get(m, 0)
            if level:
                return RAMPS["G"][min(4, idx + level - 1)]
            return RAMPS[m[0]][idx]
        shaded = {px: colour(mat[px], idx) for px, idx in shade_region(part).items()}
        for px, m in mat.items():
            if glow.get(m, 0) == 2:
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    q = (px[0] + dx, px[1] + dy)
                    if q in shaded and not glow.get(mat[q], 0):
                        shaded[q] = HALO
        for px, col in shaded.items():
            canvas[px] = col
            owner[px] = li
        if line:
            # a dark line where this part sits over an earlier part (below or beside it)
            for (x, y) in part:
                for dx, dy in ((0, 1), (1, 0), (-1, 0)):
                    q = (x + dx, y + dy)
                    if q not in part and q in owner and owner[q] < li:
                        m = mat[(x, y)]
                        canvas[(x, y)] = RAMPS[m[0]][0]
                        break
    # outline round the silhouette, lighter on the lit (top-left) side
    solid = set(canvas)
    for y in range(H):
        for x in range(W):
            if (x, y) in solid:
                continue
            n = [(x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)]
            if any(q in solid for q in n):
                lit = ((x + 1, y) in solid or (x, y + 1) in solid) and not ((x - 1, y) in solid or (x, y - 1) in solid)
                canvas[(x, y)] = FIXED["O"] if lit and y < 30 else FIXED["o"]
    for px, c in face.items():
        canvas[px] = FIXED[c]
    for (x, y) in ellipse(23.5, 47.6 + OY, 14.5, 1.6):
        if (x, y) not in canvas:
            canvas[(x, y)] = SHADOW
    return canvas


# ---------------------------------------------------------------- PNG output
def write_png(path, pixels, w, h):
    raw = b"".join(b"\x00" + bytes(pixels[y * w * 4:(y + 1) * w * 4]) for y in range(h))
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)) + \
        chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")
    with open(path, "wb") as f:
        f.write(png)


def frames_to_png(path, frames, scale, backdrop=None, gap=0, fw=None, fh=None):
    fw, fh = fw or W, fh or H
    n = len(frames)
    w = n * fw * scale + (n - 1) * gap * scale
    h = fh * scale
    buf = bytearray(w * h * 4)
    bg = hex_rgb(backdrop) + (255,) if backdrop else (0, 0, 0, 0)
    for i in range(0, len(buf), 4):
        buf[i:i + 4] = bytes(bg)
    for fi, canvas in enumerate(frames):
        ox = fi * (fw + gap) * scale
        for (x, y), col in canvas.items():
            if not (0 <= x < fw and 0 <= y < fh):
                continue
            r, g, b = hex_rgb(col)
            for yy in range(y * scale, (y + 1) * scale):
                row = yy * w * 4
                for xx in range(ox + x * scale, ox + (x + 1) * scale):
                    buf[row + xx * 4: row + xx * 4 + 4] = bytes((r, g, b, 255))
    write_png(path, buf, w, h)


# ---------------------------------------------------------------- choreography (100 ms ticks)
TICK_MS = 100
BREATH = [0, 0, 0, 0, 0, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0]   # body sinks a pixel (exhale) and rises again


def breath(t):
    return BREATH[t % len(BREATH)]


def base_pose(t):
    """Breathing with follow-through: body first, head one tick later, horns one tick after the head."""
    bd, hd, hd_prev = breath(t), breath(t - 1), breath(t - 2)
    return {"body_dy": bd, "head_dy": hd, "horn_lag": hd_prev - hd,
            "tail": round(math.sin(2 * math.pi * t / 32))}


def idle_timeline():
    ticks = []
    blinks = {10: 1, 11: 2, 12: 1, 44: 1, 45: 2, 46: 1, 48: 1, 49: 2, 50: 1}   # one blink, then a double blink
    for t in range(64):
        p = base_pose(t)
        p["blink"] = blinks.get(t, 0)
        if t in (26, 27):
            p["ear_l"] = 1
        if t in (58, 59):
            p["ear_r"] = 1
        if 30 <= t <= 38:
            p["look"] = 1
        ticks.append(p)
    return ticks


def working_timeline():
    ticks = []
    taps = [1, 0, 2, 0, 1, 0, 2, 0, 0, 0, 0, 0, 1, 2, 1, 2, 1, 0, 0, 0, 2, 0, 1, 0, 2, 0, 0, 0, 0, 0, 1, 2]
    sparkle_at = {"S1": (16, 22), "S2": (31, 25), "S3": (36, 29)}
    for t in range(32):
        p = base_pose(t)
        p.update(working=True, tap=taps[t], blink={20: 1, 21: 2, 22: 1}.get(t, 3))
        glow, sparkles = {}, []
        for k, sid in enumerate(("S1", "S2", "S3")):
            d = (t - k * 8) % 24                      # each spot glows in turn, 2.4 s round
            level = 1 if d in (0, 1, 6, 7) else (2 if 2 <= d <= 5 else 0)
            if level:
                glow[sid] = level
            if d in (2, 3):
                x, y = sparkle_at[sid]
                sparkles.append((x, y, 1 if d == 2 else 2))
        p["glow"] = glow
        p["sparkles"] = sparkles
        ticks.append(p)
    return ticks


def key(p):
    return json.dumps(p, sort_keys=True)


# ---------------------------------------------------------------- GIF (for showing the animation to people)
def write_gif(path, frames, delays_cs, scale, backdrop, fw=None, fh=None):
    fw, fh = fw or W, fh or H
    colours = {hex_rgb(backdrop): 0}
    for f in frames:
        for c in f.values():
            colours.setdefault(hex_rgb(c), len(colours))
    assert len(colours) <= 256, len(colours)
    table = sorted(colours, key=colours.get) + [(0, 0, 0)] * (256 - len(colours))
    w, h = fw * scale, fh * scale

    def lzw(data):
        clear, eoi = 256, 257
        out, bitbuf, bitcount = bytearray(), 0, 0
        def emit(code, size):
            nonlocal bitbuf, bitcount
            bitbuf |= code << bitcount
            bitcount += size
            while bitcount >= 8:
                out.append(bitbuf & 0xff); bitbuf >>= 8; bitcount -= 8
        size, nxt = 9, 258
        table_ = {bytes([i]): i for i in range(256)}
        emit(clear, size)
        cur = b""
        for byte in data:
            nk = cur + bytes([byte])
            if nk in table_:
                cur = nk
                continue
            emit(table_[cur], size)
            if nxt < 4096:
                table_[nk] = nxt; nxt += 1
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
            out.append(bitbuf & 0xff)
        return bytes(out)

    g = bytearray(b"GIF89a" + struct.pack("<HHBBB", w, h, 0xF7, 0, 0))
    for c in table:
        g += bytes(c)
    g += b"\x21\xFF\x0BNETSCAPE2.0\x03\x01\x00\x00\x00"
    for f, d in zip(frames, delays_cs):
        idx = bytearray(w * h)
        for (x, y), c in f.items():
            if 0 <= x < fw and 0 <= y < fh:
                ci = colours[hex_rgb(c)]
                for yy in range(y * scale, (y + 1) * scale):
                    idx[yy * w + x * scale: yy * w + (x + 1) * scale] = bytes([ci]) * scale
        g += b"\x21\xF9\x04\x04" + struct.pack("<H", d) + b"\x00\x00"
        g += b"\x2C" + struct.pack("<HHHHB", 0, 0, w, h, 0) + b"\x08"
        data = lzw(bytes(idx))
        for i in range(0, len(data), 255):
            block = data[i:i + 255]
            g += bytes([len(block)]) + block
        g += b"\x00"
    g += b"\x3B"
    with open(path, "wb") as fh:
        fh.write(g)


def export():
    """Sprite sheet + timeline for the app, previews and GIFs for people."""
    loops = {"idle": idle_timeline(), "working": working_timeline()}
    unique, index = [], {}
    timelines = {}
    for name, ticks in loops.items():
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
    frames_to_png(os.path.join(OUT, "giraffe-sheet.png"), unique, 1)
    with open(os.path.join(OUT, "giraffe-sheet.json"), "w") as fh:
        json.dump({"frameWidth": W, "frameHeight": H, "frames": len(unique), "loops": timelines}, fh, indent=1)
    for name, seq in timelines.items():
        write_gif(os.path.join(OUT, f"giraffe-{name}.gif"), [unique[i] for i, _ in seq], [d // 10 for _, d in seq], 6, BACKDROP)
        firsts = [unique[i] for i, _ in seq][:12]
        frames_to_png(os.path.join(OUT, f"preview-{name}.png"), firsts, 5, BACKDROP, gap=1)
    print(len(unique), "unique frames;", {k: sum(d for _, d in v) for k, v in timelines.items()}, "ms per loop")


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    which = sys.argv[1] if len(sys.argv) > 1 else "static"
    if which == "static":
        frames_to_png(os.path.join(OUT, "preview-static.png"), [render({"big_eyes": False}), render({})], 10, BACKDROP, gap=2)
        print("wrote", os.path.join(OUT, "preview-static.png"))
    else:
        export()
