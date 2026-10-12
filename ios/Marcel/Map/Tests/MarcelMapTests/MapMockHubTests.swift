import Foundation
import MarcelChat
import MarcelKit
import XCTest

@testable import MarcelMap

/// The map and a room against the real mock hub, through the live stream, the cache and the hub's REST client the
/// screens use. Skipped unless `MARCEL_MOCK_HUB` holds the hub's server URL: start it with
/// `MOCK_DELAY=0.05 MOCK_HUB_PORT=7488 make mock-hub`, then
/// `MOCK_HUB=http://127.0.0.1:7488/api make -C ios map-integration`. Not part of the gate.
@MainActor
final class MapMockHubTests: XCTestCase {
    final class FixedToken: TokenStore, @unchecked Sendable {
        func token() throws -> String? { "map-integration-test" }
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

    func testTheMapARoomsMessageAndAMoveAgainstTheMockHub() async throws {
        guard let hub = ProcessInfo.processInfo.environment["MARCEL_MOCK_HUB"], let serverURL = URL(string: hub) else {
            throw XCTSkip("Set MARCEL_MOCK_HUB (make -C ios map-integration) to run against the mock hub.")
        }
        let tokens = FixedToken()
        let cache = MarcelCache(container: try MarcelCache.makeContainer(inMemory: true))
        let stream = EventStream(connector: URLSessionEventSocketConnector(serverURL: serverURL, tokenStore: tokens),
                                 sink: cache)
        let streaming = Task { await stream.run() }
        defer { streaming.cancel() }
        let service = LiveProjectService(client: HubClient.make(serverURL: serverURL, tokenStore: tokens))
        let model = MapModel(service: service, cache: cache)
        func input() throws -> MapInput { try MapInput.read(from: cache.container.mainContext) }
        func map() throws -> ProjectMap { ProjectMap(projects: try input().projects, artifacts: try input().artifacts,
                                                     now: Date()) }
        func room(_ id: String) throws -> ProjectRoom? {
            let input = try input()
            return ProjectRoom(projectId: id, tasks: input.tasks, artifacts: input.artifacts, projects: input.projects,
                               now: Date())
        }

        // The map: the mock's three projects, the inbox last.
        await model.refreshProjects()
        XCTAssertNil(model.problem)
        XCTAssertEqual(try map().tiles.last?.name, "Inbox")
        XCTAssertGreaterThanOrEqual(try map().tiles.count, 3)

        // Open Energy contracts: its board's rows and notes come from the hub.
        let energy = try XCTUnwrap(try room("prj_01JA0A1"))
        await model.open(energy)
        let opened = try XCTUnwrap(try room("prj_01JA0A1"))
        await model.loadBoard(opened.board)
        guard case .rows(let rows) = model.boards[opened.board.key] else {
            return XCTFail("the board did not load: \(String(describing: model.boards[opened.board.key]))")
        }
        XCTAssertFalse(rows.isEmpty)
        XCTAssertNotNil(model.notes[opened.slug])

        // A message from the room: stored in the main conversation with the project's tag. It starts the mock's
        // items run, whose thread is filed in this project.
        model.drafts[energy.id] = "Compare energy offers"
        await model.send(projectId: energy.id, projectName: energy.name)
        XCTAssertNil(model.problem)
        let chat = try FeedInput.read(from: cache.container.mainContext, conversationId: "cnv_main_marcel", limit: 50)
        let tagged = chat.messages.first { $0.text == "Compare energy offers" }
        XCTAssertEqual(tagged?.projectId, energy.id)
        try await waitUntil("a quest in the room") { try room(energy.id)?.questIds.isEmpty == false }
        let taskId = try XCTUnwrap(try room(energy.id)?.questIds.first)

        // Move it to the inbox: both rooms change.
        await model.move(taskId: taskId, to: "prj_inbox")
        XCTAssertNil(model.problem)
        XCTAssertFalse(try room(energy.id)?.questIds.contains(taskId) ?? true)
        try await waitUntil("the quest in the inbox") { try room("prj_inbox")?.questIds.contains(taskId) == true }

        // An unknown quest is refused in plain words.
        await model.move(taskId: "tsk_does_not_exist", to: "prj_inbox")
        XCTAssertEqual(model.problem, "The hub no longer has this quest.")
    }
}
