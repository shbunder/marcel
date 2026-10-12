import Foundation
import MarcelChat
import MarcelKit
import SwiftData
import XCTest

@testable import MarcelMap

/// The story's UI tests, run one layer down. XCUITest needs the iOS 26.5 platform this Mac's Xcode lacks (see
/// ios/README.md), so these drive what the screens are built from: a real in-memory cache fed with the live stream's
/// event frames, the screens' model with a fake hub behind it, and the map, the rooms and the main chat read back the
/// way their `@Query`s read them.
@MainActor
final class MapLiveTests: XCTestCase {
    // UI test: open a project, see its board and quests, post a message that appears in the main chat tagged.
    func testOpenAProjectSeeItsBoardAndQuestsAndPostAMessageTheMainChatShowsTagged() async throws {
        let fed = try FedCache.seeded()
        let hub = FakeProjectService()
        hub.notesBySlug["energy"] = "# Home energy\n- Supplier A from 1 November\n- Solar: three installers asked"
        let model = MapModel(service: hub, cache: fed.cache)

        // Open the room: its items, its notes and its board's rows.
        let room = try XCTUnwrap(try fed.room("prj_energy"))
        await model.open(room)
        await model.loadBoard(room.board)
        XCTAssertEqual(hub.asked, ["artifacts prj_energy", "notes energy", "content art_board_prj_energy"])
        XCTAssertEqual(model.boards[room.board.key], .rows([
            PreviewRow("Contract switched (−€486/yr)"), PreviewRow("Solar quote Friday"),
            PreviewRow("Heat pump: wait for subsidy news"),
        ]))
        XCTAssertEqual(model.notes["energy"], .text(hub.notesBySlug["energy"]!))
        XCTAssertEqual(room.questIds, ["t_meter", "t_solar", "t_contracts"])

        // Post from the room.
        model.drafts["prj_energy"] = "  Did the second installer reply?  "
        await model.send(projectId: room.id, projectName: room.name)

        XCTAssertNil(model.problem)
        XCTAssertEqual(hub.sent.map(\.text), ["Did the second installer reply?"])
        XCTAssertEqual(hub.sent.first?.conversationId, Fx.conversation, "to the main conversation, not a room's own")
        XCTAssertEqual(hub.sent.first?.projectId, "prj_energy", "tagged with the project (B-32)")
        XCTAssertEqual(model.drafts["prj_energy"], "")
        XCTAssertEqual(model.sent["prj_energy"], "Sent to Marcel about Home energy. His answer comes in Chat.")

        // The main chat shows it at once, tagged with the project.
        let chat = try FeedInput.read(from: fed.cache.container.mainContext, conversationId: Fx.conversation,
                                      limit: ChatModel.pageSize)
        let users = ChatFeed.items(chat).compactMap { if case .user(let line) = $0 { line } else { nil } }
        XCTAssertEqual(users.map(\.text), ["Did the second installer reply?"])
        XCTAssertEqual(users.first?.project, "Home energy")
        XCTAssertEqual(users.first?.accessibilityText, "You said, about Home energy: Did the second installer reply?")
    }

    // UI test: moving a thread updates both project rooms.
    func testMovingAThreadUpdatesBothRooms() async throws {
        let fed = try FedCache.seeded()
        let hub = FakeProjectService()
        let model = MapModel(service: hub, cache: fed.cache)
        let energy = try XCTUnwrap(try fed.room("prj_energy"))
        XCTAssertEqual(energy.moveTargets.map(\.id), ["prj_family", "prj_health", "prj_dev", "prj_inbox"])

        await model.move(taskId: "t_solar", to: "prj_family")

        XCTAssertNil(model.problem)
        XCTAssertEqual(hub.asked, ["move t_solar to prj_family"])
        XCTAssertEqual(try fed.room("prj_energy")?.questIds, ["t_meter", "t_contracts"], "gone from Home energy")
        XCTAssertEqual(try fed.room("prj_family")?.questIds, ["t_solar", "t_lunch"], "in Family, newest first")
        XCTAssertTrue(model.moving.isEmpty)

        // The stream then confirms it: task.moved, and both projects' new counts.
        try fed.apply("task.moved", ["task_id": "t_solar", "from_project_id": "prj_energy",
                                     "to_project_id": "prj_family"])
        try fed.apply("project.updated", Fx.projectJSON("prj_energy", "Home energy", working: 0, needsYou: 1,
                                                        updated: 0))
        try fed.apply("project.updated", Fx.projectJSON("prj_family", "Family", working: 2, updated: 0))
        XCTAssertEqual(try fed.room("prj_family")?.questIds, ["t_solar", "t_lunch"])
        let map = try fed.map()
        XCTAssertEqual(map.tiles.first { $0.id == "prj_family" }?.detail, "2 working · 4 items")
        XCTAssertEqual(map.tiles.first { $0.id == "prj_energy" }?.detail, "2 items")
    }

