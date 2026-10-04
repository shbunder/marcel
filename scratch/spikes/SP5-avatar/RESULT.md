# SP5, round 2: plush and pixel-art looks

**Why:** the owner wanted a redesign in the soft, fluffy style of OpenAI's "Dots", still a giraffe,
animated and alive. Round 1 (below) is a flat copy of the logo. Round 2 builds two new looks into
the same app, with a switch at the top: **Plush**, **Pixel**, and **Boxes v1** (round 1, kept for comparison).

**Still open:** frame rate and energy on the iPhone (none connected), and the owner's choice between Plush and Pixel.

## Pictures

- [compare-new-looks-idle.png](screenshots/compare-new-looks-idle.png): logo | Pixel | Plush, idle.
- [compare-new-looks-working3.png](screenshots/compare-new-looks-working3.png): the same with `working(3)`.
- [filmstrip-plush-idle.png](screenshots/filmstrip-plush-idle.png): seven frames, 1.5 s apart: head nods and turns, ears move, tail swings, one frame mid-blink.
- [filmstrip-pixel-idle.png](screenshots/filmstrip-pixel-idle.png): the same for Pixel.

## What each look does to feel alive

| | Plush (3D, RealityKit) | Pixel (SwiftUI, drawn pixel by pixel) |
|---|---|---|
| Breathing | body swells and relaxes, slow weight shift | chest rises one pixel |
| Blinking | every 3.6 s, plus a longer gap one | same |
| Head | gentle sway, nods, and every 9 s looks ahead for a moment; eyes shift with it | nod, and a glance that moves the eye a pixel |
| Ears | floppy, flick every 5 s (new: the logo has none) | flick every 5 s |
| Horns | sway a moment behind the head | trail the head by a beat |
| Tail | three segments swing one after the other (new) | swishes (new) |
| `working(3)` | head dips, eyes narrow, spots 1, 3, 5 glow orange in turn | head dips, eyes narrow, same spots glow in a chase, small sparkles |
| Fuzz | 5 thin see-through fur layers over body, neck, head, legs; felt-patch spots | not applicable; two extra shades give soft edges |
| Turn 25° | yes, real 3D | no (flat by nature) |

## How they were checked (iPhone 16 Pro simulator, iOS 18.5, on an M1 Pro Mac)

| Look and state | Average fps | Slowest 1% of frames |
|---|---|---|
| Pixel, working, 45 s | 60 | 60 |
| Plush, idle, 45 s | 59 | 60 |
| Plush, working + turned, 45 s | 59 | 60 |

- One long frame (130–400 ms) at app start-up in every run. It is the first frame, not a stutter.
- **Problem found and fixed:** the plush first showed a 1%-low of 30 fps in working mode. Cause: the
  spot glow built a new material on every frame. Now nine glow steps are made once and swapped. Idle was never affected.
- **Motion from a 14 s video of the plush:** legs unchanged in 129 of 130 sampled frames; head,
  eyes, ears, horns and tail moved in 126–130. Blinking was measured in round 1 (eye closes for about 0.14 s every 4.2 s) and uses the same idea here.
- Simulator numbers only. The iPhone is the real test, and plush is the one more likely to show a difference.

## Honest weak spots

**Plush**
- The neck looks like a balloon. A real plush giraffe has a slight taper and a mane. The head is a flattened oval.
- Colour is dustier than the brand rose (a flat-front area reads about `#c37182`, brand is `#cc5e76`) because lighting and fur layers lighten and grey it. Lighting numbers are in `PlushLook` at the top of `PlushGiraffeView.swift`.
- The fur is five see-through layers, not real hair. It looks good at this size. Close up or on a big screen the edge shows as a dotted halo.
- About 90 objects in the scene. Fine in the simulator; the phone has to confirm.

**Pixel**
- Chunkier than the logo (36 × 78 pixel grid). The ear and tail are new additions.
- Two extra shades of rose (light edge, dark edge) beyond the palette file.

## What I take from this

