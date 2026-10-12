import CryptoKit
import Foundation
import HTTPTypes
import MarcelKit
import OpenAPIRuntime
import XCTest

@testable import MarcelNotebook

/// Hub objects written as the contract's JSON, and a fake hub that serves them through the real generated client.
enum Fx {
    /// "Now" in every test: 2026-10-04 18:40 UTC (a Sunday).
    static let now = Date(timeIntervalSince1970: 1_791_139_200)
    static let utc = TimeZone(identifier: "UTC")!
    static let text = NotebookText(now: now, timeZone: utc)

    static func at(_ seconds: TimeInterval) -> Date { now.addingTimeInterval(seconds) }

    static let aboutYou = """
        # Memory

        - Prefers mornings for appointments
        - Approves energy switches over €150
        - Family dinner is at 18:30
        """

    static let energy = """
        # Home energy

        - Roof fits 4 kWp, not 6
        - Heat pump waits for subsidy news
        """

    static let projectNames = ["energy": "Home energy", "garden": "Garden"]

    static func json(_ object: Any) -> String {
        String(decoding: try! JSONSerialization.data(withJSONObject: object), as: UTF8.self)
    }

    static func error(_ code: String, _ message: String) -> String {
        json(["code": code, "message": message])
    }

    static func iso(_ date: Date) -> String { date.formatted(Date.ISO8601FormatStyle()) }

    static func frame(_ type: String, _ data: [String: Any], id: Int, at: Date) -> EventFrame {
        let json: [String: Any] = ["id": id, "type": type, "at": iso(at), "data": data]
        return try! EventFrame.decode(try! JSONSerialization.data(withJSONObject: json))
    }

    nonisolated(unsafe) static let me: [String: Any] = [
        "user_id": "usr_owner", "name": "Shaun", "device_id": "dev_phone", "push": ["apns": false, "ntfy": false],
        "agents": [["id": "agt_marcel", "name": "Marcel", "animal": "giraffe", "palette": "marcel", "seed": "Marcel",
                    "main_conversation_id": "cnv_main", "state": ["kind": "idle"], "model_brain": "opus",
                    "model_worker": "sonnet"]],
    ]

    /// The hub's `Rollover` (app API 0.6), as `GET /agents/{id}/brain/rollovers` lists it.
    static func hubRollover(_ id: String, at: Date, reason: String = "daily",
                            kept: String = "Your notes, the last 40 messages and 3 open threads.") -> [String: Any] {
        ["id": id, "agent_id": "agt_marcel", "at": iso(at), "reason": reason, "kept": kept]
    }

    /// What the real hub answers for a route it does not serve yet (`hub/…/auth/errors.py`).
    static let notBuilt = (404, error("not_found", "There is nothing at that address."))

    static let rolloversRoute = "GET /agents/agt_marcel/brain/rollovers?limit=10"
    static let rollOverRoute = "POST /agents/agt_marcel/brain/rollover"

    static func rollover(id: Int, at: Date, reason: String = "daily",
                         kept: String = "open threads, decisions, today's plan.") -> EventFrame {
        frame("brain.rollover", ["agent_id": "agt_marcel", "reason": reason, "kept": kept], id: id, at: at)
    }
}

/// A hub that keeps memory files the way `hub/src/marcel_hub/memory.py` does: the etag is the git blob id of the
/// bytes, a save with a stale `If-Match` answers 409 with the current version, a save without `If-Match` creates
/// (409 if the file exists), and `If-Match` on a file that is gone answers 404 `memory_file_not_found`. Served through
/// the real generated client. `overrides` answers a route ("GET /memory") with a fixed status and body instead.
final class FakeHub: ClientTransport, @unchecked Sendable {
    struct File {
        var content: String
        var updatedAt: Date
        var by: String
        var etag: String { FakeHub.blob(content) }
    }

    private let lock = NSLock()
    private var store: [String: File] = [:]
    private var table: [String: (Int, String)] = [:]
    private var seen: [String] = []
    private var matches: [String?] = []
    var offline = false
    /// Paths the tree lists that are not real files (an untrusted name from the hub).
    var extraTreePaths: [String] = []

    init(files: [String: File]? = nil) {
        store = files ?? [
            "MEMORY.md": File(content: Fx.aboutYou, updatedAt: Fx.at(-86_400), by: "agent"),
            "memory/projects/energy.md": File(content: Fx.energy, updatedAt: Fx.at(-3600 * 2), by: "app"),
            "memory/2026-10-03.md": File(content: "- Called the plumber.\n", updatedAt: Fx.at(-86_400), by: "agent"),
            "memory/2026-10-04.md": File(content: "- Solar quote on Friday.\n", updatedAt: Fx.at(-600), by: "agent"),
        ]
    }

    static func blob(_ content: String) -> String {
        let data = Data(content.utf8)
        let digest = Insecure.SHA1.hash(data: Data("blob \(data.count)\0".utf8) + data)
        return digest.map { String(format: "%02x", $0) }.joined()
    }

