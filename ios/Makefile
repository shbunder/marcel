# The iOS lane. Run on the Mac: `make ios-check` from the repo root, or `make -C ios check`.
#
# Why not `xcodebuild test`: on this Mac's Xcode the iOS platform component is not installed, so
# xcodebuild lists no simulator as a destination for any scheme ("iOS 26.5 is not installed"). This lane
# needs no destination: it builds the app with `-target … -sdk iphonesimulator`, builds MarcelKit's,
# AvatarKit's and PixelKit's tests with SwiftPM for the simulator, and runs them on a booted simulator
# through a small runner that carries the Keychain entitlement an app has. See README.md.

DEVELOPER_DIR ?= /Applications/Xcode.app/Contents/Developer
export DEVELOPER_DIR

BUILD   := $(CURDIR)/.build
ARCH    := $(shell uname -m)
SDK     := $(shell DEVELOPER_DIR=$(DEVELOPER_DIR) xcrun --sdk iphonesimulator --show-sdk-path)
SDK_VER := $(shell DEVELOPER_DIR=$(DEVELOPER_DIR) xcrun --sdk iphonesimulator --show-sdk-version)
PLAT    := $(DEVELOPER_DIR)/Platforms/iPhoneSimulator.platform/Developer
TRIPLE  := $(ARCH)-apple-ios18.0-simulator
TESTS   := $(BUILD)/spm-sim/$(ARCH)-apple-ios-simulator/debug/MarcelKitPackageTests.xctest
AVATAR_TESTS := $(BUILD)/spm-avatar/$(ARCH)-apple-ios-simulator/debug/AvatarKitPackageTests.xctest
PIXEL_TESTS := $(BUILD)/pixelkit-sim/$(ARCH)-apple-ios-simulator/debug/PixelKitPackageTests.xctest
CHAT_TESTS := $(BUILD)/chat-sim/$(ARCH)-apple-ios-simulator/debug/MarcelChatPackageTests.xctest
QUESTS_TESTS := $(BUILD)/quests-sim/$(ARCH)-apple-ios-simulator/debug/MarcelQuestsPackageTests.xctest
MAP_TESTS := $(BUILD)/map-sim/$(ARCH)-apple-ios-simulator/debug/MarcelMapPackageTests.xctest
ITEMS_TESTS := $(BUILD)/items-sim/$(ARCH)-apple-ios-simulator/debug/MarcelItemsPackageTests.xctest
QUEST_TESTS := $(BUILD)/quest-sim/$(ARCH)-apple-ios-simulator/debug/MarcelQuestPackageTests.xctest
SETTINGS_TESTS := $(BUILD)/settings-sim/$(ARCH)-apple-ios-simulator/debug/MarcelSettingsPackageTests.xctest
NOTEBOOK_TESTS := $(BUILD)/notebook-sim/$(ARCH)-apple-ios-simulator/debug/MarcelNotebookPackageTests.xctest
LOOK_TESTS := $(BUILD)/look-sim/$(ARCH)-apple-ios-simulator/debug/MarcelLookPackageTests.xctest
WELCOME_TESTS := $(BUILD)/welcome-sim/$(ARCH)-apple-ios-simulator/debug/MarcelWelcomePackageTests.xctest
RUNNER  := $(BUILD)/runner/SimTestRunner
SIM     ?= $(shell DEVELOPER_DIR=$(DEVELOPER_DIR) xcrun simctl list devices available -j | python3 scripts/pick-simulator.py $(SDK_VER))
MOCK_HUB ?= http://127.0.0.1:7499/api
APP_PATH := $(BUILD)/xcode/Debug-iphonesimulator/Marcel.app
BUNDLE_ID := com.shbunder.marcel
PREVIEW := Marcel Preview
MOCK_STARTED := $(BUILD)/mock-hub.started
PAIR_LINK := marcel://pair?hub=http%3A%2F%2F127.0.0.1%3A7499%2Fapi&code=K7Q2M9

.PHONY: check app test tests-bundle avatar-tests-bundle pixelkit-test pixelkit-bundle pixelkit-record integration \
  chat-test chat-bundle chat-record chat-integration quests-test quests-bundle quests-record quests-integration \
  map-test map-bundle map-record map-integration items-test items-bundle items-record items-integration quest-test \
  quest-bundle quest-record settings-test settings-bundle settings-record settings-integration notebook-test notebook-bundle notebook-record \
  look-test look-bundle look-record welcome-test welcome-bundle welcome-record welcome-integration generate boot run run-mock stop clean

