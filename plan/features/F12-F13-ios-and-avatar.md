# F12 — iOS app  (waves 1–2, all on the Mac)

Swift 6, SwiftUI, iOS 18+. Two local packages:

- `MarcelKit`: models, API client, WebSocket, cache;
- `AvatarKit`: F13.

Everything is built against the **mock hub** (S-02.4) until wave 3.

### S-12.1  Project, MarcelKit, pairing                       runs-on: Mac · size: M
depends: S-02.1, S-02.4   touches: ios/
behaviours: B-27
do:
1. Create the Xcode project `Marcel` with the two packages.
2. Generate the API client from `app-api.yaml` with swift-openapi-generator.
3. Pairing screen: scan the QR → `POST /pair` → store the token in the Keychain.
4. WebSocket client with reconnect and `since` replay; a SwiftData cache of messages and tasks.
done when:
- [ ] `make ios-check` passes.
- [ ] XCTest covers pairing, WS replay after a simulated drop, and that the token is in the Keychain (not UserDefaults).

### S-12.2  Chat screen                                       runs-on: Mac · size: L (split: bubbles / cards / side threads)
depends: S-12.1   touches: ios/Marcel/Chat/
behaviours: B-01, B-02, B-06, B-08, B-12
do:
1. Main conversation with chat bubbles. The composer is always enabled (non-blocking).
2. Message kinds:
   - text, rendered as Markdown;
   - **milestone card** (task title, location badge NUC or cloud, state, open button);
   - **approval card** (tool + summary, Approve and Deny buttons, resolved state).
3. **Reply in thread**: long-press or a reply button opens a side-thread sheet, Slack-style. The parent message
   shows "n replies". Side threads have a "send to main" action.
4. Pagination upward from the cache.
done when:
- [ ] UI tests against the mock-hub scenario: send → started card → approval → approve → done card.
- [ ] Side-thread UI test: open, reply, send to main.

### S-12.3  Activity tab                                      runs-on: Mac · size: M
depends: S-12.1   touches: ios/Marcel/Activity/
behaviours: B-07, B-25
do: One card per task, grouped **Needs you** (pinned), Working, Queued, then Done or Failed (last 7 days). Each card shows
title, location, model, age, last activity and PR badge. Swipe to stop. It updates live from WS.
done when:
- [ ] Test: the grouping and ordering view model works.
- [ ] UI test: a task moving to `needs_you` jumps to the top.

### S-12.4  Thread view                                       runs-on: Mac · size: L (split: transcript / steer+approve)
depends: S-12.1   touches: ios/Marcel/Thread/
behaviours: B-11, B-12, B-26
do:
1. A live transcript from `GET /tasks/{id}/events` plus WS `task.event`. Event rendering:
   - text: Markdown;
   - tool calls: collapsible rows (tool, args summary, result);
   - **diffs**: a unified diff view with syntax colours;
   - permission requests: inline approval cards.
2. A composer that steers the worker. A toolbar with stop, hand back to Marcel, and "open in Claude"
   (cloud URL or Remote Control).
3. Jump to the latest event. Stay smooth with 5k events (lazy list).
done when:
- [ ] Snapshot tests for each event type.
- [ ] UI test: steer and approve.
- [ ] Performance test: 5k events load in under 1 s on the simulator.

### S-12.5  Library                                           runs-on: Mac · size: M
depends: S-12.1   touches: ios/Marcel/Library/
behaviours: B-21
do:
1. Library grouped per task and by kind:
   - PR cards (state, checks, review; open in GitHub);
   - docs: Markdown rendered natively; HTML in `WKWebView` with JS off unless the hub marks it trusted;
   - files: QuickLook;
   - **dashboards**: a small native widget set driven by JSON (stat, list, checklist, table, chart via
     Swift Charts), with the schema in `contracts/dashboard.schema.json`.
done when:
- [ ] Each kind renders from mock-hub fixtures (snapshot tests).