    func testAMoveFromAnotherPhoneReachesBothRoomsThroughTheStream() throws {
        let fed = try FedCache.seeded()
        try fed.apply("task.moved", ["task_id": "t_meter", "from_project_id": "prj_energy",
                                     "to_project_id": "prj_inbox"])
        XCTAssertEqual(try fed.room("prj_energy")?.questIds, ["t_solar", "t_contracts"])
        XCTAssertEqual(try fed.room("prj_inbox")?.questIds, ["t_meter"])
        XCTAssertEqual(try fed.room("prj_inbox")?.quests.sections.first?.group, .waitingOnYou)
    }

    func testMovingToAnArchivedProjectLeavesTheQuestAndSaysWhy() async throws {
        let fed = try FedCache.seeded()
        let hub = FakeProjectService()
        hub.archived = ["prj_health"]
        let model = MapModel(service: hub, cache: fed.cache)

        await model.move(taskId: "t_solar", to: "prj_health")

        XCTAssertEqual(model.problem, "That project is archived, so the quest stayed where it was.")
        XCTAssertEqual(try fed.room("prj_energy")?.questIds, ["t_meter", "t_solar", "t_contracts"])
        XCTAssertEqual(try fed.room("prj_health")?.questIds, [])
    }

    func testAMoveTheHubCannotBeReachedForSaysSoAndTheNextActionClearsIt() async throws {
        let fed = try FedCache.seeded()
        let hub = FakeProjectService()
        hub.moveError = .offline
        let model = MapModel(service: hub, cache: fed.cache)

        await model.move(taskId: "t_solar", to: "prj_family")
        XCTAssertEqual(model.problem, "Couldn't reach the hub. Check the connection and try again.")
        XCTAssertEqual(try fed.room("prj_energy")?.questIds, ["t_meter", "t_solar", "t_contracts"])

        hub.moveError = nil
        await model.move(taskId: "t_solar", to: "prj_family")
        XCTAssertNil(model.problem)
        XCTAssertEqual(try fed.room("prj_family")?.questIds, ["t_solar", "t_lunch"])
    }

    func testAMessageThatDidNotGetThroughStaysInTheBox() async throws {
        let fed = try FedCache.seeded()
        let hub = FakeProjectService()
        hub.sendError = .offline
        let model = MapModel(service: hub, cache: fed.cache, conversationId: Fx.conversation)
        model.drafts["prj_family"] = "Garden or terrace?"

        await model.send(projectId: "prj_family", projectName: "Family")

        XCTAssertEqual(model.problem, "Not sent. Couldn't reach the hub. Check the connection and try again.")
        XCTAssertEqual(model.drafts["prj_family"], "Garden or terrace?", "one tap to try again")
        XCTAssertNil(model.sent["prj_family"])
        XCTAssertFalse(hub.asked.contains("me"), "a known conversation is not asked for again")
        XCTAssertTrue(model.sending.isEmpty)
    }

    func testAnEmptyMessageIsNotSent() async throws {
        let hub = FakeProjectService()
        let model = MapModel(service: hub, cache: try FedCache().cache)
        model.drafts["prj_family"] = "   "
        await model.send(projectId: "prj_family", projectName: "Family")
        XCTAssertEqual(hub.asked, [])
    }