check: app test pixelkit-test chat-test quests-test map-test items-test quest-test settings-test notebook-test look-test \
  welcome-test

# The app and its packages, for the simulator.
app:
	xcodebuild build -quiet -project Marcel.xcodeproj -target Marcel -sdk iphonesimulator -configuration Debug \
	  SYMROOT=$(BUILD)/xcode ARCHS=$(ARCH) SWIFT_ENABLE_EXPLICIT_MODULES=NO

# Try the app: build it, boot the "Marcel Preview" simulator (created on first use, never the one the tests use),
# open Simulator, install and launch. Xcode's Run button needs the iOS platform component the gate works around.
run: app
	@udid=$$(python3 scripts/preview-simulator.py $(SDK_VER)) && echo "Simulator: $(PREVIEW) ($$udid)" && \
	  xcrun simctl bootstatus $$udid -b >/dev/null && open -a Simulator --args -CurrentDeviceUDID $$udid && \
	  xcrun simctl install $$udid $(APP_PATH) && xcrun simctl launch $$udid $(BUNDLE_ID)

# The same, against the mock hub: starts it in the background unless it already answers, then puts the pairing link
# on the simulator's clipboard. Paste it on the pairing screen and tap Pair: the app never pairs from an opened
# marcel://pair link (Marcel/Settings/README.md). `make -C ios stop` shuts both down.
run-mock:
	@mkdir -p $(BUILD)
	@if curl -sf --max-time 2 $(MOCK_HUB)/health >/dev/null; then echo "Mock hub: already running"; else \
	  echo "Mock hub: starting (log in $(BUILD)/mock-hub.log)"; touch $(MOCK_STARTED); \
	  nohup $(MAKE) -C .. mock-hub >$(BUILD)/mock-hub.log 2>&1 & \
	  for i in $$(seq 240); do curl -sf --max-time 2 $(MOCK_HUB)/health >/dev/null && break; sleep 0.5; done; \
	  curl -sf --max-time 2 $(MOCK_HUB)/health >/dev/null || { echo "Mock hub did not start; see $(BUILD)/mock-hub.log"; exit 1; }; fi
	@$(MAKE) --no-print-directory run
	@printf '%s' '$(PAIR_LINK)' | xcrun simctl pbcopy "$(PREVIEW)"
	@echo "Pairing link copied to the simulator: paste it on the pairing screen and tap Pair."

# Shut down the preview simulator, and the mock hub if run-mock started it (whatever listens on 7499 now).
stop:
	-xcrun simctl shutdown "$(PREVIEW)"
	@if [ -f $(MOCK_STARTED) ]; then pids=$$(lsof -ti tcp:7499 -sTCP:LISTEN); [ -z "$$pids" ] || kill $$pids; \
	  rm -f $(MOCK_STARTED); echo "Mock hub: stopped"; fi

$(RUNNER): Tools/SimTestRunner/main.swift Tools/SimTestRunner/entitlements.plist
	@mkdir -p $(dir $@)
	SDKROOT=$(SDK) xcrun --sdk iphonesimulator swiftc -target $(TRIPLE) -sdk $(SDK) \
	  -F $(PLAT)/Library/Frameworks -I $(PLAT)/usr/lib -L $(PLAT)/usr/lib \
	  -Xlinker -rpath -Xlinker $(PLAT)/Library/Frameworks -Xlinker -rpath -Xlinker $(PLAT)/Library/PrivateFrameworks \
	  -Xlinker -rpath -Xlinker $(PLAT)/usr/lib \
	  -Xlinker -sectcreate -Xlinker __TEXT -Xlinker __entitlements -Xlinker Tools/SimTestRunner/entitlements.plist \
	  Tools/SimTestRunner/main.swift -o $@
	codesign --force -s - $@

tests-bundle:
	swift build --quiet --package-path MarcelKit --scratch-path $(BUILD)/spm-sim --build-tests \
	  --triple $(TRIPLE) --sdk $(SDK) -Xswiftc -F -Xswiftc $(PLAT)/Library/Frameworks \
	  -Xlinker -F -Xlinker $(PLAT)/Library/Frameworks

