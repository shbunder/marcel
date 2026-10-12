# shared/avatar/pixel/ — Marcel's pixel giraffe

`build.py` draws the giraffe from parts, colour ramps and a choreography per state, and writes:

| File in `out/` | For |
|---|---|
| `giraffe.png` | the sprite sheet: 48 × 54 frames, 8 per row, transparent background |
| `giraffe.json` | which frames each loop plays and for how long (`contracts/avatar-sheet.schema.json`) |
| `preview-<loop>.gif` | each loop at 4×, for people to review |
| `preview-states-1x.png`, `-4x.png` | one telling frame per state, at app size and enlarged |

The app (AvatarKit) plays `giraffe.png` + `giraffe.json` at a whole-number zoom so pixels stay sharp.

## Loops (B-28)

| Loop | What happens |
|---|---|
| `idle` (6.4 s) | breathing, a blink and a double blink, each ear flicks, a glance |
| `thinking` (3.2 s) | eyes up to the right, three thought dots appear one by one |
| `working1`…`working3` (3.2 s) | at the laptop, typing; 1, 2 or 3 spots glow (neck top, neck bottom, crown), each flares in turn |
| `needs_you` (1.6 s) | ears up, looks straight at you, a hopping gold "!", two hearts pulsing |
| `done` (1.6 s) | a happy nod with eyes closed, then a small sparkle |

## Changing the giraffe

1. Edit `build.py` (no third-party packages: PNG via zlib, GIF via a small LZW encoder).
2. `make build` (or `python3 build.py`) and look at the previews.
3. Commit `build.py` and everything in `out/` together. `make check` fails if `out/` is not what
   `build.py` makes.

Tests: `make check` here, or from the repo root
`uv run --project shared/avatar/pixel pytest -c shared/avatar/pixel/pyproject.toml shared/avatar`.
