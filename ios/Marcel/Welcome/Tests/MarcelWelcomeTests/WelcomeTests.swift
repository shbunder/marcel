import Foundation
import SwiftUI
import UIKit
import MarcelKit
import XCTest

@testable import MarcelWelcome

@MainActor
final class WelcomeTests: XCTestCase {
    static let hub = PairedHub(serverURL: URL(string: "http://hub.test/api")!, deviceId: "dev_emma", userId: "usr_emma")

    private var defaults: UserDefaults!

    override func setUp() async throws {
        defaults = UserDefaults(suiteName: "welcome-\(UUID().uuidString)")
    }

    private func connection(paired: Bool) throws -> HubConnection {
        HubConnection(tokenStore: MemoryTokens(paired ? "tok" : nil), hubStore: MemoryHubs(paired ? Self.hub : nil),
                      cache: MarcelCache(container: try MarcelCache.makeContainer(inMemory: true)),
                      transport: HubClient.transport, connectorFactory: { _ in NoSocket() })
    }

    private func flow(_ connection: HubConnection, _ hub: FakeWelcomeService) -> AppFlow {
        AppFlow(connection: connection, defaults: defaults, makeService: { _ in hub }, makeLookService: { _ in NoPreviews() })
    }

    // MARK: Which screen

    func testAnUnpairedPhoneShowsTheWelcomeScreen() async throws {
        let flow = flow(try connection(paired: false), FakeWelcomeService())
        await flow.update()
        XCTAssertEqual(flow.screen, .welcome)
    }

    func testAHelperNotOnboardedYetMeetsYouThenTheChatOpens() async throws {
        let hub = FakeWelcomeService()
        let flow = flow(try connection(paired: true), hub)
        XCTAssertEqual(flow.screen, .checking)
        await flow.update()
        XCTAssertEqual(flow.screen, .onboarding)
        let onboarding = try XCTUnwrap(flow.onboarding)
        XCTAssertEqual(onboarding.greeting, "Hi Emma! Meet your helper")
        XCTAssertEqual(onboarding.helperName, "Pip")
        XCTAssertEqual(onboarding.look.saved, KnownLook(animal: .fox, color: .sky, personality: .cozy))

        onboarding.helperName = "  Pippa "
        onboarding.look.choose(animal: .frog)
        XCTAssertEqual(onboarding.look.name, "  Pippa ", "Pick from my name follows the name as typed")
        let started = await onboarding.startChatting()
        XCTAssertTrue(started)
        XCTAssertEqual(hub.finished.first?.name, "Pippa", "trimmed")
        XCTAssertEqual(hub.finished.first?.look.animal, .frog)
        flow.finishedOnboarding()
        XCTAssertEqual(flow.screen, .main)

        // Next launch: straight to the chat, without asking the hub.
        let next = self.flow(try connection(paired: true), hub)
        XCTAssertEqual(next.screen, .main)
    }

    func testAnOnboardedHelperGoesStraightToTheChat() async throws {
        let hub = FakeWelcomeService()
        hub.onboarded = true
        let flow = flow(try connection(paired: true), hub)
        await flow.update()
        XCTAssertEqual(flow.screen, .main)
    }

    func testOfflineAfterALaunchShowsTheChatAndAsksAgainNextTime() async throws {
        let hub = FakeWelcomeService()
        hub.offline = true
        let flow = flow(try connection(paired: true), hub)
        await flow.update()
        XCTAssertEqual(flow.screen, .main, "the chat shows what is saved")
        hub.offline = false
        let next = self.flow(try connection(paired: true), hub)
        await next.update()
        XCTAssertEqual(next.screen, .onboarding, "not remembered as onboarded")
    }

    func testStartChattingNeedsANameAndSaysWhyTheHubRefused() async throws {
        let hub = FakeWelcomeService()
        let flow = flow(try connection(paired: true), hub)
        await flow.update()
        let onboarding = try XCTUnwrap(flow.onboarding)
        onboarding.helperName = "   "
        XCTAssertFalse(onboarding.canStart)
        let empty = await onboarding.startChatting()
        XCTAssertFalse(empty)
        XCTAssertEqual(onboarding.problem, "Give your helper a name first.")
        onboarding.helperName = "Pip"
        hub.refuse = "That name is too long."
        let refused = await onboarding.startChatting()
        XCTAssertFalse(refused)
        XCTAssertEqual(onboarding.problem, "That name is too long.")
        XCTAssertEqual(flow.screen, .onboarding)
    }

    // MARK: Sign out

    func testSignOutTellsTheHubForgetsEverythingAndShowsWelcome() async throws {
        let hub = FakeWelcomeService()
        hub.onboarded = true
        let connection = try connection(paired: true)
        let flow = flow(connection, hub)
        await flow.update()
        XCTAssertEqual(flow.screen, .main)
        let note = await flow.signOut()
        XCTAssertNil(note)
        XCTAssertEqual(hub.signedOut, ["dev_emma"], "DELETE /devices/{this phone}")
        XCTAssertEqual(connection.phase, .unpaired)
        XCTAssertNil(try connection.tokenStore.token(), "the keychain is cleared")
        XCTAssertNil(connection.hubStore.load())
        XCTAssertEqual(flow.screen, .welcome)
        XCTAssertNil(defaults.string(forKey: AppFlow.onboardedKey))
    }

