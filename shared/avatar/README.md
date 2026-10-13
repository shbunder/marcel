# shared/avatar/ — Marcel's avatar

Marcel is a pixel-art pink giraffe (decided in spike SP5, 2026-10-12). The sprite is **built, not drawn by hand**:
the parts, colour ramps and choreography live in a script, and the app plays the sheet the script writes.

| Path | What |
|---|---|
| [`pixel/build.py`](pixel/build.py) | The sprite builder. Pure Python, no packages: it writes the PNG and GIFs itself. |
| [`pixel/out/`](pixel/out/) | Its committed output: `giraffe.png` (the sheet), `giraffe.json` (the loops), preview GIFs per state. |
| [`zoo/`](zoo/) | Pixel Zoo (F19): any animal × colour × personality in 25 moods, and a sheet per look. See [`zoo/README.md`](zoo/README.md). |
| [`../../contracts/avatar-sheet.schema.json`](../../contracts/avatar-sheet.schema.json) | The shape of every sheet's JSON, shared with the app's AvatarKit (S-13.2). |

One loop per avatar state (B-28): `idle`, `thinking`, `working1`…`working3` (1–3 glowing spots, one per active
thread), `needs_you`, `done`. Rebuild with `make -C shared/avatar/pixel build`; the lane's tests fail when the
committed output differs from what the script makes. See [`pixel/README.md`](pixel/README.md).
