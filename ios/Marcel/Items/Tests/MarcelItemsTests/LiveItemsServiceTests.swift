import Foundation
import HTTPTypes
import MarcelKit
import OpenAPIRuntime
import XCTest

@testable import MarcelItems

/// `LiveItemsService` on the real generated client, with only the transport stubbed: the requests the tab and the
/// viewer send (with the mock hub's recorded answers), and the hub's error replies in the words the screens show.
@MainActor
final class LiveItemsServiceTests: XCTestCase {
    final class StubTransport: ClientTransport, @unchecked Sendable {
        enum Answer {
            case body(Int, String, Data)
            case fail(any Error)
        }

        private let lock = NSLock()
        private var seen: [String] = []
        private var bodies: [String] = []
        private let answer: @Sendable (HTTPRequest) -> Answer

        init(_ answer: @escaping @Sendable (HTTPRequest) -> Answer) { self.answer = answer }

        var requests: [String] { lock.withLock { seen } }
        var sentBodies: [String] { lock.withLock { bodies } }

        func send(_ request: HTTPRequest, body: HTTPBody?, baseURL: URL, operationID: String) async throws -> (
            HTTPResponse, HTTPBody?
        ) {
            let text = if let body { try await String(collecting: body, upTo: 64 * 1024) } else { "" }
            lock.withLock {
                seen.append("\(request.method.rawValue) \(request.path ?? "")")
                if !text.isEmpty { bodies.append(text) }
            }
            switch answer(request) {
            case .fail(let error):
                throw error
            case .body(let status, let type, let data):
                var fields = HTTPFields()
                fields[.contentType] = type
                return (HTTPResponse(status: .init(code: status), headerFields: fields), HTTPBody(data))
            }
        }
    }

    nonisolated static func json(_ status: Int, _ data: Data) -> StubTransport.Answer {
        .body(status, "application/json", data)
    }

    nonisolated static func error(_ status: Int, _ code: String) -> StubTransport.Answer {
        json(status, Data(#"{"code":"\#(code)","message":"No."}"#.utf8))
    }

    private func service(_ answer: @escaping @Sendable (HTTPRequest) -> StubTransport.Answer)
        -> (LiveItemsService, StubTransport)
    {
        let transport = StubTransport(answer)
        let client = HubClient.make(serverURL: URL(string: "http://hub.test/api")!, transport: transport)
        return (LiveItemsService(client: client), transport)
    }

    private func words<T>(_ call: () async throws -> T) async -> String? {
        do {
            _ = try await call()
            return nil
        } catch {
            return ItemsError.words(for: error)
        }
    }

    func testTheListReadsTheMockHubsItems() async throws {
        let artifacts = Fx.mockHub("artifacts.json")
        let (hub, transport) = service { _ in Self.json(200, artifacts) }
        let items = try await hub.artifacts()
        XCTAssertEqual(items.map(\.id), [Fx.boardId, Fx.docId, Fx.prId, "art_01J9ZM2", "art_01J9ZM0"])
        XCTAssertEqual(transport.requests, ["GET /artifacts?limit=200"])
    }

    func testTheListFollowsTheCursor() async throws {
        let (hub, transport) = service { request in
            let first = !(request.path ?? "").contains("cursor")
            let page: [String: Any] = first
                ? ["items": [Fx.mockArtifact(Fx.docId)], "next_cursor": "c2"] : ["items": [Fx.mockArtifact(Fx.prId)]]
            return Self.json(200, try! JSONSerialization.data(withJSONObject: page))
        }
        let items = try await hub.artifacts()
        XCTAssertEqual(items.map(\.id), [Fx.docId, Fx.prId])
        XCTAssertEqual(transport.requests, ["GET /artifacts?limit=200", "GET /artifacts?cursor=c2&limit=200"])
    }

    func testContentIsAskedForByVersion() async throws {
        let doc = Fx.mockHub("doc-v2.md")
        let (hub, transport) = service { _ in .body(200, "text/markdown; charset=utf-8", doc) }
        let data = try await hub.content(id: Fx.docId, version: 2)
        XCTAssertEqual(data, doc)
        XCTAssertEqual(transport.requests, ["GET /artifacts/art_01JA0B1/content?version=2"])
    }

    func testVersionsAndOneItem() async throws {
        let versions = Fx.mockHub("doc-versions.json")
        let item = try JSONSerialization.data(withJSONObject: Fx.mockArtifact(Fx.docId))
        let (hub, transport) = service { request in
            (request.path ?? "").hasSuffix("/versions") ? Self.json(200, versions) : Self.json(200, item)
        }
        let list = try await hub.versions(id: Fx.docId)
        XCTAssertEqual(list.map(\.version), [3, 2, 1])
        XCTAssertEqual(list.first?.summary, "Added the Engie offer; Luminus is still the cheapest.")
        let one = try await hub.artifact(id: Fx.docId)
        XCTAssertEqual(one.currentVersion, 3)
        XCTAssertEqual(transport.requests, ["GET /artifacts/art_01JA0B1/versions", "GET /artifacts/art_01JA0B1"])
    }

    func testAskAboutThisSendsTheProjectTag() async throws {
        let message = try JSONSerialization.data(withJSONObject: Fx.messageJSON(
            "msg_1", text: "Why?", project: Fx.energy, clientId: "c1"))
        let (hub, transport) = service { _ in Self.json(201, message) }
        _ = try await hub.send(text: "Why?", clientId: "c1", conversationId: Fx.conversation, projectId: Fx.energy)
        XCTAssertEqual(transport.requests, ["POST /conversations/cnv_main_marcel/messages"])
        let body = try XCTUnwrap(transport.sentBodies.first.flatMap { try? JSONSerialization.jsonObject(
            with: Data($0.utf8)) as? [String: String] })
        XCTAssertEqual(body, ["text": "Why?", "client_id": "c1", "project_id": Fx.energy])
    }

    func testTheHubsRefusalsInPlainWords() async {
        let (gone, _) = service { _ in Self.error(404, "artifact_not_found") }
        let said404 = await words { try await gone.content(id: "x", version: 9) }
        XCTAssertEqual(said404, "The hub no longer has this item.")
        let saidItem = await words { try await gone.artifact(id: "x") }
        XCTAssertEqual(saidItem, "The hub no longer has this item.")

        let (refused, _) = service { _ in Self.error(401, "unauthorized") }
        let said401 = await words { try await refused.artifacts() }
        XCTAssertEqual(said401, "The hub no longer accepts this phone. Pair it again.")

        let (unknown, _) = service { _ in Self.error(400, "unknown_project") }
        let said400 = await words {
            try await unknown.send(text: "a", clientId: "b", conversationId: "c", projectId: "prj_gone")
        }
        XCTAssertEqual(said400, "The hub doesn't know this item's project any more.")

        let (down, _) = service { _ in .fail(URLError(.cannotConnectToHost)) }
        let saidDown = await words { try await down.versions(id: "x") }
        XCTAssertEqual(saidDown, "Couldn't reach the hub. Check the connection and try again.")

        let (odd, _) = service { _ in Self.error(500, "oops") }
        let said500 = await words { try await odd.artifacts() }
        XCTAssertEqual(said500, "The hub sent something the app doesn't understand (items, HTTP 500).")
    }

    func testContentTooBigForThePhoneIsRefused() async {
        let big = Data(count: LiveItemsService.maxContent + 1)
        let (hub, _) = service { _ in .body(200, "application/pdf", big) }
        let said = await words { try await hub.content(id: Fx.fileId, version: 1) }
        XCTAssertEqual(said, "This item is too big to open on the phone.")
    }
}