    func testSignOutOfflineStillSignsOutAndSaysTheHubWasNotTold() async throws {
        let hub = FakeWelcomeService()
        hub.onboarded = true
        let connection = try connection(paired: true)
        let flow = flow(connection, hub)
        await flow.update()
        hub.offline = true
        let note = await flow.signOut()
        XCTAssertEqual(connection.phase, .unpaired)
        XCTAssertEqual(flow.screen, .welcome)
        XCTAssertTrue(note?.contains("the hub wasn't told") == true, note ?? "")
    }

    // MARK: The lock

    private func lock(_ face: FakeAuthenticator, _ clock: FakeClock) -> AppLock {
        AppLock(authenticator: face, now: { clock.now }, defaults: defaults)
    }

    func testTheLockIsOnByDefaultAndFaceIDOpensIt() async {
        let face = FakeAuthenticator()
        let lock = lock(face, FakeClock())
        XCTAssertTrue(lock.enabled)
        XCTAssertTrue(lock.locked, "asked at launch")
        face.answer = false
        await lock.unlock()
        XCTAssertTrue(lock.locked)
        XCTAssertEqual(lock.problem, "Not unlocked. Try again.")
        face.answer = true
        await lock.unlock()
        XCTAssertFalse(lock.locked)
        XCTAssertNil(lock.problem)
    }

    func testItLocksAfterFiveMinutesAwayNotAfterFour() async {
        let face = FakeAuthenticator()
        let clock = FakeClock()
        let lock = lock(face, clock)
        await lock.unlock()

        lock.wentAway()
        clock.advance(minutes: 4)
        lock.cameBack()
        XCTAssertFalse(lock.locked, "four minutes: still open")

        lock.wentAway()
        clock.advance(minutes: 4.99)
        lock.cameBack()
        XCTAssertFalse(lock.locked, "just under five: still open")

        lock.wentAway()
        clock.advance(minutes: 5)
        lock.cameBack()
        XCTAssertTrue(lock.locked, "five minutes: locked")
    }

    func testTurnedOffOrWithoutAPasscodeItNeverLocks() async {
        let clock = FakeClock()
        let face = FakeAuthenticator()
        let off = lock(face, clock)
        off.enabled = false
        XCTAssertFalse(off.locked, "turning it off opens it")
        off.wentAway()
        clock.advance(minutes: 30)
        off.cameBack()
        XCTAssertFalse(off.locked)
        XCTAssertFalse(lock(face, clock).locked, "and it stays off next launch")

        defaults.removeObject(forKey: AppLock.enabledKey)
        let noPasscode = FakeAuthenticator()
        noPasscode.available = false
        XCTAssertFalse(lock(noPasscode, clock).locked, "a phone with no passcode has nothing to lock with")
    }

    // MARK: What covers the app

    func testTheLockCoversTheAppOnlyWhilePairedAndAPlainCoverHidesItFromTheAppSwitcher() {
        XCTAssertEqual(LockCover.of(locked: true, paired: true, phase: .active), .lock)
        XCTAssertEqual(LockCover.of(locked: true, paired: true, phase: .background), .lock)
        XCTAssertEqual(LockCover.of(locked: false, paired: true, phase: .active), .none)
        XCTAssertEqual(LockCover.of(locked: false, paired: true, phase: .inactive), .privacy, "the app switcher")
        XCTAssertEqual(LockCover.of(locked: false, paired: true, phase: .background), .privacy)
        XCTAssertEqual(LockCover.of(locked: true, paired: false, phase: .active), .none, "welcome has nothing to hide")
    }

    /// The lock is a window above the app's own, so a sheet the app shows (Settings, a quest) cannot cover it.
    func testTheLockIsAWindowAboveEverySheet() {
        let lock = lock(FakeAuthenticator(), FakeClock())
        let windows = LockWindow()
        windows.show(.lock, lock: lock)
        let window = windows.window
        XCTAssertNotNil(window)
        XCTAssertEqual(window?.isHidden, false)
        XCTAssertGreaterThan(window?.windowLevel.rawValue ?? 0, UIWindow.Level.alert.rawValue,
                             "above the app's window and every sheet in it")
        XCTAssertEqual(window?.accessibilityViewIsModal, true, "VoiceOver stays in the lock")
        windows.show(.none, lock: lock)
        XCTAssertNil(windows.window)
        XCTAssertEqual(window?.isHidden, true, "gone once unlocked")
    }

    func testNoLockStraightAfterSigningIn() async {
        let lock = lock(FakeAuthenticator(), FakeClock())
        XCTAssertTrue(lock.locked)
        lock.signedIn()
        XCTAssertFalse(lock.locked)
    }
}