    subscript(route: String) -> (Int, String)? {
        get { lock.withLock { table[route] } }
        set { lock.withLock { table[route] = newValue } }
    }

    func file(_ path: String) -> File? { lock.withLock { store[path] } }

    /// Someone else (Marcel, another phone) writes the file.
    func write(_ path: String, _ content: String, by: String = "agent", at: Date = Fx.at(-60)) {
        lock.withLock { store[path] = File(content: content, updatedAt: at, by: by) }
    }

    func remove(_ path: String) { _ = lock.withLock { store.removeValue(forKey: path) } }

    /// "PUT /memory/files/memory%2F2026-10-04.md", as sent (the path still percent-encoded).
    var requests: [String] { lock.withLock { seen } }
    /// Every `If-Match` sent with a PUT, in order (nil: none).
    var ifMatches: [String?] { lock.withLock { matches } }

    var client: Client { HubClient.make(serverURL: URL(string: "http://hub.test/api")!, transport: self) }
    @MainActor var service: LiveNotebookService { LiveNotebookService(client: client) }

    private func describe(_ path: String, _ file: File) -> [String: Any] {
        ["path": path, "content": file.content, "etag": file.etag, "updated_at": Fx.iso(file.updatedAt),
         "updated_by": file.by]
    }

    func send(_ request: HTTPRequest, body: HTTPBody?, baseURL: URL, operationID: String) async throws -> (
        HTTPResponse, HTTPBody?
    ) {
        let text = if let body { try await String(collecting: body, upTo: 2 * 1024 * 1024) } else { "" }
        let wirePath = request.path ?? ""
        let method = request.method.rawValue
        let ifMatch = request.headerFields[HTTPField.Name("If-Match")!]
        let (status, json): (Int, String) = lock.withLock {
            seen.append("\(method) \(wirePath)")
            if method == "PUT" { matches.append(ifMatch) }
            return answer(method: method, wirePath: wirePath, body: text, ifMatch: ifMatch)
        }
        if offline { throw URLError(.notConnectedToInternet) }
        var fields = HTTPFields()
        fields[.contentType] = "application/json"
        return (HTTPResponse(status: .init(code: status), headerFields: fields), json.isEmpty ? nil : HTTPBody(json))
    }

    /// Called with the lock held.
    private func answer(method: String, wirePath: String, body: String, ifMatch: String?) -> (Int, String) {
        let route = "\(method) \(wirePath)"
        if let fixed = table[route] { return fixed }
        if route == "GET /me" { return (200, Fx.json(Fx.me)) }
        // The rollover routes (app API 0.6) are not served by the hub until S-06.7: its generic 404 by default.
        if wirePath.hasPrefix("/agents/") { return Fx.notBuilt }
        if route == "GET /memory" {
            var files = store.keys.sorted().map { path -> [String: Any] in
                ["path": path, "size_bytes": store[path]!.content.utf8.count,
                 "updated_at": Fx.iso(store[path]!.updatedAt)]
            }
            files += extraTreePaths.map { ["path": $0, "size_bytes": 1, "updated_at": Fx.iso(Fx.now)] }
            return (200, Fx.json(["files": files]))
        }
        let prefix = "/memory/files/"
        // The path must arrive as one segment: a raw `/` after the prefix means the app did not encode it.
        guard wirePath.hasPrefix(prefix), !wirePath.dropFirst(prefix.count).contains("/"),
              let path = String(wirePath.dropFirst(prefix.count)).removingPercentEncoding
        else { return (404, #"{"detail":"Not Found"}"#) }
        guard MemoryPath.isSafe(path) else {
            return (400, Fx.error("invalid_memory_path", "That is not a memory file path."))
        }
        switch method {
        case "GET":
            guard let file = store[path] else {
                return (404, Fx.error("memory_file_not_found", "There is no memory file called \(path)."))
            }
            return (200, Fx.json(describe(path, file)))
        case "PUT":
            guard let data = body.data(using: .utf8),
                  let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let content = object["content"] as? String
            else { return (400, Fx.error("bad_request", "Send JSON like {\"content\": \"…\"}.")) }
            let etag = ifMatch.map { $0.trimmingCharacters(in: .whitespaces).replacingOccurrences(of: "W/", with: "")
                .trimmingCharacters(in: CharacterSet(charactersIn: "\"")) }
            if let current = store[path] {
                guard let etag, etag == current.etag else { return (409, Fx.json(describe(path, current))) }
            } else if etag != nil {
                return (404, Fx.error("memory_file_not_found", "\(path) was removed since you opened it."))
            }
            store[path] = File(content: content, updatedAt: Fx.now, by: "app")
            return (200, Fx.json(describe(path, store[path]!)))
        default:
            return (405, #"{"detail":"Method Not Allowed"}"#)
        }
    }
}

extension UserDefaults {
    /// A throwaway defaults domain per test.
    static func scratch(_ name: String = #function) -> UserDefaults {
        let suite = "notebook-tests-\(name)"
        UserDefaults().removePersistentDomain(forName: suite)
        return UserDefaults(suiteName: suite)!
    }
}