    func testWithoutAPairedHubItSaysSoInsteadOfFailingQuietly() async throws {
        let fed = try FedCache.seeded()
        let model = MapModel(service: nil, cache: fed.cache)
        await model.move(taskId: "t_solar", to: "prj_family")
        XCTAssertEqual(model.problem, "This phone isn't paired with a hub yet.")
        model.drafts["prj_family"] = "Hello"
        await model.send(projectId: "prj_family", projectName: "Family")
        XCTAssertEqual(model.problem, "This phone isn't paired with a hub yet.")
    }

    // MARK: Live updates on the map

    func testProjectUpdatedChangesATileAndItsPlace() throws {
        let fed = try FedCache.seeded()
        XCTAssertNil(try fed.map().tiles.first { $0.id == "prj_health" }?.needsYou)

        try fed.apply("project.updated", Fx.projectJSON("prj_health", "Health", needsYou: 1, updated: 0))

        let map = try fed.map()
        XCTAssertEqual(map.tiles.first?.id, "prj_health", "the project that just changed comes first")
        XCTAssertEqual(map.tiles.first?.needsYou, "1 needs you")
        XCTAssertEqual(map.tiles.last?.name, "Inbox")
    }

    func testANewProjectGetsATileAndAnArchivedOneLeaves() throws {
        let fed = try FedCache.seeded()
        try fed.apply("project.created", Fx.projectJSON("prj_garden", "Garden", updated: 0))
        XCTAssertEqual(try fed.map().tiles.first?.name, "Garden")

        try fed.apply("project.updated", Fx.projectJSON("prj_health", "Health", archived: true))
        XCTAssertFalse(try fed.map().tiles.contains { $0.id == "prj_health" })
        XCTAssertFalse(try XCTUnwrap(try fed.room("prj_energy")).moveTargets.contains { $0.id == "prj_health" },
                       "an archived project is not offered as a place to move to")
    }

    func testANewBoardVersionIsFetchedAgain() async throws {
        let fed = try FedCache.seeded()
        let hub = FakeProjectService()
        let model = MapModel(service: hub, cache: fed.cache)
        let first = try XCTUnwrap(try fed.room("prj_energy")).board
        await model.loadBoard(first)
        await model.loadBoard(first)
        XCTAssertEqual(hub.asked, ["content art_board_prj_energy"], "one fetch per version")

        try fed.apply("artifact.updated", ["artifact": Fx.boardJSON("prj_energy", version: 8, updated: 0),
                                           "version": ["artifact_id": "art_board_prj_energy", "version": 8,
                                                       "author": "the hub", "created_at": Fx.ago(0)]])
        let second = try XCTUnwrap(try fed.room("prj_energy")).board
        XCTAssertEqual(second.badge, "Pinned · v8")
        XCTAssertEqual(try fed.map().tiles.first?.updated, "Board updated 04:00")
        await model.loadBoard(second)
        XCTAssertEqual(hub.asked.count, 2)
    }

    func testTheMapFetchesProjectsTheStreamHasNotBrought() async throws {
        let fed = try FedCache()
        let hub = FakeProjectService()
        let model = MapModel(service: hub, cache: fed.cache)
        XCTAssertTrue(try fed.map().isEmpty)
        await model.refreshProjects()
        XCTAssertEqual(try fed.map().tiles.count, 5)
    }

    func testAProjectWithoutNotesSaysSo() async throws {
        let fed = try FedCache.seeded()
        let model = MapModel(service: FakeProjectService(), cache: fed.cache)
        await model.loadNotes(slug: "family")
        XCTAssertEqual(model.notes["family"], NotesState.empty)
        XCTAssertEqual(NotesView(state: .empty).lines,
                       ["No notes yet. Marcel writes down what every quest here should know."])
    }

    func testLongNotesShowTheirFirstLines() {
        let notes = (1...9).map { "- Line \($0)" }.joined(separator: "\n")
        let lines = NotesView(state: .text(notes)).lines
        XCTAssertEqual(lines.count, NotesView.maxLines + 1)
        XCTAssertEqual(lines.last, "…and 3 more lines.")
    }
}
