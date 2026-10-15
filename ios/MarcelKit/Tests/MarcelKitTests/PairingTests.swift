import Foundation
import XCTest

@testable import MarcelKit

final class PairingCodeTests: XCTestCase {
    func testReadsHubAndCodeFromTheQRCode() throws {
        let code = try PairingCode(scanned: "marcel://pair?hub=https%3A%2F%2Fmarcel-bot.com%2Fapi&code=K7Q2M9")
        XCTAssertEqual(code.serverURL, URL(string: "https://marcel-bot.com/api"))
        XCTAssertEqual(code.code, "K7Q2M9")
    }

    func testRefusesAnythingElse() {
        for text in [
            "https://marcel-bot.com/api?code=K7Q2M9",
            "marcel://pair?hub=https%3A%2F%2Fmarcel-bot.com%2Fapi",
            "marcel://pair?hub=https%3A%2F%2Fmarcel-bot.com%2Fapi&code=abc",
            "marcel://pair?hub=ftp%3A%2F%2Fx&code=K7Q2M9",
            "",
        ] {
            XCTAssertThrowsError(try PairingCode(scanned: text), text) { error in
                XCTAssertEqual(error as? PairingError, .notAPairingCode)
            }
        }
    }
}

final class PairingServiceTests: XCTestCase {
    static let scanned = "marcel://pair?hub=http%3A%2F%2F127.0.0.1%3A7499%2Fapi&code=K7Q2M9"
    static let paired = #"{"device_id":"dev_01J9ZQ4K2B","user_id":"usr_owner","token":"mrc_secret_token"}"#

    func testPairingPostsTheCodeAndKeepsTheToken() async throws {
        let transport = StubTransport { _ in .json(201, Self.paired) }
        let tokens = MemoryTokenStore()
        let hubs = MemoryHubStore()
        let service = PairingService(tokenStore: tokens, hubStore: hubs, transport: transport)

        let hub = try await service.pair(scanned: Self.scanned, deviceName: "Shaun's iPhone")

        let request = try XCTUnwrap(transport.requests.first)
        XCTAssertEqual(transport.requests.count, 1)
        XCTAssertEqual(request.method, .post)
        XCTAssertEqual(request.url, "http://127.0.0.1:7499/api/pair")
        XCTAssertNil(request.authorization, "pairing needs no token")
        let body = try JSONSerialization.jsonObject(with: request.body) as? [String: String]
        XCTAssertEqual(body, ["code": "K7Q2M9", "device_name": "Shaun's iPhone"])

        XCTAssertEqual(try tokens.token(), "mrc_secret_token")
        XCTAssertEqual(hub, PairedHub(serverURL: URL(string: "http://127.0.0.1:7499/api")!, deviceId: "dev_01J9ZQ4K2B", userId: "usr_owner"))
        XCTAssertEqual(hubs.load(), hub)
    }

    func testAPairingLinkWithABuddyStartsOnIt() async throws {
        let link = "marcel://pair?hub=http%3A%2F%2F127.0.0.1%3A7499%2Fapi&code=K7Q2M9&buddy=agt_pip"
        XCTAssertEqual(try PairingCode(scanned: link).buddy, "agt_pip")
        XCTAssertNil(try PairingCode(scanned: Self.scanned).buddy, "no buddy: the hub's first live one")
        XCTAssertNil(try PairingCode(scanned: Self.scanned + "&buddy=").buddy, "an empty buddy is none")
        XCTAssertNil(try PairingCode(scanned: Self.scanned + "&buddy=..%2Fagt%20pip").buddy, "not an id: dropped")

        final class Started: @unchecked Sendable { var ids: [String] = [] }
        let started = Started()
        let service = PairingService(tokenStore: MemoryTokenStore(), hubStore: MemoryHubStore(),
                                     transport: StubTransport { _ in .json(201, Self.paired) },
                                     startOn: { started.ids.append($0) })
        try await service.pair(scanned: link, deviceName: "Emma's iPhone")
        XCTAssertEqual(started.ids, ["agt_pip"])
        try await service.pair(scanned: Self.scanned, deviceName: "Emma's iPhone")
        XCTAssertEqual(started.ids, ["agt_pip"], "a link without a buddy names none")
    }

