# Settings — settings, notifications and links (S-12.10)

Settings (B-18, B-19, B-22), the system status screen, and where every `marcel://` link and every push leads. Drawn
with PixelKit components. Settings opens from the avatar header in the chat (tap the giraffe,
then "Settings"; the same menu opens Marcel's notebook, `Marcel/Notebook`).

It is a Swift package (`MarcelSettings`) so its tests run in the simulator like the other packages'. It depends only on
MarcelKit and PixelKit. It does not show the screens a link leads to: it says where to go (`LinkDestination`), and the
app (`ios/Marcel/App/RootView.swift`) shows the quest view, the artifact viewer, a Map room or a chat message.

## Settings

| Section | Hub routes | What it does |
|---|---|---|
| Name | `GET /me`, `PATCH /agents/{id}` | Rename the agent (1–40 characters). Shows its brain and worker models. The name is passed on (`SettingsModel.nameChanged`) so the chat header follows a rename. |
| Notifications | `PUT /devices/{id}/push` | APNs when `/me` says the hub has it **and** this build can get a token; else an ntfy topic, with the steps to subscribe in the ntfy app (B-18). |
| Plan usage | `GET /usage` | The 5-hour and weekly meters, reset times, and the usage guard's own words while it holds work back. A 503 `no_reading` (the runner has not read usage since it started) says "Usage unavailable" as a plain note, not a problem. |
| Schedules | `GET /schedules`, `PATCH`, `DELETE /schedules/{id}` | Pause / resume any schedule; delete one after a confirmation. Built-in digests can only be paused. |
| Devices | `GET /devices`, `DELETE /devices/{id}` | Every paired device; remove another one (after a confirmation). **Unpair this phone** first revokes this phone on the hub (`DELETE /devices/{own id}`, so its token stops and pushes stop), forgets the ntfy topic, then forgets the hub. If the hub can't be told, the phone still unpairs and the pairing screen says so. |
| System | `GET /health` | Opens the status screen. |

Each section loads and fails on its own. A hub that answers **404, 405 or 501** for a route the contract has (schedules
before S-09.1, usage before S-10.1, renaming the agent) shows "Not available yet" in that section; the rest still work.
The hub answers a route it does not serve with 404 and its generic code `not_found` (405 `method_not_allowed` for a
method it does not take on that path). Where the contract lists a 404, that body decodes as the contract's `Error`, so
the code decides: `not_found` is "not built yet", a specific code (`device_not_found`, `message_not_found`, …) means
the thing is gone (`SettingsError.missing`). A bare 404 that does not decode is read as "not built yet" too
(`LiveSettingsService.call`).

**ntfy topic.** Made once per phone (`NtfyTopicStore`): `marcel-` and 24 random lower-case letters and digits. Anyone
who knows a topic can read what is sent to it, so it is random, the screen says to keep it private, **Copy** puts it on
this phone's clipboard only (never Universal Clipboard) for two minutes (`TopicClipboard`), and unpairing forgets it.

**APNs.** `APNsRegistrar` asks for permission, calls `registerForRemoteNotifications`, and waits for the app delegate to
pass on the token (20 s at most, per attempt: an earlier attempt's timer never fails a later one). The app has no `aps-environment` entitlement until there is an Apple developer
account, so today registration fails and Settings falls back to ntfy and says why. Once a phone uses APNs, the app asks
for the token again at each launch and sends a changed one to the hub.

## The status screen

`StatusView` shows `GET /health` (the hub, the NUC runner and the reason it is unreachable, Marcel's brain) and the last
alert: the `broke` push that opened the screen, else the last `broke` push this phone saw (`LastAlertStore`). `/health`
needs no token, so "Couldn't reach the hub" means the hub or the tunnel is down.

## Links

`DeepLink.parse` accepts only:

| Link | Goes to |
|---|---|
| `marcel://task/<id>` | the quest view |
| `marcel://message/<id>` | read with `GET /messages/{id}`: the Chat tab, scrolled to that message; a message in a quest's own thread opens that quest |
| `marcel://approval/<id>` | read with `GET /approvals/{id}` (answered or not): the quest that asks |
| `marcel://project/<slug>` | the project's room on the Map (the cache first, then `GET /projects`) |
| `marcel://artifact/<id>` | the item viewer |
| `marcel://status` | the status screen |

A message or approval the hub answers 404 `message_not_found` / `approval_not_found` for says so in plain words. A hub
from before app API 0.5 has neither read (404 `not_found`, or 405 for `GET /approvals/{id}`): the router then falls back to what it did before, the cache for a message (else the chat) and
`GET /approvals` for an approval.

Every link is untrusted (anything on the phone can open one): only the `marcel` scheme, one id of `[A-Za-z0-9_-]`
(at most 128) or a slug matching the contract's pattern, no query, fragment, user, port or percent-escape, at most 300
characters. **`marcel://pair?…` never pairs the phone**: a web page could otherwise pair it with a stranger's hub.
Anything else shows one plain sentence over the tabs (tap to dismiss) and opens nothing; nothing is sent to the hub.
A pairing link while the phone is unpaired is answered on the pairing screen ("scan the code on this screen"), and that
sentence goes away once the phone is paired. A link the hub could not look up says which: the hub refused this phone
(pair again), the hub had a problem (5xx), or the hub couldn't be reached.

**Cold start.** `AppDelegate` makes one `LinkRouter` and one `PushHandler` at launch and sets the handler as the
notification centre's delegate in `didFinishLaunching`, so a push that launches the app is delivered. A link or push that
arrives before the tabs are up waits in the router (the newest one wins). When the tabs appear they `attach` the router
to the paired hub (`LiveLinkLookup`), which looks the waiting link up and shows it. Unpairing `detach`es it again.

## Pushes

`PushCategories.make()` registers every category in the contract's `PushNotification.category` (`approval`, `task`,
`broke`, `digest`, `note`). Only `approval` has actions: **Approve** and **Deny**, which need an unlocked phone and do
not open the app; `PushHandler` answers with `POST /approvals/{id}` using this phone's token. The answerer is made at
launch (`PushHandler.answering`) and reads the saved hub address and the Keychain token when the action comes in, because
iOS may start the app in the background just for the action, and then no screen ever loads. If that fails (answered in
the terminal first, not paired, no id) the phone shows a local notification that says so. A tap on any push opens its
`deep_link`. A push that arrives while the app is open still shows as a banner.

## Tests (`make -C ios settings-test`, part of `make ios-check`)

- `LinkRoutingTests` — the story's test: every link (task, message in the chat, message in a quest's thread, a message
  not on the phone yet, open and answered approval, cached and uncached project, artifact, status) routes correctly **from a cold start**
  (delivered before the router is attached, as at launch) and **while running**, both as a link and as a tapped push.
  XCUITest cannot run on this Mac (no iOS 26.5 platform, see `ios/README.md`), so these drive the router and push
  handler exactly as `AppDelegate` and `MainTabs` wire them, with a fake hub behind the real generated client and a
  real in-memory cache. Also: messages and approvals read by id (and the fallback on an older hub), bad links open
  nothing and never reach the hub, links to things that are gone, Approve and Deny, every contract category registered.
- `SettingsReviewFixTests` and the review tests in `LinkRoutingTests` — Approve from the lock screen with the app not
  running; unpairing tells the hub (and still unpairs offline); 5xx, 401 and offline said apart; the topic copied to
  this phone only; an APNs timeout failing only its own attempt; a pairing link on the pairing screen.
- `DeepLinkTests` — every kind, and malformed, foreign and oversized links refused.
- `SettingsModelTests` — each section against the fake hub; "not available yet" on 404 / 405 / 501; rename; APNs, the
  ntfy fallback, refused permission; pause, delete, built-in digests; revoking a device; the words each section shows;
  "usage unavailable" on 503 `no_reading`; the name passed on for the chat header.
- `SettingsSnapshotTests` — Settings (everything there; a hub with no schedules or usage yet; no usage reading yet) and
  the status screen, in
  light and dark, pixel for pixel. `make -C ios settings-record` re-records them after a deliberate change.
