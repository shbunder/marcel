---
id: SP5
title: RealityKit giraffe
feature: F00
wave: 0
runs_on: Mac
size: S
status: In Progress
depends: 
touches: scratch/spikes/SP5-*
behaviours: 
---

# SP5 — RealityKit giraffe

Blocks: F13

**Steps:**
1. Build the giraffe from rounded boxes in a `RealityView` with `.virtual` camera:
   - recipe JSON from `shared/avatar/recipes/giraffe.json`;
   - colour roles from `docs/design/palette.json` (rose `#cc5e76`, maroon `#8e3447`, teal `#5e807f`,
     orange `#f6aa1c`).
2. Compare `UnlitMaterial` (the flat logo look) with PBR (soft depth).
3. Build an idle animation (breathing and blinking) and `working(3)` (three spots glow) in a custom `System`.
4. Measure frames per second and energy use on the owner's iPhone.

**Pass:** the owner looks at a screenshot next to `docs/design/logo.png` and says "that's Marcel", and it
runs at a steady 60 fps.

## From the spikes (2026-10-04)

- Built and working in the iPhone 16 Pro simulator (60 fps there). Open until the owner runs it on the iPhone: frame rate in four modes, energy impact, the colour-fix switch, and the "that's Marcel" verdict. Steps: `scratch/spikes/SP5-avatar/RESULT.md` § "To finish on the iPhone".
