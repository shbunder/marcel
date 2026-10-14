# shared/avatar/ — Marcel's avatar

Every helper's avatar comes from the **Pixel Zoo** (`zoo/`, F19): low-res 32 × 32 animals, built from parts by a
script, not drawn by hand. Marcel is the rose giraffe with the bow tie (giraffe, rose, professional); the app bundles
that sheet as its default (`ios/AvatarKit`, `make -C ios/AvatarKit sheet`) and plays each helper's own sheet from the
hub. The first, higher-resolution giraffe in `pixel/` (spike SP5) is no longer used by the app.

| Path | What |
|---|---|
| [`pixel/build.py`](pixel/build.py) | The first giraffe's builder (no longer used by the app). Pure Python, no packages: it writes the PNG and GIFs itself. |
| [`pixel/out/`](pixel/out/) | Its committed output: `giraffe.png` (the sheet), `giraffe.json` (the loops), preview GIFs per state. |
| [`zoo/`](zoo/) | Pixel Zoo (F19): any animal × colour × personality in 25 moods, and a sheet per look. See [`zoo/README.md`](zoo/README.md). |
| [`../../contracts/avatar-sheet.schema.json`](../../contracts/avatar-sheet.schema.json) | The shape of every sheet's JSON, shared with the app's AvatarKit (S-13.2). |

One loop per avatar state (B-28): `idle`, `thinking`, `working1`…`working3` (1–3 glowing spots, one per active
thread), `needs_you`, `done`. Rebuild with `make -C shared/avatar/pixel build`; the lane's tests fail when the
committed output differs from what the script makes. See [`pixel/README.md`](pixel/README.md).
