import Foundation
import MarcelKit
import MarcelLook

@testable import MarcelWelcome

/// The device token in memory.
final class MemoryTokens: TokenStore, @unchecked Sendable {
    var value: String?
    init(_ value: String? = nil) { self.value = value }
    func token() throws -> String? { value }
    func save(_ token: String) throws { value = token }
    func delete() throws { value = nil }
}

/// The paired hub in memory.
final class MemoryHubs: PairedHubStore, @unchecked Sendable {
    var hub: PairedHub?
    init(_ hub: PairedHub? = nil) { self.hub = hub }
    func load() -> PairedHub? { hub }
    func save(_ hub: PairedHub) throws { self.hub = hub }
    func clear() { hub = nil }
}

/// A stream that never connects: these tests are about screens, not events.
struct NoSocket: EventSocketConnector {
    func connect(since: Int?) async throws -> any EventSocket { throw URLError(.cannotConnectToHost) }
}

/// A hub for getting in: who this phone is, onboarding's save, and signing out.
final class FakeWelcomeService: WelcomeService, @unchecked Sendable {
    var onboarded = false
    var offline = false
    var refuse: String?
    private(set) var finished: [(name: String, look: KnownLook)] = []
    private(set) var signedOut: [String] = []

    func me() async throws -> Me {
        if offline { throw WelcomeError.unreachable }
        return try Self.decode(Me.self, [
            "user_id": "usr_emma", "name": "Emma", "device_id": "dev_emma", "push": ["apns": false, "ntfy": false],
            "agents": [Self.agent(onboarded: onboarded, name: "Pip")],
        ])
    }

    func finish(agentId: String, name: String, look: KnownLook) async throws -> Agent {
        if offline { throw WelcomeError.unreachable }
        if let refuse { throw WelcomeError(refuse) }
        finished.append((name, look))
        onboarded = true
        return try Self.decode(Agent.self, Self.agent(onboarded: true, name: name))
    }

    func signOut(deviceId: String) async throws {
        if offline { throw WelcomeError.unreachable }
        signedOut.append(deviceId)
    }

    static func agent(onboarded: Bool, name: String) -> [String: Any] {
        var agent: [String: Any] = [
            "id": "agt_pip", "name": name, "animal": "fox", "main_conversation_id": "cnv_pip",
            "look": ["animal": "fox", "color": "sky", "personality": "cozy"],
            "state": ["kind": "idle"], "model_brain": "opus", "model_worker": "sonnet",
        ]
        if onboarded { agent["onboarded_at"] = "2026-10-14T08:00:00Z" }
        return agent
    }

    static func decode<T: Decodable>(_: T.Type, _ json: [String: Any]) throws -> T {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        return try decoder.decode(T.self, from: JSONSerialization.data(withJSONObject: json))
    }
}

/// Previews the Pixel Zoo drew for the look picker's tests (`Marcel/Look/Tests/MarcelLookTests/Fixtures`, teal and cool).
struct ZooPreviews: LookService {
    static let fixtures = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent()
        .deletingLastPathComponent().deletingLastPathComponent().appending(path: "Look/Tests/MarcelLookTests/Fixtures")

    func preview(_ look: KnownLook) async throws -> Data {
        try Data(contentsOf: Self.fixtures.appending(
            path: "\(look.animal.rawValue)-\(look.color.rawValue)-\(look.personality.rawValue).png"))
    }

    func save(_ look: KnownLook, agentId: String) async throws -> Agent { throw LookError.unreachable }
}

/// Previews from nowhere: the look picker draws its words instead.
struct NoPreviews: LookService {
    func preview(_ look: KnownLook) async throws -> Data { throw LookError.unreachable }
    func save(_ look: KnownLook, agentId: String) async throws -> Agent { throw LookError.unreachable }
}

/// Face ID that answers as told, and counts how often it was asked.
final class FakeAuthenticator: Authenticator, @unchecked Sendable {
    var available = true
    var answer = true
    private(set) var asked = 0
    func canAuthenticate() -> Bool { available }
    func authenticate(reason: String) async -> Bool {
        asked += 1
        return answer
    }
}

/// A clock the test moves by hand, in seconds.
final class FakeClock: @unchecked Sendable {
    var now: TimeInterval = 1_000
    func advance(minutes: Double) { now += minutes * 60 }
}
