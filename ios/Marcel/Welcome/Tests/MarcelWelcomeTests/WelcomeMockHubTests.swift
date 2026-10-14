import Foundation
import MarcelKit
import MarcelLook
import XCTest

@testable import MarcelWelcome

/// S-17.5's UI tests, one layer down (XCUITest cannot run on this Mac's Xcode; ios/README.md): pair as Emma →
/// onboarding → chat; sign out → welcome. Through `AppFlow` and the real `HubConnection`, as `RootView` wires them.
/// Skipped unless `MARCEL_MOCK_HUB` holds the hub's server URL; needs a fresh mock hub (Emma not onboarded yet):
/// `MOCK_DELAY=0 MOCK_HUB_PORT=7488 make mock-hub`, then
/// `MOCK_HUB=http://127.0.0.1:7488/api make -C ios welcome-integration`.
@MainActor
final class WelcomeMockHubTests: XCTestCase {
    static let emma = "usr_01JA3E1"

    func testPairAsEmmaOnboardThenChatThenSignOut() async throws {
        guard let hub = ProcessInfo.processInfo.environment["MARCEL_MOCK_HUB"], let serverURL = URL(string: hub) else {
            throw XCTSkip("Set MARCEL_MOCK_HUB (make -C ios welcome-integration) to run against the mock hub.")
        }
        // The owner makes Emma a code (any token the mock did not hand out is the owner's).
        let owner = HubClient.make(serverURL: serverURL, tokenStore: MemoryTokens("welcome-owner"))
        let issued = try await owner.createPairingCode(path: .init(userId: Self.emma)).created.body.json
        let link = "marcel://pair?hub=\(serverURL.absoluteString.addingPercentEncoding(withAllowedCharacters: .alphanumerics)!)"
            + "&code=\(issued.code)"

        // Emma's phone: nothing saved yet → the welcome screen.
        let tokens = MemoryTokens()
        let connection = HubConnection(
            tokenStore: tokens, hubStore: MemoryHubs(),
            cache: MarcelCache(container: try MarcelCache.makeContainer(inMemory: true)), transport: HubClient.transport,
            connectorFactory: { URLSessionEventSocketConnector(serverURL: $0.serverURL, tokenStore: tokens) })
        defer { connection.stop() }
        let defaults = try XCTUnwrap(UserDefaults(suiteName: "welcome-mock-\(UUID().uuidString)"))
        let flow = AppFlow(
            connection: connection, defaults: defaults,
            makeService: { LiveWelcomeService(client: HubClient.make(serverURL: $0.serverURL, tokenStore: tokens)) },
            makeLookService: { LiveLookService(client: HubClient.make(serverURL: $0.serverURL, tokenStore: tokens)) })
        await flow.update()
        XCTAssertEqual(flow.screen, .welcome)

        // Pair (the link the QR code holds) → "Hi Emma! Meet your helper".
        try await connection.pair(scanned: link, deviceName: "Emma's iPhone")
        await flow.update()
        XCTAssertEqual(flow.screen, .onboarding)
        let onboarding = try XCTUnwrap(flow.onboarding)
        XCTAssertEqual(onboarding.greeting, "Hi Emma! Meet your helper")
        onboarding.helperName = "Pippa"
        onboarding.look.choose(animal: .panda)
        onboarding.look.choose(color: .lavender)
        await onboarding.look.loadPreview(onboarding.look.look)
        XCTAssertNotNil(onboarding.look.previews[onboarding.look.look], "the hub drew the preview")

        // "Start chatting" → the chat; the hub has the name, the look and onboarded_at.
        let started = await onboarding.startChatting()
        XCTAssertTrue(started, onboarding.problem ?? "")
        flow.finishedOnboarding()
        XCTAssertEqual(flow.screen, .main)
        let me = try await LiveWelcomeService(client: HubClient.make(serverURL: serverURL, tokenStore: tokens)).me()
        let pip = try XCTUnwrap(me.agents.first)
        XCTAssertEqual(me.name, "Emma")
        XCTAssertEqual(pip.name, "Pippa")
        XCTAssertEqual(pip.drawnLook?.animal, .panda)
        XCTAssertNotNil(pip.onboardedAt)

        // Sign out → the welcome screen; the hub no longer knows the old token.
        let oldToken = try XCTUnwrap(tokens.token())
        let note = await flow.signOut()
        XCTAssertNil(note)
        XCTAssertEqual(flow.screen, .welcome)
        XCTAssertNil(try tokens.token())
        do {
            _ = try await LiveWelcomeService(client: HubClient.make(serverURL: serverURL,
                                                                    tokenStore: MemoryTokens(oldToken))).me()
            XCTFail("the signed-out phone's token still works")
        } catch let error as WelcomeError {
            XCTAssertEqual(error, .unpaired)
        }
    }
}
