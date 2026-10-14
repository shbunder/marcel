import Foundation
import HTTPTypes
import MarcelKit
import MarcelLook
import OpenAPIRuntime
import XCTest

@testable import MarcelSettings

/// Hub objects written as the contract's JSON, and a fake hub that serves them through the real generated client.
enum Fx {
    /// "Now" in every test: 2026-10-04 18:40 UTC (a Sunday).
    static let now = Date(timeIntervalSince1970: 1_791_139_200)
    static let utc = TimeZone(identifier: "UTC")!
    static let text = SettingsText(now: now, timeZone: utc)

    static func at(_ seconds: TimeInterval) -> String {
        now.addingTimeInterval(seconds).formatted(Date.ISO8601FormatStyle())
    }

    nonisolated(unsafe) static let agent: [String: Any] = [
        "id": "agt_marcel", "name": "Marcel", "animal": "giraffe", "palette": "marcel", "seed": "Marcel",
        "main_conversation_id": "cnv_main", "state": ["kind": "idle"], "model_brain": "opus", "model_worker": "sonnet",
    ]

    static func me(apns: Bool = false, ntfy: Bool = true) -> [String: Any] {
        var push: [String: Any] = ["apns": apns, "ntfy": ntfy]
        if ntfy { push["ntfy_server"] = "https://ntfy.sh" }
        return ["user_id": "usr_owner", "name": "Shaun", "role": "owner", "device_id": "dev_phone", "agents": [agent],
                "push": push]
    }

    /// Everyone, as the owner's People screen lists them (`GET /users`).
    nonisolated(unsafe) static let users: [[String: Any]] = [
        ["id": "usr_owner", "name": "Shaun", "role": "owner", "created_at": at(-86_400 * 60),
         "agent": ["id": "agt_marcel", "name": "Marcel"], "device_count": 2, "quests_this_week": 12],
        ["id": "usr_emma", "name": "Emma", "role": "member", "created_at": at(-86_400 * 3),
         "agent": ["id": "agt_pip", "name": "Pip", "look": ["animal": "fox", "color": "sky", "personality": "cozy"]],
         "device_count": 1, "quests_this_week": 3],
    ]

    static func issued(_ code: String = "R4T8WZ", minutesLeft: Double = 10) -> [String: Any] {
        ["code": code, "link": "marcel://pair?hub=http%3A%2F%2Fhub.test%2Fapi&code=\(code)",
         "expires_at": at(minutesLeft * 60)]
    }

    static func devices(phonePush: String = "ntfy") -> [[String: Any]] {
        [
            ["id": "dev_phone", "name": "Shaun's iPhone", "created_at": at(-86_400 * 8), "last_seen_at": at(0),
             "push": phonePush, "current": true],
            ["id": "dev_ipad", "name": "Kitchen iPad", "created_at": at(-86_400 * 30), "last_seen_at": at(-86_400 * 2),
             "push": "none", "current": false],
        ]
    }

    nonisolated(unsafe) static let schedules: [[String: Any]] = [
        ["id": "sch_digest_morning", "agent_id": "agt_marcel", "name": "Morning digest", "cron": "0 7 * * *",
         "tz": "Europe/Brussels", "location": "brain", "prompt": "Write the morning digest.", "paused": false,
         "builtin": true, "next_run_at": at(10 * 3600 + 20 * 60)],
        ["id": "sch_deps", "agent_id": "agt_marcel", "name": "Monday dependency report", "cron": "0 8 * * 1",
         "tz": "Europe/Brussels", "location": "cloud", "prompt": "List outdated dependencies.", "paused": true,
         "builtin": false],
    ]

    nonisolated(unsafe) static let usage: [String: Any] = [
        "five_hour": ["used_pct": 91.0, "resets_at": at(2 * 3600 + 20 * 60)],
        "weekly": ["used_pct": 48.2, "resets_at": at(86_400 * 2 + 14 * 3600 + 20 * 60)],
        "source": "statusline", "stale": false, "taken_at": at(0),
        "guard": ["active": true, "reason": "Plan usage is at 91 %. New tasks wait until 21:00.", "queued": 2],
    ]

    nonisolated(unsafe) static let health: [String: Any] = [
        "ok": true, "version": "0.4.1", "runner": ["reachable": false, "reason": "The NUC runner didn't answer."],
        "brain_alive": true,
    ]

    nonisolated(unsafe) static let approvals: [[String: Any]] = [
        ["id": "apr_engie", "task_id": "tsk_energy", "tool": "Bash", "summary": "Fetch Engie's price list",
         "state": "open", "created_at": at(-120)],
    ]

    static func project(_ id: String, slug: String, name: String) -> [String: Any] {
        ["id": id, "agent_id": "agt_marcel", "slug": slug, "name": name, "board_artifact_id": "art_board_\(slug)",
         "archived": false, "counts": ["working": 1, "needs_you": 0, "done_7d": 2], "created_at": at(-86_400),
         "updated_at": at(-60)]
    }

    static func message(_ id: String, conversation: String, task: String? = nil) -> [String: Any] {
        var json: [String: Any] = ["id": id, "conversation_id": conversation, "role": "agent", "kind": "text",
                                   "text": "Hello", "created_at": at(-300)]
        if let task { json["task_id"] = task }
        return json
    }

    static func json(_ object: Any) -> String {
        String(decoding: try! JSONSerialization.data(withJSONObject: object), as: UTF8.self)
    }

    static func decode<T: Decodable>(_ object: Any) -> T {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .custom { decoder in
            let text = try decoder.singleValueContainer().decode(String.self)
            return try Date.ISO8601FormatStyle().parse(text)
        }
        return try! decoder.decode(T.self, from: try! JSONSerialization.data(withJSONObject: object))
    }

