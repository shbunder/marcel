import Foundation
import MarcelKit
import SwiftData
import XCTest

@testable import MarcelQuests

/// The story's UI tests, run one layer down. XCUITest needs the iOS 26.5 platform this Mac's Xcode lacks (see
/// ios/README.md), so these drive what the screen is built from: a real in-memory cache fed with the live stream's
/// event frames, the screen's model with a fake hub behind it, and the board read back the way the screen's
/// `@Query`s read it.
@MainActor
final class QuestsLiveTests: XCTestCase {
    private func seeded() throws -> FedCache {
        let fed = try FedCache()
        for project in Fx.projects.map({ Fx.projectJSON($0.id, $0.name, slug: $0.slug) }) {
            try fed.apply("project.created", project)
        }
        try fed.apply("task.created", Fx.taskJSON("t_energy", "Energy contracts", state: "working", active: 2 * Fx.minute))
        try fed.apply("task.created", Fx.taskJSON("t_lunch", "Sunday lunch", state: "working", project: "prj_family",
                                                  active: 4 * Fx.minute))
        try fed.apply("task.created", Fx.taskJSON("t_dentist", "Dentist booking", state: "needs_you",
                                                  project: "prj_family", active: Fx.hour))
        try fed.apply("task.created", Fx.taskJSON("t_bins", "Paper bins", state: "done", project: "prj_inbox",
                                                  active: Fx.day))
        return fed
    }

    // UI test: a task moving to needs_you jumps to the top.
    func testATaskMovingToNeedsYouJumpsToTheTop() throws {
        let fed = try seeded()
        XCTAssertEqual(try fed.board().layout[.waitingOnYou], ["t_dentist"])
        XCTAssertEqual(try fed.board().layout[.working], ["t_energy", "t_lunch"])

        try fed.apply("task.updated", Fx.taskJSON(
            "t_lunch", "Sunday lunch", state: "needs_you", project: "prj_family", active: 0,
            extra: ["waiting_for": "Garden or terrace?"]))

        let board = try fed.board()
        XCTAssertEqual(board.groups.first, .waitingOnYou, "waiting on you is pinned to the top")
        XCTAssertEqual(board.sections.first?.rows.first?.id, "t_lunch", "the quest that just asked is the first card")
        XCTAssertEqual(board.layout[.waitingOnYou], ["t_lunch", "t_dentist"])
        XCTAssertEqual(board.layout[.working], ["t_energy"])
        XCTAssertTrue(board.row("t_lunch")?.detail.hasSuffix("Garden or terrace?") ?? false)
    }

    // UI test: filtering by project hides the others.
    func testFilteringByProjectHidesTheOthers() throws {
        let fed = try seeded()
        let model = QuestsModel(service: nil, cache: fed.cache)
        model.selectProject("prj_family")

        let board = try fed.board(model.filter)
        XCTAssertEqual(board.layout, [.waitingOnYou: ["t_dentist"], .working: ["t_lunch"]])
        XCTAssertEqual(board.projectTitle, "Family")

        model.selectProject(nil)
        XCTAssertEqual(try fed.board(model.filter).layout[.working], ["t_energy", "t_lunch"])
    }

    func testAQuestMovedToAnotherProjectFollowsTheFilter() throws {
        let fed = try seeded()
        try fed.apply("task.moved", ["task_id": "t_energy", "from_project_id": "prj_energy",
                                     "to_project_id": "prj_family"])
        XCTAssertEqual(try fed.board(QuestFilter(projectId: "prj_family")).layout[.working], ["t_energy", "t_lunch"])
        XCTAssertNil(try fed.board(QuestFilter(projectId: "prj_energy")).row("t_energy"))
    }

    // UI test: archiving a done task moves it under "Archived".
    func testArchivingADoneTaskMovesItUnderArchived() async throws {
        let fed = try seeded()
        let hub = FakeQuestService(tasks: [Fx.taskJSON("t_bins", "Paper bins", state: "done", project: "prj_inbox",
                                                       active: Fx.day)])
        let model = QuestsModel(service: hub, cache: fed.cache)
        XCTAssertEqual(try fed.board().layout[.finished], ["t_bins"])

        await model.archive("t_bins")

        XCTAssertNil(model.problem)
        XCTAssertEqual(hub.asked, ["archive t_bins"])
        XCTAssertNil(try fed.board().row("t_bins"), "an archived quest leaves the log at once")
        await model.showArchived(true)
        XCTAssertEqual(model.filter, QuestFilter(archived: true))
        let archived = try fed.board(model.filter)
        XCTAssertEqual(archived.layout, [.archived: ["t_bins"]])
        XCTAssertEqual(archived.projectTitle, "All projects")
        XCTAssertEqual(archived.filter.status, .archived)
        XCTAssertTrue(archived.row("t_bins")?.spoken.hasSuffix("Archived.") ?? false)
    }

    func testTheArchivedFilterFetchesArchivedQuestsFromTheHub() async throws {
        let fed = try seeded()
        let hub = FakeQuestService(tasks: [])
        hub.archived = [Fx.taskJSON("t_old", "Old trip", state: "done", project: "prj_family", active: 40 * Fx.day,
                                    extra: ["archived_at": Fx.ago(30 * Fx.day)])]
        let model = QuestsModel(service: hub, cache: fed.cache)

        await model.showArchived(true)

        XCTAssertEqual(hub.asked, ["archived"])
        XCTAssertEqual(try fed.board(model.filter).layout, [.archived: ["t_old"]])
        await model.showArchived(false)
        XCTAssertNil(try fed.board(model.filter).row("t_old"))
    }

