# Welcome — getting into the app (S-17.5, B-39)

The app target has no test bundle (`project/lessons/ios-screen-packages.md`), so the welcome screen, onboarding, the
lock and signing out live in this package; `RootView` only routes between them.

- **Which screen** (`AppFlow`): the welcome screen until the phone is paired; then `GET /me`, and "Meet your helper"
  while the helper has no `onboarded_at`; then the chat. Once onboarded, the hub's address is remembered, so the next
  launch goes straight to the chat. Offline right after a launch, the chat shows what is saved and onboarding is asked
  again next time.
- **Welcome** (`WelcomeView`): the giraffe, "Scan the code from the person who runs Marcel at home", the camera
  (`QRScannerView`, moved here from the app target), a paste-a-link field with "Sign in", and "No code? Make one in
  Settings › Buddies on a phone that has Marcel." What went wrong pairing is said under the field. Onboarding is for
  the buddy the phone starts on (`CurrentBuddy`: a pairing link's `&buddy=`), else the first.
- **Meet your helper** (`OnboardingView`, `OnboardingModel`): "Hi Emma! Meet your helper", the helper's name, the
  look picker (MarcelLook, without its own Save; "Pick from my name" follows the name as typed), and "Start chatting":
  one `PATCH /agents/{id}` with the name, the look and `onboarded: true`.
- **The lock** (`AppLock`, `LockView`, `.appLock(_:paired:)`): Face ID, or the passcode, before the app opens. It
  shows in a window of its own above the app's (`LockWindow`), so no sheet (Settings, a quest) can sit on top of it,
  and VoiceOver reads nothing underneath. While the app is not in front, a plain cover hides it from the app
  switcher (`LockCover`). Time away is counted on a clock that changing the phone's time does not move. On by default
  (`lock.enabled`; Settings turns it off, S-17.6), asked at launch and again after five minutes in the background, not
  after four. Not straight after signing in, and never on a phone with no passcode. `Authenticator` and the clock are
  passed in, so tests use fakes.
- **Sign out of this phone** (`AppFlow.signOut`): `DELETE /devices/{this phone}`, then the sign-in (Keychain), the
  hub's address and the cache are forgotten (`HubConnection.unpair`) and the welcome screen shows. Offline, it still
  signs out and says the hub wasn't told. Settings' "Sign out of this phone" button (S-17.6) will call it; until
  then Settings' "Unpair" does the same through its own path. Either way the app forgets the chat's saved ids when
  the phone becomes unpaired (`RootView`).

## Tests (`make -C ios welcome-test`, part of `make ios-check`)

`WelcomeTests`: which screen when, onboarding (name trimmed, the look, a refusal), sign out online and offline, and
the lock with `FakeAuthenticator` and `FakeClock` (locks after five minutes, not after four; off; no passcode; not
after signing in). `WelcomeSnapshotTests`: "Welcome", "Meet-your-helper" and "Locked" in light, dark and the largest
text; re-record with `make -C ios welcome-record`.

`WelcomeMockHubTests` is the story's UI test one layer down (XCUITest cannot run on this Mac's Xcode): pair as Emma
with a code from the owner → "Meet your helper" → Start chatting → the chat; sign out → welcome, and the old token is
refused. On a fresh mock hub: `MOCK_DELAY=0 MOCK_HUB_PORT=7488 make mock-hub`, then
`MOCK_HUB=http://127.0.0.1:7488/api make -C ios welcome-integration`.
