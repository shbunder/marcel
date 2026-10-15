import Foundation
import MarcelKit
import MarcelLook
import XCTest

@testable import MarcelSettings

/// S-22.12 against the mock hub (S-02.15: Marcel and Pip seeded, any phone sees every buddy): Settings › Buddies
/// lists both; a code made to start on Pip pairs a second phone that starts on Pip (`&buddy=`); that phone switches to
/// Marcel with `PATCH /devices/{id}`; then it is signed out again. Skipped unless `MARCEL_MOCK_HUB` is set:
/// `MOCK_DELAY=0 MOCK_HUB_PORT=7488 make mock-hub`, then
/// `MOCK_HUB=http://127.0.0.1:7488/api make -C ios settings-integration`.
@MainActor
final class BuddiesMockHubTests: XCTestCase {
    final class Token: TokenStore, @unchecked Sendable {
        var value: String?
        init(_ value: String?) { self.value = value }
        func token() throws -> String? { value }
        func save(_ token: String) throws { value = token }
        func delete() throws { value = nil }
    }

    final class Started: @unchecked Sendable { var ids: [String] = [] }

    func testACodeForPipPairsAPhoneThatStartsOnPipAndSwitches() async throws {
        guard let hub = ProcessInfo.processInfo.environment["MARCEL_MOCK_HUB"], let serverURL = URL(string: hub) else {
            throw XCTSkip("Set MARCEL_MOCK_HUB (make -C ios settings-integration) to run against the mock hub.")
        }
        // Any token the mock did not hand out is the first phone's.
        let first = LiveSettingsService(client: HubClient.make(serverURL: serverURL, tokenStore: Token("settings-first")))
        let shown = CurrentBuddy(defaults: .scratch("buddies-mock-\(UUID().uuidString)"))
        let buddies = BuddiesModel(service: first, looks: LiveLookService(
            client: HubClient.make(serverURL: serverURL, tokenStore: Token("settings-first"))), buddy: shown)
        await buddies.load()
        let list = try XCTUnwrap(buddies.buddies.value, "\(buddies.buddies)")
        // Marcel first, then Pip (another test may have renamed Marcel during onboarding).
        XCTAssertGreaterThanOrEqual(list.count, 2, "every buddy: \(list.map(\.name))")
        let pip = try XCTUnwrap(list.first { $0.name == "Pip" }, "\(list.map(\.name))")
        let marcel = try XCTUnwrap(list.first { $0.id != pip.id })
        XCTAssertNotNil(buddies.avatar(for: pip), "drawn from its look (GET /avatar-previews)")

        // A code that starts on Pip → its link says so.
        buddies.startOn = pip.id
        await buddies.newCode()
        let code = try XCTUnwrap(buddies.code, buddies.problem ?? "")
        XCTAssertTrue(code.link.hasSuffix("&buddy=\(pip.id)"), code.link)
        XCTAssertEqual(code.startsOn, "Pip")

        // The second phone pairs with it and starts on Pip.
        let started = Started()
        let secondToken = Token(nil)
        let link = "marcel://pair?hub=\(Self.encoded(serverURL))&code=\(code.code)&buddy=\(pip.id)"
        let paired = try await PairingService(tokenStore: secondToken, hubStore: MemoryHub(),
                                              transport: HubClient.transport, startOn: { started.ids.append($0) })
            .pair(scanned: link, deviceName: "Second phone \(UUID().uuidString.prefix(4))")
        XCTAssertEqual(started.ids, [pip.id], "the app starts on Pip")
        let second = HubClient.make(serverURL: serverURL, tokenStore: secondToken)
        func shownOnSecond() async throws -> String? {
            guard case .ok(let ok) = try await second.listDevices() else { return nil }
            return try ok.body.json.first { $0.id == paired.deviceId }?.agentId
        }
        let onPip = try await shownOnSecond()
        XCTAssertEqual(onPip, pip.id, "the hub agrees")

        // Switch: PATCH /devices/{id} → the hub shows Marcel on that phone.
        guard case .ok = try await second.updateDevice(path: .init(deviceId: paired.deviceId),
                                                        body: .json(.init(agentId: marcel.id))) else {
            return XCTFail("the switch was refused")
        }
        let onMarcel = try await shownOnSecond()
        XCTAssertEqual(onMarcel, marcel.id)

        // Clean up: sign the second phone out.
        try await LiveSettingsService(client: second).revoke(deviceId: paired.deviceId)
    }

    /// The mock's links name the contract example's hub; the phone pairs with this one, with the same code.
    static func encoded(_ url: URL) -> String {
        url.absoluteString.addingPercentEncoding(withAllowedCharacters: .alphanumerics) ?? url.absoluteString
    }

    final class MemoryHub: PairedHubStore, @unchecked Sendable {
        var hub: PairedHub?
        func load() -> PairedHub? { hub }
        func save(_ hub: PairedHub) throws { self.hub = hub }
        func clear() { hub = nil }
    }
}
