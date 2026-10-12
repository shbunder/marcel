import Foundation
import HTTPTypes
import MarcelKit
import OpenAPIRuntime
import XCTest

@testable import MarcelQuests

/// `LiveQuestService` on the real generated client, with only the transport stubbed: the hub's HTTP answers become
/// the words the quest log shows.
@MainActor
final class LiveQuestServiceTests: XCTestCase {
    final class StubTransport: ClientTransport, @unchecked Sendable {
        enum Answer {
            case json(Int, String)
            case fail(any Error)
        }

        private let lock = NSLock()
        private var seen: [String] = []
        private let answer: @Sendable (HTTPRequest) -> Answer

        init(_ answer: @escaping @Sendable (HTTPRequest) -> Answer) { self.answer = answer }

        var requests: [String] { lock.withLock { seen } }

        func send(_ request: HTTPRequest, body: HTTPBody?, baseURL: URL, operationID: String) async throws -> (
            HTTPResponse, HTTPBody?
        ) {
            lock.withLock { seen.append("\(request.method.rawValue) \(request.path ?? "")") }
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
        -> (LiveQuestService, StubTransport)
    {
        let transport = StubTransport(answer)
        let client = HubClient.make(serverURL: URL(string: "http://hub.test/api")!, transport: transport)
        return (LiveQuestService(client: client), transport)
    }

    nonisolated private static func error(_ code: String, _ message: String) -> String {
        #"{"code":"\#(code)","message":"\#(message)"}"#
    }

    nonisolated private static var taskJSON: String {
        let data = try! JSONSerialization.data(withJSONObject: Fx.taskJSON("t1", "Energy contracts", state: "stopped"))
        return String(decoding: data, as: UTF8.self)
    }

    private func words<T>(_ call: () async throws -> T) async -> String? {
        do {
            _ = try await call()
            return nil
        } catch {
            return QuestsModel.words(for: error)
        }
    }

    func testAStopTheNUCDidNotAnswerSaysTheQuestIsStillRunning() async {
        let (hub, transport) = service { _ in .json(503, Self.error("runner_unreachable", "The runner did not answer.")) }
        let said = await words { try await hub.stop(taskId: "t1") }
        XCTAssertEqual(said, "The NUC didn't answer, so the quest is still running. Try again in a minute.")
        XCTAssertEqual(transport.requests, ["POST /tasks/t1/stop"])
    }

    func testAStopThatWorksReturnsTheTask() async throws {
        let (hub, _) = service { _ in .json(200, Self.taskJSON) }
        let task = try await hub.stop(taskId: "t1")
        XCTAssertEqual(task.state, .stopped)
    }

    func testANetworkErrorSaysTheHubCannotBeReached() async {
        let (hub, _) = service { _ in .fail(URLError(.notConnectedToInternet)) }
        let stop = await words { try await hub.stop(taskId: "t1") }
        let archive = await words { try await hub.archive(taskId: "t1") }
        XCTAssertEqual(stop, "Couldn't reach the hub. Check the connection and try again.")
        XCTAssertEqual(archive, stop)
    }

    func testArchivingAQuestThatIsNotFinishedSaysOnlyFinishedOnesCan() async {
        let (hub, transport) = service { _ in .json(409, Self.error("task_not_finished", "Not finished.")) }
        let said = await words { try await hub.archive(taskId: "t1") }
        XCTAssertEqual(said, "Only a finished quest can be archived.")
        XCTAssertEqual(transport.requests, ["POST /tasks/t1/archive"])
    }

    func testOtherAnswersAreNamedPlainly() async {
        let (hub, _) = service { request in
            request.path?.hasSuffix("/stop") == true ? .json(404, Self.error("task_not_found", "No."))
                : .json(401, Self.error("unauthorized", "No."))
        }
        let stop = await words { try await hub.stop(taskId: "gone") }
        let archive = await words { try await hub.archive(taskId: "t1") }
        XCTAssertEqual(stop, "The hub no longer has this quest.")
        XCTAssertEqual(archive, "The hub no longer accepts this phone. Pair it again.")
    }
}