- **Plush** is the closer match to "fluffy Dots", and its depth and head movement make it look more alive. It costs more to draw.
- **Pixel** is cheap (steady 60 fps, tiny), and the recipe-in-code approach makes other animals easy: one new part list per animal, same painter and same animations.
- **Recommendation: Plush as the main avatar, Pixel for small sizes** (notification icons, menu bar) if the phone confirms plush runs smoothly. If the phone struggles, drop the fur layers first (set `furLayers` to 2 or 3), then fall back to Pixel.
- **Other animals** (the "animal pics" idea): both looks are driven by a list of parts, so an animal is a new list, not new code. Not built yet.
- Plush needed no custom shader (no special graphics program). Real hair-style fur or a soft glowing rim would need one, and I have not tried that.

## To check on the iPhone (add to the list in round 1)

Run the app, switch **Look** between Plush and Pixel, leave each in Idle and Working(3) for a minute, and
write down the top line. Then tell me which you want, and whether the plush stutters.

---

# SP5, round 1 — RealityKit giraffe from boxes: result

**Status: built and working in the simulator. Two things are still open and need the owner:
the frame rate on the iPhone (no phone was connected) and the "that's Marcel" verdict.**

## The question

Can a giraffe made of rounded boxes, drawn by RealityKit with the logo palette, look like
`docs/design/logo.png` and run at a steady 60 frames per second? (`plan/03-spikes.md` § SP5.)

## Answer so far

| Pass condition | Result |
|---|---|
| Looks like the logo | **Close, owner has to judge.** Same shape, colours, eye, nostril, horns and spots. The neck curve is the one visible difference (see below). |
| Idle animation (breathing + blinking) | **Works.** Measured from a video, see below. |
| `working(3)`: three spots glow | **Works.** Spots 1, 3 and 5 pulse orange about every 1.2 s. |
| Unlit vs PBR | **Both built, switchable in the app.** Unlit is the flat logo look. PBR gives soft depth, and a turned view shows real 3D. |
| Steady 60 fps on the iPhone | **NOT MEASURED. No iPhone was connected.** The app has a built-in counter, ready to run (steps below). |
| Energy use on the iPhone | **NOT MEASURED.** Same reason. |
| Simulator frame rate (not the iPhone) | 60 fps average, 60 fps for the slowest 1% of frames, in the heaviest mode (PBR + turned + glowing), 40 s run. One slow frame of 130–180 ms at app start-up. |

The simulator runs on this Mac (M1 Pro). Its 60 fps says the scene is not heavy. It does not
say what the iPhone does.

## Screenshots (iPhone 16 Pro simulator, iOS 18.5)

- [compare-logo-unlit-pbr.png](screenshots/compare-logo-unlit-pbr.png): logo | Unlit | PBR, idle.
- [compare-working3.png](screenshots/compare-working3.png): logo | Unlit | PBR, `working(3)`.
- [pbr-turned.png](screenshots/pbr-turned.png): PBR turned 25°, `working(3)`.
- [hud-pbr-working.png](screenshots/hud-pbr-working.png): the app with its controls and frame counter.
- [unlit-idle.png](screenshots/unlit-idle.png): the raw Unlit screenshot.

## Not tested

- The on-screen controls (Unlit/PBR, Idle/Working(3), Turn 25°, brightness fix, Reset) were not
  tapped. Nothing here can tap the simulator. The same states were checked through launch options,
  which use the same code, but the first tap on the phone is the real test.
- Nothing was run on a real iPhone.

## What differs from the logo

- **Neck.** The logo has one smooth sweeping curve from head to back. Boxes can only make
  steps: a rounded block at the base of the neck and a big rounded body. Recognisable, not identical.
- **Soft shading.** The logo has a faint darker gradient on the neck and back. Unlit is flat.
- **Far legs and haunch.** The dark patch at the back is a rounded box that bulges slightly.
- **Eye and nostril in PBR** look dark grey, not black, because lighting lifts them.

## What was run

- Xcode 26.6 (`DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer`; the Mac's default
  developer folder points at the command line tools, which cannot build apps).
- Project: `SP5Avatar.xcodeproj`, one iPhone app target, minimum iOS 18.0, Swift 5 mode, no packages.
- Built and run on the iPhone 16 Pro simulator, iOS 18.5.
- Build command (building the target directly; see "Plan changes" for why):
  ```
  DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer \
  xcodebuild -project SP5Avatar.xcodeproj -target SP5Avatar -sdk iphonesimulator \
    -configuration Debug ARCHS=arm64 CODE_SIGNING_ALLOWED=NO build
  ```