# AvatarKit's tests (sprite timing + SwiftUI snapshots), built the same way in their own scratch path.
avatar-tests-bundle:
	swift build --quiet --package-path AvatarKit --scratch-path $(BUILD)/spm-avatar --build-tests \
	  --triple $(TRIPLE) --sdk $(SDK) -Xswiftc -F -Xswiftc $(PLAT)/Library/Frameworks \
	  -Xlinker -F -Xlinker $(PLAT)/Library/Frameworks

boot:
	@echo "Simulator: $(SIM)"
	@xcrun simctl bootstatus $(SIM) -b >/dev/null

# MarcelKit's and AvatarKit's unit tests on the simulator. They use stubs; nothing reaches the network.
test: tests-bundle avatar-tests-bundle $(RUNNER) boot
	xcrun simctl spawn $(SIM) $(RUNNER) $(TESTS)
	xcrun simctl spawn $(SIM) $(RUNNER) $(AVATAR_TESTS)

# PixelKit's tests on the simulator: snapshots of every component (light, dark, largest text), contrast and
# accessibility checks. Built separately so MarcelKit's package graph stays as it is.
pixelkit-bundle:
	swift build --quiet --package-path PixelKit --scratch-path $(BUILD)/pixelkit-sim --build-tests \
	  --triple $(TRIPLE) --sdk $(SDK) -Xswiftc -F -Xswiftc $(PLAT)/Library/Frameworks \
	  -Xlinker -F -Xlinker $(PLAT)/Library/Frameworks

pixelkit-test: pixelkit-bundle $(RUNNER) boot
	xcrun simctl spawn $(SIM) $(RUNNER) $(PIXEL_TESTS)

# Re-record PixelKit's reference snapshots after a deliberate change (the recording run fails by design), then
# check them. Look at the PNGs in PixelKit/Tests/PixelKitTests/__Snapshots__ before committing them.
pixelkit-record: pixelkit-bundle $(RUNNER) boot
	-SIMCTL_CHILD_PIXELKIT_RECORD=1 xcrun simctl spawn $(SIM) $(RUNNER) $(PIXEL_TESTS) ComponentSnapshotTests
	xcrun simctl spawn $(SIM) $(RUNNER) $(PIXEL_TESTS)

# The Chat tab's tests (Marcel/Chat, the MarcelChat package): the feed, its cards, the composer, and snapshots of
# the screen in light and dark, compared with Marcel/Chat/Tests/MarcelChatTests/__Snapshots__.
chat-bundle:
	swift build --quiet --package-path Marcel/Chat --scratch-path $(BUILD)/chat-sim --build-tests \
	  --triple $(TRIPLE) --sdk $(SDK) -Xswiftc -F -Xswiftc $(PLAT)/Library/Frameworks \
	  -Xlinker -F -Xlinker $(PLAT)/Library/Frameworks

chat-test: chat-bundle $(RUNNER) boot
	xcrun simctl spawn $(SIM) $(RUNNER) $(CHAT_TESTS)

# Re-record the Chat snapshots after a deliberate change (the recording run fails by design), then check them.
# This also redraws the avatar header's snapshots. (The app icon is drawn by shared/brand/app-icon/generate.py.)
chat-record: chat-bundle $(RUNNER) boot
	-SIMCTL_CHILD_MARCELCHAT_RECORD=1 xcrun simctl spawn $(SIM) $(RUNNER) $(CHAT_TESTS) ChatSnapshotTests \
	  AvatarHeaderSnapshotTests ChatLookSnapshotTests SideChatSnapshotTests QuoteAndMentionTests \
	  OwnAvatarTests
	xcrun simctl spawn $(SIM) $(RUNNER) $(CHAT_TESTS)

# The chat's scenario against the mock hub (start it first: MOCK_HUB_PORT=7488 make mock-hub). Not part of the gate.
chat-integration: chat-bundle $(RUNNER) boot
	SIMCTL_CHILD_MARCEL_MOCK_HUB=$(MOCK_HUB) xcrun simctl spawn $(SIM) $(RUNNER) $(CHAT_TESTS) ChatMockHubTests

