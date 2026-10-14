# shared/avatar/zoo/ — Pixel Zoo

Old-school (Game Boy Zelda) pixel avatars: one for Marcel and one for each family member's agent (F19). An avatar
is a **look** — animal × colour × personality — shown in a **mood**. The same name always gives the same look.

| | Choices |
|---|---|
| Animals | giraffe, crocodile, frog, cat, fox, bear, panda, rabbit, dog, pig, mouse |
| Colours | `natural` (the animal's own), 16 names (`rose`, `teal`, `ink`, …) or any `#rrggbb` |
| Personalities | professional, cheerful (flower), curious (glasses), cozy (scarf), bold (crown), cool (sunglasses), sporty (cap), shy, grumpy — each sets an accessory, a resting face and how much it bobs |
| Signature bow | every avatar wears Marcel's bow — teal at the neck (rose on green or teal animals), in the hair when a scarf is on the neck, behind the laptop while working |
| Moods | 25, from `neutral` and `happy` to `crying`, `dizzy` and `working` (at the laptop) |

## Use it

```python
from zoo import frame, from_name, sheet

look = from_name('Emma')  # {'species': 'fox', 'color': 'sky', 'personality': 'cozy', ...}
png_bytes, meta = sheet(look)  # the app's sprite sheet and its JSON
fr = frame(look, 'love', 3)  # one 32x32 frame: palette letters + palette
```

```bash
uv run python -m zoo sheet --animal giraffe --color rose --personality professional   # -> out/
uv run python -m zoo sheet --from-name Emma
uv run python -m zoo preview --animal frog --personality bold            # every mood, enlarged
uv run python -m zoo preview --animal frog --mood laughing               # one mood's 16 frames
```

## The sheet

`sheet(look)` writes 32 × 32 frames, 8 per row, on a transparent background, with the JSON of
[`contracts/avatar-sheet.schema.json`](../../../contracts/avatar-sheet.schema.json). Each loop is 16 frames of
140 ms; repeated frames are stored once.

| Loop | Mood |
|---|---|
| `idle` | the personality's resting face (a grumpy agent waits grumpily) |
| `thinking` | `thinking`: eyes aside, a thought bubble filling with dots |
| `working1`…`working3` | `working`: at the laptop, typing, 1–3 lamps lit on the lid (one per session) |
| `needs_you` | `surprised`: wide eyes and a bouncing "!" |
| `done` | `happy` |

## Where it comes from

The design was made as a JavaScript spike, [`scratch/spikes/SP5-avatar/zoo/zoo.js`](../../../scratch/spikes/SP5-avatar/zoo/zoo.js),
which also drives the live maker page. This package is a pixel-for-pixel port with no dependencies. The tests
compare it with 50 frames and 10 name picks exported from the JS (`tests/fixtures/zoo-js.json`).

To change a drawing: change `zoo.js` and `zoo/core.py` the same way, then `make fixtures` (needs node) and
`make check`. Where JS and Python differ — `Math.round`, `%` on floats, 32-bit hashing, UTF-16 names — `core.py`
copies JS's behaviour; keep it that way or the fixtures will fail.
