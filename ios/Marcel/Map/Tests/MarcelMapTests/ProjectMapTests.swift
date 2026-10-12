import Foundation
import MarcelKit
import MarcelQuests
import XCTest

@testable import MarcelMap

/// The map's and the rooms' rules, on the data the screens are drawn from: counts, ordering, words.
@MainActor
final class ProjectMapTests: XCTestCase {
    func testTilesComeMostRecentFirstWithTheInboxLast() throws {
        let map = try FedCache.seeded().map()
        XCTAssertEqual(map.tiles.map(\.name), ["Home energy", "Family", "Marcel dev", "Health", "Inbox"],
                       "the inbox changed most recently but stays last")
    }

    func testATileShowsItsCountsItemsBoardTimeAndNeedsYouBadge() throws {
        let map = try FedCache.seeded().map()
        let energy = try XCTUnwrap(map.tiles.first { $0.id == "prj_energy" })
        XCTAssertEqual(energy.needsYou, "1 needs you")
        XCTAssertEqual(energy.detail, "1 working · 2 items", "the board is not counted as an item")
        XCTAssertEqual(energy.updated, "Board updated 03:32")
        XCTAssertNil(energy.claudeProject)
        XCTAssertEqual(energy.spoken, "Home energy. 1 thing needs you. 1 working. 2 items. Board updated at 03:32.")

        let family = try XCTUnwrap(map.tiles.first { $0.id == "prj_family" })
        XCTAssertNil(family.needsYou)
        XCTAssertEqual(family.detail, "1 working · 4 items")
        XCTAssertEqual(family.updated, "Updated 02:00", "without its board cached, the project's own time")
    }

    func testAProjectLinkedToClaudeCodeShowsTheLinkAndAReadyPR() throws {
        let map = try FedCache.seeded().map()
        let dev = try XCTUnwrap(map.tiles.first { $0.id == "prj_dev" })
        XCTAssertEqual(dev.claudeProject, URL(string: "https://claude.ai/project/marcel-dev"))
        XCTAssertEqual(dev.detail, "3 working · 1 item · PR ready")
        XCTAssertTrue(dev.spoken.hasSuffix("A pull request is ready for review. Updated at 01:00. "
            + "Linked to a Claude Code project."), dev.spoken)
    }

    func testAQuietProjectSaysSo() throws {
        let map = try FedCache.seeded().map()
        let health = try XCTUnwrap(map.tiles.first { $0.id == "prj_health" })
        XCTAssertEqual(health.detail, "quiet · 2 items")
        XCTAssertEqual(health.updated, "Updated Thu")
        XCTAssertEqual(health.spoken, "Health. Nothing running. 2 items. Updated on Thursday.")
    }

    func testDoneThisWeekAndSeveralThingsWaiting() {
        let project: Project = Fx.decode(Fx.projectJSON("prj_x", "Garden", working: 0, needsYou: 2, done: 3))
        let tile = ProjectMap(projects: [project], now: Fx.now, timeZone: Fx.utc).tiles[0]
        XCTAssertEqual(tile.needsYou, "2 needs you")
        XCTAssertEqual(tile.detail, "3 done", "waiting on you is not quiet")
        XCTAssertTrue(tile.spoken.hasPrefix("Garden. 2 things need you. 3 done this week."), tile.spoken)
    }

    func testArchivedProjectsAreNotOnTheMap() {
        let projects: [Project] = [Fx.decode(Fx.projectJSON("prj_a", "Old move", archived: true)),
                                   Fx.decode(Fx.projectJSON("prj_inbox", "Inbox", slug: "inbox"))]
        XCTAssertEqual(ProjectMap(projects: projects, now: Fx.now).tiles.map(\.id), ["prj_inbox"])
    }

    func testTimesReadAsTodayYesterdayWeekdayOrDate() {
        func when(_ ago: TimeInterval) -> When { When(Fx.now.addingTimeInterval(-ago), now: Fx.now, timeZone: Fx.utc) }
        XCTAssertEqual([when(28 * Fx.minute).short, when(28 * Fx.minute).spoken], ["03:32", "at 03:32"])
        XCTAssertEqual([when(Fx.day).short, when(Fx.day).spoken], ["yesterday", "yesterday"])
        XCTAssertEqual([when(2 * Fx.day).short, when(2 * Fx.day).spoken], ["Thu", "on Thursday"])
        XCTAssertEqual(when(10 * Fx.day).spoken, "on 23 September")
        XCTAssertEqual(when(10 * Fx.day).short.prefix(5), "23 Se")
    }

    func testTheNewProjectTileAsksMarcel() {
        XCTAssertEqual(ProjectMap.newProjectRequest, "Start a new project: ")
    }

    // MARK: A room

    func testARoomHasItsBoardQuestsByStateItemsAndMoveTargets() throws {
        let room = try XCTUnwrap(try FedCache.seeded().room("prj_energy"))
        XCTAssertEqual(room.name, "Home energy")
        XCTAssertEqual(room.goal, "Lower the bill by 20% this year")
        XCTAssertEqual(room.board.badge, "Pinned · v7")
        XCTAssertEqual(room.board.spokenBadge, "Pinned, version 7")
        XCTAssertEqual(room.board.key, "art_board_prj_energy@7")
        XCTAssertEqual(room.quests.sections.map(\.group), [.waitingOnYou, .working, .finished])
        XCTAssertEqual(room.questIds, ["t_meter", "t_solar", "t_contracts"])
        XCTAssertEqual(room.items.map(\.title), ["Comparison", "Bills 2026"], "newest first, no board")
        XCTAssertEqual(room.items.map(\.spoken), ["Document: Comparison", "File: Bills 2026"])
        XCTAssertEqual(room.moveTargets.map(\.name), ["Family", "Health", "Marcel dev", "Inbox"])
        XCTAssertEqual(room.spokenTitle, "Home energy. Lower the bill by 20% this year")
    }

    func testARoomWhoseBoardIsNotCachedYetIsStillPinned() throws {
        let room = try XCTUnwrap(try FedCache.seeded().room("prj_family"))
        XCTAssertEqual(room.board.badge, "Pinned")
        XCTAssertEqual(room.board.key, "art_board_prj_family@0")
        XCTAssertEqual(room.questIds, ["t_lunch"])
    }

    func testAProjectTheCacheNoLongerHasHasNoRoom() throws {
        XCTAssertNil(try FedCache.seeded().room("prj_gone"))
    }

    func testAQuestCardInARoomDoesNotRepeatTheProject() throws {
        let room = try XCTUnwrap(try FedCache.seeded().room("prj_energy"))
        let row = try XCTUnwrap(room.quests.sections.first?.rows.first)
        let spoken = RoomQuest(row: row, moving: false).spoken
        XCTAssertFalse(spoken.contains("Project:"), spoken)
        XCTAssertTrue(spoken.hasPrefix("Meter reading. Needs your OK. Waiting for: Send the photo of the meter."),
                      spoken)
    }
}