# Against the mock hub (start it first with `make mock-hub` from the repo root). Not part of the gate.
integration: tests-bundle $(RUNNER) boot
	SIMCTL_CHILD_MARCEL_MOCK_HUB=$(MOCK_HUB) xcrun simctl spawn $(SIM) $(RUNNER) $(TESTS) MockHubIntegrationTests

# The Quests tab's tests (Marcel/Quests, the MarcelQuests package): the grouping and ordering of the quest log, stop
# and archive, the live stream moving cards, and snapshots compared with Marcel/Quests/Tests/MarcelQuestsTests/__Snapshots__.
quests-bundle:
	swift build --quiet --package-path Marcel/Quests --scratch-path $(BUILD)/quests-sim --build-tests \
	  --triple $(TRIPLE) --sdk $(SDK) -Xswiftc -F -Xswiftc $(PLAT)/Library/Frameworks \
	  -Xlinker -F -Xlinker $(PLAT)/Library/Frameworks

quests-test: quests-bundle $(RUNNER) boot
	xcrun simctl spawn $(SIM) $(RUNNER) $(QUESTS_TESTS)

# Re-record the Quests snapshots after a deliberate change (the recording run fails by design), then check them.
quests-record: quests-bundle $(RUNNER) boot
	-SIMCTL_CHILD_MARCELQUESTS_RECORD=1 xcrun simctl spawn $(SIM) $(RUNNER) $(QUESTS_TESTS) QuestSnapshotTests
	xcrun simctl spawn $(SIM) $(RUNNER) $(QUESTS_TESTS)

# The quest log against the mock hub (start it first: MOCK_HUB_PORT=7488 make mock-hub). Not part of the gate.
quests-integration: quests-bundle $(RUNNER) boot
	SIMCTL_CHILD_MARCEL_MOCK_HUB=$(MOCK_HUB) xcrun simctl spawn $(SIM) $(RUNNER) $(QUESTS_TESTS) QuestsMockHubTests

# The Map tab's tests (Marcel/Map, the MarcelMap package): the tiles and rooms, moving a quest, a room's messages,
# the hub's answers in plain words, and snapshots compared with Marcel/Map/Tests/MarcelMapTests/__Snapshots__.
map-bundle:
	swift build --quiet --package-path Marcel/Map --scratch-path $(BUILD)/map-sim --build-tests \
	  --triple $(TRIPLE) --sdk $(SDK) -Xswiftc -F -Xswiftc $(PLAT)/Library/Frameworks \
	  -Xlinker -F -Xlinker $(PLAT)/Library/Frameworks

map-test: map-bundle $(RUNNER) boot
	xcrun simctl spawn $(SIM) $(RUNNER) $(MAP_TESTS)

# Re-record the Map snapshots after a deliberate change (the recording run fails by design), then check them.
map-record: map-bundle $(RUNNER) boot
	-SIMCTL_CHILD_MARCELMAP_RECORD=1 xcrun simctl spawn $(SIM) $(RUNNER) $(MAP_TESTS) MapSnapshotTests
	xcrun simctl spawn $(SIM) $(RUNNER) $(MAP_TESTS)

# The map and a room against the mock hub (start it first: MOCK_HUB_PORT=7488 make mock-hub). Not part of the gate.
map-integration: map-bundle $(RUNNER) boot
	SIMCTL_CHILD_MARCEL_MOCK_HUB=$(MOCK_HUB) xcrun simctl spawn $(SIM) $(RUNNER) $(MAP_TESTS) MapMockHubTests

# The Items tab and the artifact viewer (Marcel/Items, the MarcelItems package): the grid by project, every kind of
# item, versions, "Ask about this", the web view's rules, and snapshots compared with
# Marcel/Items/Tests/MarcelItemsTests/__Snapshots__.
items-bundle:
	swift build --quiet --package-path Marcel/Items --scratch-path $(BUILD)/items-sim --build-tests \
	  --triple $(TRIPLE) --sdk $(SDK) -Xswiftc -F -Xswiftc $(PLAT)/Library/Frameworks \
	  -Xlinker -F -Xlinker $(PLAT)/Library/Frameworks

items-test: items-bundle $(RUNNER) boot
	xcrun simctl spawn $(SIM) $(RUNNER) $(ITEMS_TESTS)