    static func error(_ code: String, _ message: String) -> String {
        json(["code": code, "message": message])
    }
}

/// A hub that answers the contract's routes from a table, through the real generated client. Unknown routes answer
/// as the real hub answers a route it does not serve: 404 with its generic `not_found` error (`hub/…/auth/errors.py`).
final class FakeHub: ClientTransport, @unchecked Sendable {
    private let lock = NSLock()
    private var table: [String: (Int, String)] = [:]
    private var seen: [String] = []
    private var bodies: [String] = []
    var offline = false

    init() {
        self["GET /me"] = (200, Fx.json(Fx.me()))
        self["GET /devices"] = (200, Fx.json(Fx.devices()))
        self["GET /schedules"] = (200, Fx.json(Fx.schedules))
        self["GET /usage"] = (200, Fx.json(Fx.usage))
        self["GET /health"] = (200, Fx.json(Fx.health))
        self["GET /approvals"] = (200, Fx.json(Fx.approvals))
        self["GET /users"] = (200, Fx.json(Fx.users))
        self["GET /projects"] = (200, Fx.json([Fx.project("prj_energy", slug: "energy", name: "Energy contracts")]))
        // By id (app API 0.5): one open approval, one answered (no longer in `GET /approvals`), and messages the
        // phone has cached and one it has not.
        self["GET /approvals/apr_engie"] = (200, Fx.json(Fx.approvals[0]))
        self["GET /approvals/apr_done"] = (200, Fx.json(Fx.approvals[0].merging(
            ["id": "apr_done", "task_id": "tsk_garden", "state": "approved"]) { $1 }))
        self["GET /approvals/apr_gone"] = (404, Fx.error("approval_not_found", "No such approval."))
        self["GET /messages/msg_main"] = (200, Fx.json(Fx.message("msg_main", conversation: "cnv_main")))
        self["GET /messages/msg_side"] = (200, Fx.json(Fx.message("msg_side", conversation: "cnv_side_1",
                                                                  task: "tsk_energy")))
        self["GET /messages/msg_fresh"] = (200, Fx.json(Fx.message("msg_fresh", conversation: "cnv_side_2",
                                                                   task: "tsk_garden")))
        self["GET /messages/msg_gone"] = (404, Fx.error("message_not_found", "No such message."))
    }

    subscript(route: String) -> (Int, String)? {
        get { lock.withLock { table[route] } }
        set { lock.withLock { table[route] = newValue } }
    }

    /// "GET /schedules?agent_id=agt_marcel", in order.
    var requests: [String] { lock.withLock { seen } }
    var sentBodies: [String] { lock.withLock { bodies } }
    /// The last JSON body sent, as a dictionary.
    var lastBody: NSDictionary? {
        sentBodies.last.flatMap { try? JSONSerialization.jsonObject(with: Data($0.utf8)) as? NSDictionary }
    }

    var client: Client { HubClient.make(serverURL: URL(string: "http://hub.test/api")!, transport: self) }
    @MainActor var service: LiveSettingsService { LiveSettingsService(client: client) }

    func send(_ request: HTTPRequest, body: HTTPBody?, baseURL: URL, operationID: String) async throws -> (
        HTTPResponse, HTTPBody?
    ) {
        let text = if let body { try await String(collecting: body, upTo: 64 * 1024) } else { "" }
        let path = request.path ?? ""
        let route = "\(request.method.rawValue) \(path.split(separator: "?").first.map(String.init) ?? path)"
        let answer: (Int, String)? = lock.withLock {
            seen.append("\(request.method.rawValue) \(path)")
            if !text.isEmpty { bodies.append(text) }
            return table[route]
        }
        if offline { throw URLError(.notConnectedToInternet) }
        let (status, json) = answer ?? (404, Fx.error("not_found", "There is nothing at that address."))
        var fields = HTTPFields()
        fields[.contentType] = "application/json"
        return (HTTPResponse(status: .init(code: status), headerFields: fields), json.isEmpty ? nil : HTTPBody(json))
    }
}

/// The look picker's hub, unused in these tests.
struct NoLooks: LookService {
    func preview(_ look: KnownLook) async throws -> Data { throw LookError.unreachable }
    func save(_ look: KnownLook, agentId: String) async throws -> Agent { throw LookError.unreachable }
}

/// An APNs token source that answers as told.
@MainActor
final class FakeTokens: PushTokenSource {
    var result: Result<String, any Error> = .failure(PushSetupError.apnsUnavailable)
    var asked = 0

    func token() async throws -> String {
        asked += 1
        return try result.get()
    }
}

extension UserDefaults {
    /// A throwaway defaults domain per test.
    static func scratch(_ name: String = #function) -> UserDefaults {
        let suite = "settings-tests-\(name)"
        UserDefaults().removePersistentDomain(forName: suite)
        return UserDefaults(suiteName: suite)!
    }
}

/// What pairing leaves on the phone: the hub's address and the device token. In memory, for tests.
struct PairedStores {
    final class Token: TokenStore, @unchecked Sendable {
        var value: String?
        init(_ value: String?) { self.value = value }
        func token() throws -> String? { value }
        func save(_ token: String) throws { value = token }
        func delete() throws { value = nil }
    }

    let hub: UserDefaultsPairedHubStore
    let token: Token

    init(paired: Bool, name: String = #function) {
        hub = UserDefaultsPairedHubStore(defaults: .scratch("stores-\(name)"))
        token = Token(paired ? "mrc_test" : nil)
        if paired {
            try! hub.save(PairedHub(serverURL: URL(string: "http://hub.test/api")!, deviceId: "dev_phone",
                                    userId: "usr_owner"))
        }
    }
}
