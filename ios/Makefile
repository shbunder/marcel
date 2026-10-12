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
RUNNER  := $(BUILD)/runner/SimTestRunner
SIM     ?= $(shell DEVELOPER_DIR=$(DEVELOPER_DIR) xcrun simctl list devices available -j | python3 scripts/pick-simulator.py $(SDK_VER))
MOCK_HUB ?= http://127.0.0.1:7499/api

.PHONY: check app test tests-bundle avatar-tests-bundle pixelkit-test pixelkit-bundle pixelkit-record integration \
  chat-test chat-bundle chat-record chat-integration generate boot clean

check: app test pixelkit-test chat-test

# The app and its three packages, for the simulator.
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

# Regenerate MarcelKit/Sources/MarcelAPI from contracts/app-api.yaml after the contract changes.
generate:
	scripts/generate-api.sh

clean:
	rm -rf $(BUILD)