    func testARejectedCodeShowsTheHubsMessageAndStoresNothing() async {
        let transport = StubTransport { _ in
            .json(400, #"{"code":"pairing_code_expired","message":"That pairing code has expired. Run `marcel-hub pair` again for a new one."}"#)
        }
        let tokens = MemoryTokenStore()
        let hubs = MemoryHubStore()
        let service = PairingService(tokenStore: tokens, hubStore: hubs, transport: transport)

        do {
            try await service.pair(scanned: Self.scanned, deviceName: "iPhone")
            XCTFail("pairing should fail")
        } catch {
            XCTAssertEqual(error, .rejected("That pairing code has expired. Run `marcel-hub pair` again for a new one."))
        }
        XCTAssertNil(try tokens.token())
        XCTAssertNil(hubs.load())
    }

    func testTooManyWrongCodesShowsTheHubsMessage() async {
        let transport = StubTransport { _ in
            .json(429, #"{"code":"too_many_tries","message":"Too many wrong codes. Wait a minute, then scan a fresh code."}"#)
        }
        let tokens = MemoryTokenStore()
        let service = PairingService(tokenStore: tokens, hubStore: MemoryHubStore(), transport: transport)

        do {
            try await service.pair(scanned: Self.scanned, deviceName: "iPhone")
            XCTFail("pairing should fail")
        } catch {
            XCTAssertEqual(error, .rejected("Too many wrong codes. Wait a minute, then scan a fresh code."))
        }
        XCTAssertNil(try tokens.token())
    }

    func testAnUnreachableHubSaysSoAndStoresNothing() async {
        let transport = StubTransport { _ in .fail(URLError(.cannotConnectToHost)) }
        let tokens = MemoryTokenStore()
        let service = PairingService(tokenStore: tokens, hubStore: MemoryHubStore(), transport: transport)

        do {
            try await service.pair(scanned: Self.scanned, deviceName: "iPhone")
            XCTFail("pairing should fail")
        } catch {
            XCTAssertEqual(error, .hubUnreachable)
            XCTAssertEqual(error.errorDescription?.hasPrefix("Could not reach the hub."), true)
        }
        XCTAssertNil(try tokens.token())
    }

    func testABadScanNeverReachesTheHub() async {
        let transport = StubTransport { _ in .json(201, Self.paired) }
        let service = PairingService(tokenStore: MemoryTokenStore(), hubStore: MemoryHubStore(), transport: transport)

        do {
            try await service.pair(scanned: "hello", deviceName: "iPhone")
            XCTFail("pairing should fail")
        } catch {
            XCTAssertEqual(error, .notAPairingCode)
        }
        XCTAssertTrue(transport.requests.isEmpty)
    }

    func testAKeychainFailureIsReportedAndTheHubIsNotRemembered() async {
        let transport = StubTransport { _ in .json(201, Self.paired) }
        let tokens = MemoryTokenStore()
        tokens.failSave = true
        let hubs = MemoryHubStore()
        let service = PairingService(tokenStore: tokens, hubStore: hubs, transport: transport)

        do {
            try await service.pair(scanned: Self.scanned, deviceName: "iPhone")
            XCTFail("pairing should fail")
        } catch {
            XCTAssertEqual(error, .couldNotStoreToken)
        }
        XCTAssertNil(hubs.load())
    }

    func testRESTCallsAfterPairingCarryTheToken() async throws {
        let transport = StubTransport { _ in .json(200, #"{"ok":true,"version":"0.1.0","runner":{"reachable":true}}"#) }
        let client = HubClient.make(
            serverURL: URL(string: "http://127.0.0.1:7499/api")!, tokenStore: MemoryTokenStore("mrc_secret_token"),
            transport: transport)

        _ = try await client.getHealth()

        XCTAssertEqual(transport.requests.first?.authorization, "Bearer mrc_secret_token")
    }
}