    func testASwipeAloneNeverStopsAQuest() async throws {
        let fed = try seeded()
        let hub = FakeQuestService(tasks: [Fx.taskJSON("t_energy", "Energy contracts", state: "working")])
        let model = QuestsModel(service: hub, cache: fed.cache)
        let row = try XCTUnwrap(try fed.board().row("t_energy"))

        model.askToStop(row)
        XCTAssertEqual(model.pendingStop?.id, "t_energy", "the swipe asks first")
        XCTAssertEqual(hub.asked, [], "the swipe sends nothing to the hub")
        XCTAssertEqual(try fed.board().row("t_energy")?.state, .working)

        model.cancelStop()
        XCTAssertNil(model.pendingStop)
        await model.confirmStop()
        XCTAssertEqual(hub.asked, [], "after \"Keep it running\" there is nothing left to confirm")

        model.askToStop(row)
        await model.confirmStop()
        XCTAssertEqual(hub.asked, ["stop t_energy"], "only the confirmation stops it")
        XCTAssertNil(model.pendingStop)
        XCTAssertEqual(try fed.board().row("t_energy")?.state, .stopped)
    }

    func testAFinishedQuestCannotBeAskedToStop() throws {
        let fed = try seeded()
        let model = QuestsModel(service: FakeQuestService(tasks: []), cache: fed.cache)
        model.askToStop(try XCTUnwrap(try fed.board().row("t_bins")))
        XCTAssertNil(model.pendingStop)
    }

    func testStoppingARunningQuestMovesItToDone() async throws {
        let fed = try seeded()
        let hub = FakeQuestService(tasks: [Fx.taskJSON("t_energy", "Energy contracts", state: "working")])
        let model = QuestsModel(service: hub, cache: fed.cache)

        await model.stop("t_energy")

        let board = try fed.board()
        XCTAssertEqual(board.layout[.working], ["t_lunch"])
        XCTAssertEqual(board.layout[.finished]?.first, "t_energy")
        XCTAssertEqual(board.row("t_energy")?.state, .stopped)
        XCTAssertEqual(board.row("t_energy")?.canArchive, true, "a stopped quest can be archived next")
    }

    func testAStopTheNUCDidNotAnswerLeavesTheQuestRunningAndSaysSo() async throws {
        let fed = try seeded()
        let hub = FakeQuestService(tasks: [Fx.taskJSON("t_energy", "Energy contracts", state: "working")])
        hub.stopError = .runnerUnreachable
        let model = QuestsModel(service: hub, cache: fed.cache)

        await model.stop("t_energy")

        XCTAssertEqual(try fed.board().row("t_energy")?.state, .working)
        XCTAssertEqual(model.problem, "The NUC didn't answer, so the quest is still running. Try again in a minute.")
        XCTAssertTrue(model.busy.isEmpty)
    }

    func testArchivingAQuestThatIsNotFinishedSaysWhy() async throws {
        let fed = try seeded()
        let hub = FakeQuestService(tasks: [Fx.taskJSON("t_energy", "Energy contracts", state: "working")])
        let model = QuestsModel(service: hub, cache: fed.cache)
        await model.archive("t_energy")
        XCTAssertEqual(model.problem, "Only a finished quest can be archived.")
        XCTAssertEqual(try fed.board().row("t_energy")?.state, .working)
    }

    func testWithoutAPairedHubASwipeSaysSoInsteadOfFailingQuietly() async throws {
        let fed = try seeded()
        let model = QuestsModel(service: nil, cache: fed.cache)
        await model.stop("t_energy")
        XCTAssertEqual(model.problem, "This phone isn't paired with a hub yet.")
    }

    func testTheNextActionClearsTheLastProblem() async throws {
        let fed = try seeded()
        let hub = FakeQuestService(tasks: [Fx.taskJSON("t_energy", "Energy contracts", state: "working")])
        hub.stopError = .offline
        let model = QuestsModel(service: hub, cache: fed.cache)
        await model.stop("t_energy")
        XCTAssertEqual(model.problem, "Couldn't reach the hub. Check the connection and try again.")
        hub.stopError = nil
        await model.stop("t_energy")
        XCTAssertNil(model.problem)
    }

    func testAPullRequestArrivingMovesADoneQuestToReadyForReview() throws {
        let fed = try seeded()
        try fed.apply("task.updated", Fx.taskJSON("t_energy", "Energy contracts", state: "done", active: 0))
        XCTAssertEqual(try fed.board().layout[.finished]?.first, "t_energy")
        try fed.apply("artifact.created", Fx.prJSON("art_pr", task: "t_energy", number: 14))
        let board = try fed.board()
        XCTAssertEqual(board.layout[.readyForReview], ["t_energy"])
        XCTAssertTrue(board.row("t_energy")?.detail.hasSuffix("PR #14 · checks passed") ?? false)
    }

    func testARowThatNoLongerDecodesIsSkippedNotFatal() throws {
        let fed = try seeded()
        let rows = try fed.cache.container.mainContext.fetch(FetchDescriptor<CachedTask>())
        rows.first { $0.id == "t_energy" }?.payload = Data("{".utf8)
        let board = try fed.board()
        XCTAssertNil(board.row("t_energy"))
        XCTAssertEqual(board.layout[.working], ["t_lunch"])
    }
}
