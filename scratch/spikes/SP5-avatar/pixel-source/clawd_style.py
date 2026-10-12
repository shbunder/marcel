"""Marcel the giraffe in the style of Clawd (the Claude Code mascot).

Clawd's grammar, decoded from the Claude Code logo (block characters, so every pixel is twice as tall as wide):
one flat colour, no outline, no shading; a 12x4 block body; single-pixel eye holes; 2-pixel claws on the sides;
four 1-pixel legs. Marcel keeps that grammar and adds what makes a giraffe: a neck, two horn stubs, ears where
Clawd has claws, and a few darker spots.

Sprites are drawn as text, one character per pixel:
  B body   S spot   K horn tip / hoof   e eye   . empty   (props use their own letters, see COLOURS)
Run: python3 clawd_style.py   -> out/marcel-clawd-sheet.png/.json, GIFs per loop on ivory and dark
"""
import colorsys
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build import write_png, hex_rgb  # noqa: E402  (the zlib PNG writer)

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")

# Anthropic palette (public brand colours)
CLAY = "#d97757"
IVORY = "#faf9f5"
SLATE = "#141413"
CLOUD = "#b0aea5"
DUSTY_BLUE = "#6a9bcc"
SAGE = "#788c5d"


def hsl(h, s, l):
    r, g, b = colorsys.hls_to_rgb(h / 360, l, s)
    return "#%02x%02x%02x" % (round(r * 255), round(g * 255), round(b * 255))


CLAWD = [
    "..............",
    "..BBBBBBBBBB..",
    "..BeBBBBBBeB..",
    "BBBBBBBBBBBBBB",
    "..BBBBBBBBBB..",
    "...B.B..B.B...",
]

# One pose for everything: a wide, low Clawd body in profile with the neck at the back (like Marcel's logo),
# and a Clawd-style head that turns to face you: two eye holes, ear stubs where Clawd has claws, horn stubs.
BASE = [
    "......K....K....",
    ".....BBBBBBBB...",
    "....BBBBBBBBBB..",
    ".....BBBBBBBB...",
    "..........BBB...",
    "..........BSB...",
    "..........BBB...",
    ".BBBBBBBBBBBB...",
    ".BSBBBBBSBBBBB..",
    ".BBBBBBBBBBBB.K.",
    "..B.B.....B.B...",
]
HEAD_ROWS = 4                      # rows 0-3: horns, head, ears; the head can bob down over the neck
EYES = [(7, 2), (10, 2)]           # eye holes, like Clawd's
EARS = [(4, 2), (13, 2)]
FRONT_LEG = (2, 10)                # reaches forward to the keyboard while typing
OX, OY = 10, 6                     # where the giraffe sits on the canvas (room above for symbols)

ROSE = hsl(345, 0.55, 0.66)            # rose pink, tuned to sit next to Claude clay (same softness and warmth)
SPOT = hsl(341, 0.52, 0.51)            # darker rose for spots and horn tips
COLOURS = {"B": ROSE, "S": SPOT, "K": SPOT,
           "L": CLOUD, "l": "#8d8b84",          # laptop (Anthropic cloud grey, darker keyboard)
           "t": CLOUD,                           # thought dots
           "1": DUSTY_BLUE, "2": SAGE, "3": CLAY, "4": ROSE}   # code bits and sparkles

CW, CH = 32, 18          # canvas in tall pixels -> frames are 32 x 36 square pixels

GLYPHS = {   # tiny code symbols that float up while typing (tall pixels)
    "<": [".X", "X.", ".X"],
    ">": ["X.", ".X", "X."],
    "/": ["..X", ".X.", "X.."],
    ";": ["X", ".", "X"],
    "{": [".XX", "XX.", ".XX"],
}

CW, CH = 32, 18          # canvas in tall pixels -> frames are 32 x 36 square pixels


class Canvas:
    def __init__(self):
        self.px = {}

    def sprite(self, rows, ox, oy, skip_rows=(), only_rows=None):
        for y, row in enumerate(rows):
            if y in skip_rows or (only_rows is not None and y not in only_rows):
                continue
            for x, ch in enumerate(row):
                if ch != ".":
                    self.put(ox + x, oy + y, ch)

    def put(self, x, y, ch):
        if 0 <= x < CW and 0 <= y < CH:
            if ch is None:
                self.px.pop((x, y), None)
            else:
                self.px[(x, y)] = ch

    def square_pixels(self):
        """Tall pixels -> square pixels (each becomes 1 wide, 2 tall) with colours."""
        out = {}
        for (x, y), ch in self.px.items():
            out[(x, 2 * y)] = COLOURS[ch]
            out[(x, 2 * y + 1)] = COLOURS[ch]
        return out


