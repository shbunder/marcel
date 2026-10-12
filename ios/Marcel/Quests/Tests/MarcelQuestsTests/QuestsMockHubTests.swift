import Foundation
import MarcelKit
import XCTest

@testable import MarcelQuests

/// The quest log against the real mock hub, through the live stream, the cache and the hub's REST client the screen
/// uses. Skipped unless `MARCEL_MOCK_HUB` holds the hub's server URL: start it with
/// `MOCK_DELAY=0.2 MOCK_HUB_PORT=7488 make mock-hub`, then
/// `MOCK_HUB=http://127.0.0.1:7488/api make -C ios quests-integration`. Not part of the gate.
///
/// The mock answers stop and archive with the task as it stands (it does not act on them), so this checks that the
/// app's requests are right and the answers decode; what the answers do to the log is in `QuestsLiveTests`.
@MainActor
final class QuestsMockHubTests: XCTestCase {
    final class FixedToken: TokenStore, @unchecked Sendable {
        func token() throws -> String? { "quests-integration-test" }
        func save(_ token: String) throws {}
        func delete() throws {}
    }

    private func waitUntil(_ what: String, seconds: Double = 20, _ condition: () throws -> Bool) async throws {
        let deadline = Date().addingTimeInterval(seconds)
        while try !condition() {
            if Date() > deadline {
                XCTFail("timed out waiting for \(what)")
                throw CancellationError()
            }
            try await Task.sleep(for: .milliseconds(100))
        }
    }

    func testALiveQuestMovesToWaitingOnYouAndStopAndArchiveReachTheHub() async throws {
        guard let hub = ProcessInfo.processInfo.environment["MARCEL_MOCK_HUB"], let serverURL = URL(string: hub) else {
            throw XCTSkip("Set MARCEL_MOCK_HUB (make -C ios quests-integration) to run against the mock hub.")
        }
        let tokens = FixedToken()
        let cache = MarcelCache(container: try MarcelCache.makeContainer(inMemory: true))
        let stream = EventStream(connector: URLSessionEventSocketConnector(serverURL: serverURL, tokenStore: tokens),
                                 sink: cache)
        let streaming = Task { await stream.run() }
        defer { streaming.cancel() }
        let client = HubClient.make(serverURL: serverURL, tokenStore: tokens)
        func board() throws -> QuestBoard {
            try QuestBoard.read(from: cache.container.mainContext, filter: QuestFilter(), now: Date())
        }

        // The login run: a thread starts, works, then asks for an OK.
        let sent = try await client.sendMessage(
            path: .init(conversationId: "cnv_main_marcel"),
            body: .json(.init(text: "Fix the flaky login test", clientId: UUID().uuidString)))
        guard case .created = sent else { return XCTFail("the mock hub did not take the message: \(sent)") }

        try await waitUntil("a quest in Working") { try board().layout[.working]?.isEmpty == false }
        try await waitUntil("the quest waiting on you at the top") { try board().groups.first == .waitingOnYou }
        let taskId = try XCTUnwrap(board().sections.first?.rows.first?.id)
        XCTAssertEqual(try board().row(taskId)?.canStop, true, "a quest waiting on you can be stopped")

        let service = LiveQuestService(client: client)
        let stopped = try await service.stop(taskId: taskId)
        XCTAssertEqual(stopped.id, taskId)
        let archived = try await service.archive(taskId: taskId)
        XCTAssertEqual(archived.id, taskId)
        _ = try await service.archivedTasks()

        do {
            _ = try await service.stop(taskId: "tsk_does_not_exist")
            XCTFail("an unknown quest should be refused")
        } catch let error as QuestActionError {
            XCTAssertEqual(error, .notFound)
        }

        // Leave the mock free for the next run: say no to the open question.
        if case .ok(let ok) = try await client.listApprovals() {
            for approval in try ok.body.json where approval.state == .open {
                _ = try await client.answerApproval(path: .init(approvalId: approval.id),
                                                     body: .json(.init(decision: .deny)))
            }
        }
    }
}
