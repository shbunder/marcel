import MarcelKit
import SwiftUI
import XCTest

@testable import MarcelWelcome

@MainActor
final class WelcomeSnapshotTests: XCTestCase {
    func testTheWelcomeScreen() {
        Snapshot.assert(WelcomeView { _ in }.environment(\.welcomeStill, true), named: "Welcome", padding: 0)
    }

    func testMeetYourHelper() async throws {
        let hub = FakeWelcomeService()
        let me = try FakeWelcomeService.decode(Me.self, [
            "user_id": "usr_emma", "name": "Emma", "device_id": "dev_emma", "push": ["apns": false, "ntfy": false],
            "agents": [FakeWelcomeService.agent(onboarded: false, name: "Pip")],
        ])
        let model = OnboardingModel(me: me, agent: me.agents[0], service: hub, looks: ZooPreviews())
        model.look.choose(color: .teal)
        model.look.choose(personality: .cool)
        for animal in LookAnimal.allCases { await model.look.loadPreview(model.look.trying(animal: animal)) }
        Snapshot.assert(OnboardingView(model: model) {}.environment(\.welcomeStill, true), named: "Meet-your-helper",
                        padding: 0)
    }

    func testTheLock() {
        let lock = AppLock(authenticator: FakeAuthenticator(), now: { 0 },
                           defaults: UserDefaults(suiteName: "welcome-lock-snapshot")!)
        Snapshot.assert(LockView(lock: lock).frame(height: 600).environment(\.welcomeStill, true), named: "Locked",
                        padding: 0)
    }
}
