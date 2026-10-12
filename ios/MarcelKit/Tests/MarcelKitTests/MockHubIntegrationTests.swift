import Foundation
import OpenAPIURLSession
import XCTest

@testable import MarcelKit

/// Integration test: talks to the real mock hub over the network (127.0.0.1 only). Skipped unless
/// `MARCEL_MOCK_HUB` holds the hub's server URL; `make -C ios integration` sets it. Not part of the gate.
@MainActor
final class MockHubIntegrationTests: XCTestCase {
    /// Records each `since` and passes the call to the real WebSocket connector.
    final class RecordingConnector: EventSocketConnector, @unchecked Sendable {
        let real: URLSessionEventSocketConnector
        private let lock = NSLock()
        private var requested: [Int?] = []
        var sinceRequested: [Int?] { lock.withLock { requested } }

        init(_ real: URLSessionEventSocketConnector) { self.real = real }

        func connect(since: Int?) async throws -> any EventSocket {
            lock.withLock { requested.append(since) }
            return try await real.connect(since: since)
        }
    }

    private func waitUntil(_ what: String, seconds: Double = 10, _ condition: () throws -> Bool) async throws {
        let deadline = Date().addingTimeInterval(seconds)
        while try !condition() {
            if Date() > deadline { return XCTFail("timed out waiting for \(what)") }
            try await Task.sleep(for: .milliseconds(100))
        }
    }

    func testPairStreamDropAndReplayAgainstTheMockHub() async throws {
        guard let hub = ProcessInfo.processInfo.environment["MARCEL_MOCK_HUB"], let serverURL = URL(string: hub) else {
            throw XCTSkip("Set MARCEL_MOCK_HUB (make -C ios integration) to run against the mock hub.")
        }
        let tokens = MemoryTokenStore()
        let encoded = hub.addingPercentEncoding(withAllowedCharacters: .alphanumerics)!
        let paired = try await PairingService(tokenStore: tokens, hubStore: MemoryHubStore())
            .pair(scanned: "marcel://pair?hub=\(encoded)&code=K7Q2M9", deviceName: "Simulator")
        XCTAssertEqual(paired.serverURL, serverURL)
        XCTAssertFalse(try XCTUnwrap(tokens.token()).isEmpty)

        let cache = MarcelCache(container: try MarcelCache.makeContainer(inMemory: true))
        let connector = RecordingConnector(URLSessionEventSocketConnector(serverURL: serverURL, tokenStore: tokens))
        let first = EventStream(connector: connector, sink: cache)
        let firstRun = Task { await first.run() }
        try await waitUntil("the first connection to go live") { first.status == .live }

        let client = HubClient.make(serverURL: serverURL, tokenStore: tokens)
        let text = "Integration test \(UUID().uuidString)"
        _ = try await client.sendMessage(path: .init(conversationId: "cnv_main_marcel"), body: .json(.init(text: text)))
        try await waitUntil("our message to arrive over the stream") {
            try cache.messages(in: "cnv_main_marcel").contains { $0.text == text }
        }

        // Drop the connection while the scenario keeps going, then reconnect from where we were.
        firstRun.cancel()
        await firstRun.value
        let droppedAt = try XCTUnwrap(cache.lastEventId)
        try await Task.sleep(for: .seconds(2))

        let second = EventStream(connector: connector, sink: cache)
        let secondRun = Task { await second.run() }
        try await waitUntil("events missed while offline to be replayed") { (cache.lastEventId ?? 0) > droppedAt }
        secondRun.cancel()
        await secondRun.value

        XCTAssertEqual(connector.sinceRequested.last, droppedAt)
        let ids = try cache.messages(in: "cnv_main_marcel").map(\.id)
        XCTAssertEqual(ids.count, Set(ids).count, "no duplicate messages after the replay")
    }
}