# Re-record the Items snapshots after a deliberate change (the recording run fails by design), then check them.
items-record: items-bundle $(RUNNER) boot
	-SIMCTL_CHILD_MARCELITEMS_RECORD=1 xcrun simctl spawn $(SIM) $(RUNNER) $(ITEMS_TESTS) ItemsSnapshotTests
	xcrun simctl spawn $(SIM) $(RUNNER) $(ITEMS_TESTS)

# Items and the viewer against the mock hub (start it first: MOCK_HUB_PORT=7488 make mock-hub). Not part of the gate.
items-integration: items-bundle $(RUNNER) boot
	SIMCTL_CHILD_MARCEL_MOCK_HUB=$(MOCK_HUB) xcrun simctl spawn $(SIM) $(RUNNER) $(ITEMS_TESTS) ItemsMockHubTests

# The quest view (Marcel/Quest, the MarcelQuest package): steps, the transcript merged from history and the live
# stream, every event kind, steering and approvals, the toolbar, 5,000 events in under a second, and snapshots
# compared with Marcel/Quest/Tests/MarcelQuestTests/__Snapshots__.
quest-bundle:
	swift build --quiet --package-path Marcel/Quest --scratch-path $(BUILD)/quest-sim --build-tests \
	  --triple $(TRIPLE) --sdk $(SDK) -Xswiftc -F -Xswiftc $(PLAT)/Library/Frameworks \
	  -Xlinker -F -Xlinker $(PLAT)/Library/Frameworks

quest-test: quest-bundle $(RUNNER) boot
	xcrun simctl spawn $(SIM) $(RUNNER) $(QUEST_TESTS)

# Re-record the quest view's snapshots after a deliberate change (the recording run fails by design), then check them.
quest-record: quest-bundle $(RUNNER) boot
	-SIMCTL_CHILD_MARCELQUEST_RECORD=1 xcrun simctl spawn $(SIM) $(RUNNER) $(QUEST_TESTS) QuestSnapshotTests
	xcrun simctl spawn $(SIM) $(RUNNER) $(QUEST_TESTS)

# Settings and links (Marcel/Settings, the MarcelSettings package): every `marcel://` link and push routed from a
# cold start and while running, the push categories, Settings' sections against a fake hub, and snapshots compared
# with Marcel/Settings/Tests/MarcelSettingsTests/__Snapshots__.
settings-bundle:
	swift build --quiet --package-path Marcel/Settings --scratch-path $(BUILD)/settings-sim --build-tests \
	  --triple $(TRIPLE) --sdk $(SDK) -Xswiftc -F -Xswiftc $(PLAT)/Library/Frameworks \
	  -Xlinker -F -Xlinker $(PLAT)/Library/Frameworks

settings-test: settings-bundle $(RUNNER) boot
	xcrun simctl spawn $(SIM) $(RUNNER) $(SETTINGS_TESTS)

# People against the mock hub: add a person (a QR), their phone sees no People, remove them. Start a mock hub first:
# `MOCK_DELAY=0 MOCK_HUB_PORT=7488 make mock-hub`, then `MOCK_HUB=http://127.0.0.1:7488/api make -C ios settings-integration`.
settings-integration: settings-bundle $(RUNNER) boot
	SIMCTL_CHILD_MARCEL_MOCK_HUB=$(MOCK_HUB) xcrun simctl spawn $(SIM) $(RUNNER) $(SETTINGS_TESTS) PeopleMockHubTests

# Re-record the Settings snapshots after a deliberate change (the recording run fails by design), then check them.
settings-record: settings-bundle $(RUNNER) boot
	-SIMCTL_CHILD_MARCELSETTINGS_RECORD=1 xcrun simctl spawn $(SIM) $(RUNNER) $(SETTINGS_TESTS) SettingsSnapshotTests PeopleTests
	xcrun simctl spawn $(SIM) $(RUNNER) $(SETTINGS_TESTS)

