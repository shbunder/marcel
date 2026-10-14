# shared/brand/app-icon/ — Marcel's app icon

A puffy bow that tiles the plane (design 12, the owner's pick, ADR-008): rows of bows in three shades of Marcel's rose
(`#d87990`) with a darker seam, each row shifted half a bow so a bow's waist fits its neighbours' wings, the tiling
scaled 1.6× and tilted 14°. The centre bow is white with a teal knot (`#3f6b6a`). The bow is Marcel's signature: every
Pixel Zoo avatar will wear one, and the home screen will show the same tiling.

The bow is one closed shape of four cubic Béziers and two straight sides (270 × 200 units). `generate.py` samples the
curves at 4× and scales down, so it needs only Pillow. The approved drawing is kept next to it,
`design-12-tiling-x1.6.svg`, the source of truth: the script draws the same thing (compared pixel by pixel when it
was written; no pixel off by more than a little anti-aliasing).

`generate.py` draws it and writes:

| File | For |
|---|---|
| `out/app-icon-1024.png` | the icon, default appearance |
| `out/app-icon-dark-1024.png` | the dark appearance: the tiling in three plums, a rose bow, a lighter teal knot. Not used yet (below) |
| `out/checksums.txt` | SHA-256 of every file the script writes; `AppIconTests` (iOS) reads it |
| `ios/Marcel/App/AppIcon/*.png` | every size the app lists in `ios/Config/Marcel-Info.plist`, plus `AppIcon-1024.png` |

The PNGs are square, full-bleed and opaque; iOS adds the rounded corners.

## Changing the icon

1. Edit `generate.py`.
2. `uv run shared/brand/app-icon/generate.py` from the repo root. `uv` installs the one dependency, Pillow, pinned in
   the script's header. It is a dev tool only: no lane depends on Pillow, and the outputs are committed.
3. Look at `out/app-icon-1024.png` next to `design-12-tiling-x1.6.svg`, and at a small size (40 and 60 px), where the
   white bow must still read as a bow.
4. Commit the script and every file it wrote together.

`uv run shared/brand/app-icon/generate.py --check` (also `make app-icon`, part of `make check` from the repo root)
fails when a committed file differs from what the script draws.
`AppIconTests` (in `ios/Marcel/Chat/Tests/MarcelChatTests/`, part of `make ios-check`) fails when a committed icon
does not match `checksums.txt`, has the wrong size or has transparency.

## Dark and tinted icons

iOS can show dark, clear and tinted icons, but only from an asset catalog (`AppIcon.appiconset`). The app cannot
have one yet (see `ios/README.md`), so the dark icon waits here. The next step is in `ios/README.md`.
