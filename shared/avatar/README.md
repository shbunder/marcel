# shared/avatar/ — Marcel's avatar

Every helper's avatar comes from the **Pixel Zoo** (`zoo/`, F19): low-res 32 × 32 animals, built from parts by a
script, not drawn by hand. Marcel is the rose giraffe with the bow tie (giraffe, rose, professional); the app bundles
that sheet as its default (`ios/AvatarKit`, `make -C ios/AvatarKit sheet`) and plays each helper's own sheet from the
hub. The first, higher-resolution giraffe (`pixel/`, spike SP5 and S-13.1) was retired on 2026-10-14; git history
keeps it.

| Path | What |
|---|---|
| [`zoo/`](zoo/) | Pixel Zoo (F19): any animal × colour × personality in 25 moods, and a sheet per look. See [`zoo/README.md`](zoo/README.md). |
| [`../../contracts/avatar-sheet.schema.json`](../../contracts/avatar-sheet.schema.json) | The shape of every sheet's JSON, shared with the app's AvatarKit (S-13.2). |

One loop per avatar state (B-28): `idle`, `thinking`, `working1`…`working3` (on the laptop, 1–3 lights, one per
running session), `needs_you`, `done`. See [`zoo/README.md`](zoo/README.md).
