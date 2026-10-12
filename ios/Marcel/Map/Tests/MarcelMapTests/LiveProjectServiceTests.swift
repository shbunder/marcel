import Foundation
import HTTPTypes
import MarcelKit
import OpenAPIRuntime
import XCTest

@testable import MarcelMap

/// `LiveProjectService` on the real generated client, with only the transport stubbed: the requests the rooms send,
/// and the hub's real error replies turned into the words the room shows.
@MainActor
final class LiveProjectServiceTests: XCTestCase {
    final class StubTransport: ClientTransport, @unchecked Sendable {
        enum Answer {
            case json(Int, String)
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
            case .json(let status, let json):
                var fields = HTTPFields()
                fields[.contentType] = "application/json"
                return (HTTPResponse(status: .init(code: status), headerFields: fields), HTTPBody(json))
            }
        }
    }

    private func service(_ answer: @escaping @Sendable (HTTPRequest) -> StubTransport.Answer)
        -> (LiveProjectService, StubTransport)
    {
        let transport = StubTransport(answer)
        let client = HubClient.make(serverURL: URL(string: "http://hub.test/api")!, transport: transport)
        return (LiveProjectService(client: client), transport)
    }

    nonisolated private static func error(_ code: String, _ message: String) -> String {
        #"{"code":"\#(code)","message":"\#(message)"}"#
    }

    nonisolated private static func json(_ object: Any) -> String {
        String(decoding: try! JSONSerialization.data(withJSONObject: object), as: UTF8.self)
    }

    private func words<T>(_ call: () async throws -> T) async -> String? {
        do {
            _ = try await call()
            return nil
        } catch {
            return MapError.words(for: error)
        }
    }

    // MARK: Moving a quest

    func testAMoveToAnArchivedProjectSaysTheQuestStayed() async {
        let (hub, transport) = service { _ in
            .json(409, Self.error("project_archived", "The target project is archived."))
        }
        let said = await words { try await hub.move(taskId: "t1", to: "prj_old") }
        XCTAssertEqual(said, "That project is archived, so the quest stayed where it was.")
        XCTAssertEqual(transport.requests, ["POST /tasks/t1/move"])
        XCTAssertEqual(transport.sentBodies.first.map(Self.object)?["project_id"] as? String, "prj_old")
    }

    func testAMoveThatWorksReturnsTheTaskInItsNewProject() async throws {
        let (hub, _) = service { _ in
            .json(200, Self.json(Fx.taskJSON("t1", "Solar quotes", state: "working", project: "prj_family")))
        }
        let task = try await hub.move(taskId: "t1", to: "prj_family")
        XCTAssertEqual(task.projectId, "prj_family")
    }

    func testANetworkErrorSaysTheHubCannotBeReached() async {
        let (hub, _) = service { _ in .fail(URLError(.notConnectedToInternet)) }
        let move = await words { try await hub.move(taskId: "t1", to: "prj_family") }
        let send = await words {
            try await hub.send(text: "Hi", clientId: "c1", conversationId: Fx.conversation, projectId: "prj_family")
        }
        let projects = await words { try await hub.projects() }
        XCTAssertEqual(move, "Couldn't reach the hub. Check the connection and try again.")
        XCTAssertEqual(send, move)
        XCTAssertEqual(projects, move)
    }

    func testOtherMoveAnswersAreNamedPlainly() async {
        let (hub, _) = service { request in
            request.path?.contains("/gone/") == true ? .json(404, Self.error("task_not_found", "No."))
                : .json(401, Self.error("unauthorized", "No."))
        }
        let gone = await words { try await hub.move(taskId: "gone", to: "prj_family") }
        let refused = await words { try await hub.move(taskId: "t1", to: "prj_family") }
        XCTAssertEqual(gone, "The hub no longer has this quest.")
        XCTAssertEqual(refused, "The hub no longer accepts this phone. Pair it again.")
    }

    // MARK: A room's message

    func testARoomsMessageGoesToTheMainConversationWithItsProject() async throws {
        let (hub, transport) = service { _ in
            .json(201, Self.json(Fx.messageJSON("msg_1", text: "Hi", project: "prj_energy", clientId: "c1")))
        }
        let message = try await hub.send(text: "Hi", clientId: "c1", conversationId: Fx.conversation,
                                         projectId: "prj_energy")
        XCTAssertEqual(message.projectId, "prj_energy")
        XCTAssertEqual(transport.requests, ["POST /conversations/cnv_main_marcel/messages"])
        let body = try XCTUnwrap(transport.sentBodies.first.map(Self.object))
        XCTAssertEqual(body["project_id"] as? String, "prj_energy")
        XCTAssertEqual(body["client_id"] as? String, "c1")
        XCTAssertEqual(body["text"] as? String, "Hi")
    }

    func testAMessageForAProjectTheHubDoesNotKnowSaysSo() async {
        let (hub, _) = service { _ in .json(400, Self.error("invalid_request", "Unknown project_id.")) }
        let said = await words {
            try await hub.send(text: "Hi", clientId: "c1", conversationId: Fx.conversation, projectId: "prj_gone")
        }
        XCTAssertEqual(said, "The hub doesn't know this project any more. Go back to the map and try again.")
    }

    // MARK: The map and a room's reads

    func testProjectsAreListedForEveryAgent() async throws {
        let (hub, transport) = service { request in
            if request.path == "/me" {
                return .json(200, Self.me)
            }
            return .json(200, Self.json(Fx.projects))
        }
        let projects = try await hub.projects()
        XCTAssertEqual(projects.count, 5)
        XCTAssertEqual(transport.requests, ["GET /me", "GET /projects?agent_id=agt_marcel"])
    }

    func testNotesAreTheProjectsMemoryFileAndMissingNotesAreNone() async throws {
        let (hub, transport) = service { request in
            request.path?.contains("family") == true ? .json(404, Self.error("memory_file_not_found", "No."))
                : .json(200, Self.json(["path": "memory/projects/energy.md", "content": "- Supplier A",
                                        "etag": "e1", "updated_at": Fx.ago(0)]))
        }
        let energy = try await hub.notes(slug: "energy")
        let family = try await hub.notes(slug: "family")
        XCTAssertEqual(energy, "- Supplier A")
        XCTAssertNil(family)
        XCTAssertEqual(transport.requests.first, "GET /memory/files/memory%2Fprojects%2Fenergy.md",
                       "the path is one segment, as the contract asks")
    }

    func testABoardThatCannotBeLoadedSaysSoInTheCard() async {
        let (hub, _) = service { _ in .fail(URLError(.timedOut)) }
        let model = MapModel(service: hub)
        let board = BoardCard(artifactId: "art_board", version: 2)
        await model.loadBoard(board)
        XCTAssertEqual(model.boards[board.key], .failed("Couldn't load the board. It shows once the hub is back."))
    }

    /// The contract's example of `GET /me`.
    nonisolated private static let me = #"""
        {"user_id": "usr_owner", "name": "Shaun", "device_id": "dev_01J9ZQ4K2B", "push": {"apns": false, "ntfy": true,
         "ntfy_server": "https://ntfy.sh"}, "agents": [{"id": "agt_marcel", "name": "Marcel", "animal": "giraffe",
         "palette": "marcel", "seed": "Marcel", "main_conversation_id": "cnv_main_marcel",
         "state": {"kind": "working", "active_tasks": 2}, "model_brain": "opus", "model_worker": "sonnet"}]}
        """#

    nonisolated private static func object(_ text: String) -> [String: Any] {
        (try? JSONSerialization.jsonObject(with: Data(text.utf8))) as? [String: Any] ?? [:]
    }
}
