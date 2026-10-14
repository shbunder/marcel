import Foundation
import MarcelKit
import XCTest

@testable import MarcelSettings

/// S-17.6's UI tests against the mock hub, one layer down: the owner adds Emma and gets a QR; a member's Settings has
/// no People; removing Emma says what happens and she is gone from the list. Skipped unless `MARCEL_MOCK_HUB` is set:
/// `MOCK_DELAY=0 MOCK_HUB_PORT=7488 make mock-hub`, then
/// `MOCK_HUB=http://127.0.0.1:7488/api make -C ios settings-integration`.
@MainActor
final class PeopleMockHubTests: XCTestCase {
    final class Token: TokenStore, @unchecked Sendable {
        var value: String?
        init(_ value: String?) { self.value = value }
        func token() throws -> String? { value }
        func save(_ token: String) throws { value = token }
        func delete() throws { value = nil }
    }

    func testTheOwnerAddsSomeoneAMemberSeesNoPeopleAndRemovingThemSaysWhatHappens() async throws {
        guard let hub = ProcessInfo.processInfo.environment["MARCEL_MOCK_HUB"], let serverURL = URL(string: hub) else {
            throw XCTSkip("Set MARCEL_MOCK_HUB (make -C ios settings-integration) to run against the mock hub.")
        }
        // Any token the mock did not hand out is the owner's.
        let owner = LiveSettingsService(client: HubClient.make(serverURL: serverURL, tokenStore: Token("settings-owner")))
        let ownerSettings = SettingsModel(service: owner, tokens: FakeTokens(),
                                          topics: NtfyTopicStore(defaults: .scratch("people-mock")))
        await ownerSettings.load()
        XCTAssertTrue(ownerSettings.showsPeople, "the owner sees People")

        // Add a person (a new name each run: the mock keeps people while it runs) → a QR and a link.
        let people = PeopleModel(service: owner)
        await people.load()
        let name = "Lien \(UUID().uuidString.prefix(4))"
        let added = await people.add(name: name, helperName: "Bo")
        XCTAssertTrue(added, people.problem ?? "")
        let code = try XCTUnwrap(people.code)
        XCTAssertEqual(code.person, name)
        XCTAssertTrue(code.link.hasPrefix("marcel://pair?hub="), code.link)
        XCTAssertNotNil(PeopleModel.qr(code.link), "a QR to scan")
        XCTAssertGreaterThan(code.expiresAt, Date(), "time left")
        let user = try XCTUnwrap(people.people.value?.first { $0.name == name }, "listed")
        XCTAssertEqual(user.agent?.name, "Bo")

        // Their phone pairs with that code: a member, whose Settings has no People.
        let memberToken = Token(nil)
        let paired = try await PairingService(tokenStore: memberToken, hubStore: MemoryHub(),
                                              transport: HubClient.transport)
            .pair(scanned: "marcel://pair?hub=\(Self.encoded(serverURL))&code=\(code.code)", deviceName: "\(name)'s phone")
        XCTAssertNotNil(paired)
        let member = LiveSettingsService(client: HubClient.make(serverURL: serverURL, tokenStore: memberToken))
        let memberSettings = SettingsModel(service: member, tokens: FakeTokens(),
                                           topics: NtfyTopicStore(defaults: .scratch("people-member")))
        await memberSettings.load()
        XCTAssertEqual(memberSettings.me?.name, name)
        XCTAssertFalse(memberSettings.showsPeople, "a member does not see People")

        // Remove them → the words, then gone; their phone is signed out.
        await people.remove(user)
        XCTAssertEqual(people.removed, PeopleModel.removalWords(user))
        XCTAssertFalse(people.people.value?.contains { $0.id == user.id } ?? true, "gone from the list")
        do {
            _ = try await member.me()
            XCTFail("their phone still works")
        } catch let error as SettingsError {
            XCTAssertEqual(error, .unauthorized)
        }
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