- What the code does:
  - `giraffe.json`: 21 boxes, measured in logo pixels, coloured by role (rose `#cc5e76`,
    maroon `#8e3447`, orange `#f6aa1c`, plus black and white for the eye).
  - `RealityView` with `.virtual` camera, narrow 22° lens from 3 m so Unlit stays close to flat.
  - `AvatarSystem`: one custom RealityKit `System` that runs every frame: breathing, blinking, glow.
  - `FPSMeter`: counts real screen frames, shows now / average / slowest-1% / worst frame.
- Launch options for repeatable screenshots: `-style pbr`, `-mode working`, `-yaw 1`, `-hud 0`, `-fix 0`.

### Animation check (from a 10 s screen recording of the simulator)

- **Breathing:** the head rises and falls 22 px (of 2622) in a smooth 4 s cycle.
- **Blinking:** the eye closes to about 20% for roughly 4 video frames (0.14 s), every 4.2 s.
- **Glow:** spot 1 swings between `#d08241` and `#f8b42b` about every 1.2 s.

## Findings that change how F13 should be built

1. **Unlit colours come out too dark in RealityKit.** On the simulator, asking for rose
   `#cc5e76` showed `#b34f65`, and the off-white background came out light grey. White showed as
   about `#d6d6d6`. This is not the camera effects (switched off, no change) and not RealityView
   specifically (the older `ARView` did the same). The app now asks for a brighter colour,
   using a measured table in `AvatarMaterials.brightnessFix`, and lands within 1–4 of 255 on the palette.
   **This was measured on the simulator only.** The on-screen switch "Flat-colour brightness fix"
   turns it off. On the phone, compare with the fix on and off. If the phone is already correct
   with it off, the table is only a simulator workaround and should not ship.
2. **iOS 18 `RealityView` has no plain background colour.** The brand "Snow" background is a
   large flat panel behind the giraffe. Fine for the avatar, but it means the avatar cannot be
   transparent over other app content without a different approach.
3. **Boxes cannot make the logo's neck curve.** If a closer match matters, F13 needs one curved
   piece (a custom mesh from a drawn outline) for the neck and shoulder, and boxes for the rest.
4. **The recipe file does not exist yet.** The plan names `shared/avatar/recipes/giraffe.json`;
   this spike ships its own copy as `SP5Avatar/giraffe.json`. Its format (pixel boxes from the
   logo, a palette-role name per box, an animation tag, a glow list) worked and can seed the shared one.
5. **`maroon` is not in `docs/design/palette.json`.** The spike uses `#8e3447` from the spec. The
   logo's own dark colour measures `#8f3649`. Add maroon to the palette file, and decide which one wins.
6. **Build tool quirk.** `xcodebuild -scheme … -destination …` often failed with "iOS 26.5 is not
   installed" even though a simulator runtime was available. Building the target directly worked
   every time. Relevant to any later automated build for the app.

## To finish on the iPhone (about 5 minutes)

1. Open `scratch/spikes/SP5-avatar/SP5Avatar.xcodeproj` in Xcode.
2. Project → target SP5Avatar → Signing: pick your team (the field is blank on purpose).
3. Plug in the iPhone, choose it as the run target, press Run. The scheme runs the Release build,
   which is what matters for speed.
4. In the app, tap Reset on the counter, leave each mode for 60 seconds, and write down the
   top line (now / avg / 1% low / worst). Do these four: Unlit idle, Unlit working(3),
   PBR idle, PBR working(3) with "Turn 25°" on.
5. For energy use: in Xcode, open the Debug navigator → Energy Impact while it runs.
6. Compare with the screenshots above and the logo. Say "that's Marcel" or say what is off.
7. Also try the "Flat-colour brightness fix" switch off and on, and tell me which looks like the logo.

Note: Pro iPhones can run screens at 120 Hz. This spike did not opt in (that needs one extra
setting), so expect it to be capped at 60. That matches the pass condition. 120 Hz is a later question.

## What the plan changes because of this

- F13 (avatar) is not blocked: the approach works. Use RealityView, boxes from a JSON recipe, one
  custom System for idle and working states.
- Add to F13: a colour check on the real phone (finding 1), and a decision on the neck curve (finding 3).
- SP5 stays **open** until the iPhone numbers and the owner's verdict are in.