def giraffe(c, bob=0, blink=False, look=(0, 0), ear_up=(False, False), hop=0, reach=False, tap=False):
    ox, oy = OX, OY - hop
    c.sprite(BASE, ox, oy, skip_rows=range(0, HEAD_ROWS))
    c.sprite(BASE, ox, oy + bob, only_rows=range(0, HEAD_ROWS))
    for side, flick in enumerate(ear_up):
        if flick:                                  # an ear flick: the ear stretches out one pixel
            x, y = EARS[side]
            c.put(ox + x + (-1 if side == 0 else 1), oy + bob + y, "B")
    if reach:
        x, y = FRONT_LEG
        c.put(ox + x, oy + y, None)
        lift = 1 if tap else 0
        c.put(ox + x - 1, oy + y - 1 - lift, "B")
        c.put(ox + x - 2, oy + y - 1 - lift, "B")
    if not blink:
        for (x, y) in EYES:
            c.put(ox + x + look[0], oy + bob + y + look[1], None)


def laptop(c):
    # open laptop seen from the side, like Clawd's: a flat base under the reaching hoof, lid leaning back
    ground = OY + 10
    for x in range(OX - 7, OX + 1):
        c.put(x, ground, "L")
    for x in range(OX - 6, OX - 1):
        c.put(x, ground - 1, "l")
    for (x, y) in [(OX - 7, ground - 1), (OX - 7, ground - 2), (OX - 8, ground - 3), (OX - 8, ground - 4), (OX - 8, ground - 5)]:
        c.put(x, y, "L")


def glyph(c, ch, x, y, colour):
    for dy, row in enumerate(GLYPHS[ch]):
        for dx, v in enumerate(row):
            if v == "X":
                c.put(x + dx, y + dy, colour)


# ---------------------------------------------------------------- loops (100 ms ticks)
def idle(t):
    c = Canvas()
    giraffe(c, bob=1 if (t % 16) in (8, 9, 10, 11) else 0,
            blink=t in (20, 40, 42),
            look=(1, 0) if 12 <= t <= 17 else ((-1, 0) if 33 <= t <= 37 else (0, 0)),
            ear_up=(t in (28, 29), t in (30, 31)))
    return c


def thinking(t):
    c = Canvas()
    giraffe(c, blink=(t == 17), look=(1, 0), ear_up=(False, t in (8, 9)))
    phase = t % 12
    for i, (x, y) in enumerate([(24, OY - 1), (26, OY - 2), (28, OY - 3)]):
        if i * 3 <= phase < 10:
            c.put(x, y, "t")
    return c


def typing(t):
    c = Canvas()
    laptop(c)
    taps = [1, 0, 1, 0, 1, 0, 0, 0, 1, 0, 1, 1, 0, 0, 0, 0]
    giraffe(c, blink=(t == 21), look=(-1, 0), reach=True, tap=bool(taps[t % len(taps)]))
    # a code symbol floats up from above the head, one at a time, in Anthropic colours
    k = t // 8
    if t % 8 < 7:
        glyph(c, "<>/;{"[k % 5], OX + 8, OY - 4 - (t % 8) // 2, "123"[k % 3])   # starts just clear of the horns
    return c


def happy(t):
    c = Canvas()
    hop = 1 if (t % 8) in (1, 2, 3) else 0
    giraffe(c, hop=hop, ear_up=(bool(hop), bool(hop)))
    for i, (x, y, col) in enumerate([(7, OY, "1"), (26, OY - 1, "2"), (6, OY + 6, "3"), (27, OY + 5, "4")]):
        if (t + i * 2) % 8 < 2:
            c.put(x, y, col)
    return c


LOOPS = {"idle": (idle, 48), "thinking": (thinking, 24), "working": (typing, 32), "happy": (happy, 16)}


def export_all():
    from build import write_gif, frames_to_png
    import json
    unique, index, timelines = [], {}, {}
    for name, (fn, n) in LOOPS.items():
        seq = []
        for t in range(n):
            frame = fn(t).square_pixels()
            key = tuple(sorted(frame.items()))
            if key not in index:
                index[key] = len(unique)
                unique.append(frame)
            i = index[key]
            if seq and seq[-1][0] == i:
                seq[-1][1] += 100
            else:
                seq.append([i, 100])
        timelines[name] = seq
    fw, fh = CW, CH * 2
    frames_to_png(os.path.join(OUT, "marcel-clawd-sheet.png"), unique, 1, fw=fw, fh=fh)
    with open(os.path.join(OUT, "marcel-clawd-sheet.json"), "w") as f:
        json.dump({"frameWidth": fw, "frameHeight": fh, "frames": len(unique), "loops": timelines}, f, indent=1)
    for name, seq in timelines.items():
        for bg, tag in ((IVORY, "ivory"), (SLATE, "dark")):
            write_gif(os.path.join(OUT, f"marcel-clawd-{name}-{tag}.gif"), [unique[i] for i, _ in seq],
                      [d // 10 for _, d in seq], 8, bg, fw=fw, fh=fh)
    # one review sheet: a few frames of every loop, on ivory
    picks = []
    for name, seq in timelines.items():
        picks += [unique[i] for i, _ in seq[:5]]
    frames_to_png(os.path.join(OUT, "marcel-clawd-review.png"), picks, 6, IVORY, gap=1, fw=fw, fh=fh)
    print(len(unique), "unique frames", {k: sum(d for _, d in v) for k, v in timelines.items()})


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    export_all()
