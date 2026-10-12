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
RUNNER  := $(BUILD)/runner/SimTestRunner
SIM     ?= $(shell DEVELOPER_DIR=$(DEVELOPER_DIR) xcrun simctl list devices available -j | python3 scripts/pick-simulator.py $(SDK_VER))
MOCK_HUB ?= http://127.0.0.1:7499/api

.PHONY: check app test tests-bundle avatar-tests-bundle pixelkit-test pixelkit-bundle pixelkit-record integration \
  chat-test chat-bundle chat-record chat-integration quests-test quests-bundle quests-record quests-integration \
  map-test map-bundle map-record map-integration items-test items-bundle items-record items-integration quest-test \
  quest-bundle quest-record generate boot clean

check: app test pixelkit-test chat-test quests-test map-test items-test quest-test

# The app and its packages, for the simulator.
app:
	xcodebuild build -quiet -project Marcel.xcodeproj -target Marcel -sdk iphonesimulator -configuration Debug \
	  SYMROOT=$(BUILD)/xcode ARCHS=$(ARCH) SWIFT_ENABLE_EXPLICIT_MODULES=NO

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
chat-record: chat-bundle $(RUNNER) boot
	-SIMCTL_CHILD_MARCELCHAT_RECORD=1 xcrun simctl spawn $(SIM) $(RUNNER) $(CHAT_TESTS) ChatSnapshotTests
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

# Regenerate MarcelKit/Sources/MarcelAPI from contracts/app-api.yaml after the contract changes.
generate:
	scripts/generate-api.sh

clean:
	rm -rf $(BUILD)
