# shared/brand/app-icon/ — Marcel's app icon

A white pixel-art **M** on Marcel's rose (`#d87990`), wearing a soft teal bow tie low on its left leg. The M is the
app's own lettering: Silkscreen's M, the font PixelKit uses for labels and headings, on its native 5 × 5 grid, each
font pixel a hard 120-px square, centred on the 1024 canvas. Silkscreen is under the SIL Open Font License 1.1
(`ios/PixelKit/Sources/PixelKit/Resources/Fonts/Silkscreen-OFL.txt`), which allows using it in a logo; the glyph is
redrawn cell by cell in `generate.py`, so no font file is read. The bow is a smooth vector shape, on purpose: the two
styles are mixed, and the bow is Marcel's signature. It is not the giraffe avatar. One flat colour on a pink tile keeps
it apart from Mistral AI's mark (horizontal yellow-to-red colour bands).

`generate.py` draws it and writes:

| File | For |
|---|---|
| `out/app-icon-1024.png` | the icon, default appearance |
| `out/app-icon-dark-1024.png` | the dark appearance: plum field, pink M, lighter teal bow. Not used yet (below) |
| `out/checksums.txt` | SHA-256 of every file the script writes; `AppIconTests` (iOS) reads it |
| `ios/Marcel/App/AppIcon/*.png` | every size the app lists in `ios/Config/Marcel-Info.plist`, plus `AppIcon-1024.png` |

The PNGs are square, full-bleed and opaque; iOS adds the rounded corners.

## Changing the icon

1. Edit `generate.py`.
2. `uv run shared/brand/app-icon/generate.py` from the repo root. `uv` installs the one dependency, Pillow, pinned in
   the script's header. It is a dev tool only: no lane depends on Pillow, and the outputs are committed.
3. Look at `out/app-icon-1024.png`, and at a small size (40 and 60 px), where the bow must still read as a bow.
4. Commit the script and every file it wrote together.

`uv run shared/brand/app-icon/generate.py --check` (also `make app-icon`, part of `make check` from the repo root)
fails when a committed file differs from what the script draws.
`AppIconTests` (in `ios/Marcel/Chat/Tests/MarcelChatTests/`, part of `make ios-check`) fails when a committed icon
does not match `checksums.txt`, has the wrong size or has transparency.

## Dark and tinted icons

iOS can show dark, clear and tinted icons, but only from an asset catalog (`AppIcon.appiconset`). The app cannot
have one yet (see `ios/README.md`), so the dark icon waits here. The next step is in `ios/README.md`.
