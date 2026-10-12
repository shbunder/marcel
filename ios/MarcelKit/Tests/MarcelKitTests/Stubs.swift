import Foundation
import HTTPTypes
import OpenAPIRuntime

@testable import MarcelKit

/// A token store in memory, for tests that are not about the Keychain.
final class MemoryTokenStore: TokenStore, @unchecked Sendable {
    private let lock = NSLock()
    private var value: String?
    var failSave = false

    init(_ value: String? = nil) { self.value = value }

    func token() throws -> String? { lock.withLock { value } }
    func save(_ token: String) throws {
        if failSave { throw KeychainError(status: -1) }
        lock.withLock { value = token }
    }
    func delete() throws { lock.withLock { value = nil } }
}

final class MemoryHubStore: PairedHubStore, @unchecked Sendable {
    private let lock = NSLock()
    private var hub: PairedHub?

    init(_ hub: PairedHub? = nil) { self.hub = hub }

    func load() -> PairedHub? { lock.withLock { hub } }
    func save(_ hub: PairedHub) throws { lock.withLock { self.hub = hub } }
    func clear() { lock.withLock { hub = nil } }
}

/// Stands in for the network: records each request and answers from a script. Never opens a socket.
final class StubTransport: ClientTransport, @unchecked Sendable {
    struct Recorded {
        let method: HTTPRequest.Method
        let url: String
        let authorization: String?
        let body: Data
    }

    enum Answer {
        case json(Int, String)
        case fail(any Error)
    }

    private let lock = NSLock()
    private(set) var requests: [Recorded] = []
    private let answer: @Sendable (HTTPRequest) -> Answer

    init(_ answer: @escaping @Sendable (HTTPRequest) -> Answer) { self.answer = answer }

    func send(_ request: HTTPRequest, body: HTTPBody?, baseURL: URL, operationID: String) async throws -> (
        HTTPResponse, HTTPBody?
    ) {
        let bytes: Data = if let body { try await Data(collecting: body, upTo: 1 << 20) } else { Data() }
        let url = baseURL.absoluteString + (request.path ?? "")
        lock.withLock {
            requests.append(
                Recorded(
                    method: request.method, url: url, authorization: request.headerFields[.authorization], body: bytes))
        }
        switch answer(request) {
        case .fail(let error):
            throw error
        case .json(let status, let text):
            var fields = HTTPFields()
            fields[.contentType] = "application/json"
            return (HTTPResponse(status: .init(code: status), headerFields: fields), HTTPBody(text))
        }
    }
}

struct Offline: Error {}

/// A WebSocket that plays a script: frames, then a drop.
final class FakeSocket: EventSocket, @unchecked Sendable {
    private let lock = NSLock()
    private var frames: [Data]
    private let end: any Error
    private(set) var closed = false

    init(_ frames: [String], then end: any Error = Offline()) {
        self.frames = frames.map { Data($0.utf8) }
        self.end = end
    }

    func receive() async throws -> Data {
        let next: Data? = lock.withLock { frames.isEmpty ? nil : frames.removeFirst() }
        guard let next else { throw end }
        return next
    }

    func close() { lock.withLock { closed = true } }
}

/// Hands out scripted sockets in order and records each `since` it was asked for.
final class FakeConnector: EventSocketConnector, @unchecked Sendable {
    private let lock = NSLock()
    private var sockets: [Result<FakeSocket, any Error>]
    private(set) var sinceRequested: [Int?] = []

    init(_ sockets: [Result<FakeSocket, any Error>]) { self.sockets = sockets }

    var exhausted: Bool { lock.withLock { sockets.isEmpty } }

    func connect(since: Int?) async throws -> any EventSocket {
        let next: Result<FakeSocket, any Error>? = lock.withLock {
            sinceRequested.append(since)
            return sockets.isEmpty ? nil : sockets.removeFirst()
        }
        guard let next else { throw Offline() }
        return try next.get()
    }
}

/// Builds event frames shaped like contracts/events.schema.json.
enum Frames {
    static func hello(last: Int, replayed: Int = 0) -> String {
        #"{"id":0,"type":"hello","at":"2026-10-04T18:40:00Z","data":{"last_event_id":\#(last),"replayed":\#(replayed)}}"#
    }

    static func resync(last: Int) -> String {
        #"{"id":0,"type":"resync.required","at":"2026-10-04T18:40:00Z","data":{"last_event_id":\#(last)}}"#
    }

    static func message(id: Int, text: String) -> String {
        #"""
        {"id":\#(id),"type":"message.created","at":"2026-10-04T18:05:0\#(id % 10)Z","data":{"id":"msg_\#(id)","conversation_id":"cnv_main_marcel","role":"user","kind":"text","text":"\#(text)","created_at":"2026-10-04T18:05:0\#(id % 10).123Z"}}
        """#
    }

    static func task(id: Int, taskId: String, state: String, type: String = "task.updated") -> String {
        #"""
        {"id":\#(id),"type":"\#(type)","at":"2026-10-04T18:06:00Z","data":{"id":"\#(taskId)","agent_id":"agt_marcel","title":"Check disk usage","location":"nuc","state":"\#(state)","model":"sonnet","origin":"marcel","created_at":"2026-10-04T18:06:00Z","updated_at":"2026-10-04T18:06:\#(String(format: "%02d", id % 60))Z"}}
        """#
    }
}