### S-12.6  Memory editor                                     runs-on: Mac · size: S
depends: S-12.1   touches: ios/Marcel/Memory/
behaviours: B-20
do: A tree of memory files, a Markdown editor with preview, and save with etag. A 409 shows "changed elsewhere" with
reload and a diff.
done when:
- [ ] UI test: edit, save, conflict path.

### S-12.7  Settings, notifications, deep links               runs-on: Mac · size: M
depends: S-12.1, S-08.1   touches: ios/Marcel/Settings/ ios/Marcel/App/
behaviours: B-16, B-18
do:
1. Settings:
   - devices;
   - the notification backend (APNs if entitled, else an ntfy topic with instructions);
   - schedules list (pause and delete);
   - usage meter;
   - agent name and palette picker (with the avatar preview).
2. Deep links `marcel://task/<id>` and `marcel://message/<id>`, used by pushes.
done when:
- [ ] Test: deep links route correctly from a cold start.

# F13 — Avatar ("made for you", RealityKit)  (waves 1–2)

Procedural kit-of-parts. **Recipe + palette + seed → entity tree**: deterministic, with no 3D assets and no
dependencies.

### S-13.1  Recipe format, palettes, giraffe recipe           runs-on: cloud · size: M
depends: SP5   touches: shared/avatar/ contracts/avatar-recipe.schema.json
behaviours: B-29
do:
1. Define the recipe JSON schema:
   - a parts tree: id, parent, pivot, primitive `roundedBox|capsule|cylinder|sphere`, size, cornerRadius,
     position, rotation, `colorRole`, tags;
   - spot rules (count, surfaces, size range, min spacing);
   - jitter ranges;
   - required anchors `head`, `eye`, `neck`, `spots[]`, `legs[]`.
2. Write `giraffe.json` from SP5, faithful to `docs/design/logo.png`.
3. Write `palettes.json` with 8 palettes; "Marcel" is the logo palette.
4. Add a Python validator test (schema, anchors present).
done when:
- [ ] The validator passes, and a recipe missing an anchor fails with a readable message.

### S-13.2  AvatarKit builder                                 runs-on: Mac · size: M
depends: S-13.1, S-12.1   touches: ios/AvatarKit/Sources/Builder/
do:
1. Seed = FNV-1a(name or UUID) → SplitMix64. Do not use Swift's `Hasher`.
2. The builder creates `ModelEntity` parts with `MeshResource.generateBox(…cornerRadius:)` etc. Materials are cached per
   colour role; each spot gets its own material instance.
3. Spot placement uses seeded Poisson-disc sampling.
4. A `RealityView` wrapper with a virtual camera, a transparent background and a fixed three-quarter view.
done when:
- [ ] Determinism test: the same input gives an identical entity-tree dump, and a different seed gives different spots.
- [ ] A snapshot image test for the Marcel palette.

### S-13.3  State machine and animation System               runs-on: Mac · size: M
depends: S-13.2   touches: ios/AvatarKit/Sources/Animation/
behaviours: B-28
do:
1. `AvatarState` comes from the hub's `agent.state`. A custom RealityKit `System` blends layered motion with
   critically damped springs:
   - idle: breathing and seeded blinking;
   - thinking: head tilt and ossicone wobble;
   - working(n): **the first n spots, in seeded order, glow and pulse**;
   - needs_you: a hop, the head turns to the camera, an orange pulse;
   - done: a nod clip.
2. Honour Reduce Motion. Pause when the view is off screen.
done when:
- [ ] Unit tests on the state → target-pose mapping.
- [ ] A recorded preview video for each state, attached to the PR for the owner's review.

### S-13.4  Avatar in the app and palette picker              runs-on: Mac · size: S
depends: S-13.3, S-12.2, S-12.7   touches: ios/Marcel/Chat/AvatarHeader.swift ios/Marcel/Settings/
do: The avatar header on the chat screen, about 120 pt and collapsing on scroll. The palette picker in Settings uses live
previews and saves to `PUT /agents/{id}`.
done when:
- [ ] UI test: the state changes from the mock hub show up in the header.
- [ ] The owner signs off on how it looks.