# Marcel's notebook (Marcel/Notebook, the MarcelNotebook package): the memory files by section, the editor's save,
# draft and "changed elsewhere" paths against a fake hub, untrusted paths, the rollover card, and snapshots compared
# with Marcel/Notebook/Tests/MarcelNotebookTests/__Snapshots__.
notebook-bundle:
	swift build --quiet --package-path Marcel/Notebook --scratch-path $(BUILD)/notebook-sim --build-tests \
	  --triple $(TRIPLE) --sdk $(SDK) -Xswiftc -F -Xswiftc $(PLAT)/Library/Frameworks \
	  -Xlinker -F -Xlinker $(PLAT)/Library/Frameworks

notebook-test: notebook-bundle $(RUNNER) boot
	xcrun simctl spawn $(SIM) $(RUNNER) $(NOTEBOOK_TESTS)

# Re-record the notebook's snapshots after a deliberate change (the recording run fails by design), then check them.
notebook-record: notebook-bundle $(RUNNER) boot
	-SIMCTL_CHILD_MARCELNOTEBOOK_RECORD=1 xcrun simctl spawn $(SIM) $(RUNNER) $(NOTEBOOK_TESTS) NotebookSnapshotTests
	xcrun simctl spawn $(SIM) $(RUNNER) $(NOTEBOOK_TESTS)

# The look picker (Marcel/Look, the MarcelLook package): "Pick from my name" against the Pixel Zoo's own picks,
# choosing and saving against a fake hub, previews, and snapshots compared with
# Marcel/Look/Tests/MarcelLookTests/__Snapshots__.
look-bundle:
	swift build --quiet --package-path Marcel/Look --scratch-path $(BUILD)/look-sim --build-tests \
	  --triple $(TRIPLE) --sdk $(SDK) -Xswiftc -F -Xswiftc $(PLAT)/Library/Frameworks \
	  -Xlinker -F -Xlinker $(PLAT)/Library/Frameworks

look-test: look-bundle $(RUNNER) boot
	xcrun simctl spawn $(SIM) $(RUNNER) $(LOOK_TESTS)

# Re-record the look picker's snapshots after a deliberate change (the recording run fails by design), then check.
look-record: look-bundle $(RUNNER) boot
	-SIMCTL_CHILD_MARCELLOOK_RECORD=1 xcrun simctl spawn $(SIM) $(RUNNER) $(LOOK_TESTS) LookTests
	xcrun simctl spawn $(SIM) $(RUNNER) $(LOOK_TESTS)

# Getting in (Marcel/Welcome, the MarcelWelcome package): the welcome screen, onboarding, the Face ID lock with a
# fake authenticator and clock, signing out, and snapshots compared with
# Marcel/Welcome/Tests/MarcelWelcomeTests/__Snapshots__.
welcome-bundle:
	swift build --quiet --package-path Marcel/Welcome --scratch-path $(BUILD)/welcome-sim --build-tests \
	  --triple $(TRIPLE) --sdk $(SDK) -Xswiftc -F -Xswiftc $(PLAT)/Library/Frameworks \
	  -Xlinker -F -Xlinker $(PLAT)/Library/Frameworks

welcome-test: welcome-bundle $(RUNNER) boot
	xcrun simctl spawn $(SIM) $(RUNNER) $(WELCOME_TESTS)

# Re-record the welcome screens' snapshots after a deliberate change (the recording run fails by design), then check.
welcome-record: welcome-bundle $(RUNNER) boot
	-SIMCTL_CHILD_MARCELWELCOME_RECORD=1 xcrun simctl spawn $(SIM) $(RUNNER) $(WELCOME_TESTS) WelcomeSnapshotTests
	xcrun simctl spawn $(SIM) $(RUNNER) $(WELCOME_TESTS)

# Pair as Emma on the mock hub → onboarding → chat; sign out → welcome. Start a fresh hub first:
# `MOCK_DELAY=0 MOCK_HUB_PORT=7488 make mock-hub`, then `MOCK_HUB=http://127.0.0.1:7488/api make -C ios welcome-integration`.
welcome-integration: welcome-bundle $(RUNNER) boot
	SIMCTL_CHILD_MARCEL_MOCK_HUB=$(MOCK_HUB) xcrun simctl spawn $(SIM) $(RUNNER) $(WELCOME_TESTS) WelcomeMockHubTests

# Regenerate MarcelKit/Sources/MarcelAPI from contracts/app-api.yaml after the contract changes.
generate:
	scripts/generate-api.sh

clean:
	rm -rf $(BUILD)
