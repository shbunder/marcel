import Foundation
import MarcelKit
import XCTest

@testable import MarcelQuests

/// The grouping and ordering rules of the quest log, on plain values.
final class QuestBoardTests: XCTestCase {
    func testEveryStateLandsInItsGroupAndTheGroupsComeInOrder() {
        let tasks = [
            Fx.task("t_done", "Sunday lunch", state: "done", active: 2 * Fx.hour),
            Fx.task("t_queued", "Disk usage", state: "queued"),
            Fx.task("t_working", "Energy contracts", state: "working"),
            Fx.task("t_needs", "Dentist booking", state: "needs_you", project: "prj_family"),
            Fx.task("t_review", "Avatar sprite in app", state: "done", project: "prj_dev"),
            Fx.task("t_starting", "Book flights", state: "starting"),
            Fx.task("t_silent", "Clean photos", state: "silent"),
            Fx.task("t_unknown", "Backup check", state: "unknown"),
            Fx.task("t_failed", "Renew passport", state: "failed"),
            Fx.task("t_stopped", "Old idea", state: "stopped"),
        ]
        let board = QuestBoard(tasks: tasks, artifacts: [Fx.pr("art_pr", task: "t_review")],
                               projects: Fx.projects, now: Fx.now)

        XCTAssertEqual(board.groups, [.waitingOnYou, .working, .queued, .readyForReview, .finished])
        XCTAssertEqual(board.layout[.waitingOnYou], ["t_needs"])
        XCTAssertEqual(Set(board.layout[.working] ?? []), ["t_working", "t_starting", "t_silent", "t_unknown"])
        XCTAssertEqual(board.layout[.queued], ["t_queued"])
        XCTAssertEqual(board.layout[.readyForReview], ["t_review"])
        XCTAssertEqual(Set(board.layout[.finished] ?? []), ["t_done", "t_failed", "t_stopped"])
    }

    func testEmptyGroupsAreLeftOut() {
        let board = QuestBoard(tasks: [Fx.task("t1", "Energy contracts", state: "working")], now: Fx.now)
        XCTAssertEqual(board.groups, [.working])
    }

    func testNewestActivityIsOnTopAndTheQueueRunsOldestFirst() {
        let tasks = [
            Fx.task("w_old", "A", state: "working", active: 3 * Fx.hour),
            Fx.task("w_new", "B", state: "working", active: 1 * Fx.minute),
            Fx.task("w_mid", "C", state: "working", active: 20 * Fx.minute),
            Fx.task("q_late", "D", state: "queued", created: 5 * Fx.minute, active: 1 * Fx.minute),
            Fx.task("q_early", "E", state: "queued", created: 50 * Fx.minute, active: 40 * Fx.minute),
            Fx.task("n_old", "F", state: "needs_you", active: 2 * Fx.hour),
            Fx.task("n_new", "G", state: "needs_you", active: 2 * Fx.minute),
        ]
        let board = QuestBoard(tasks: tasks, now: Fx.now)
        XCTAssertEqual(board.layout[.working], ["w_new", "w_mid", "w_old"])
        XCTAssertEqual(board.layout[.queued], ["q_early", "q_late"], "the queue runs in the order it was joined")
        XCTAssertEqual(board.layout[.waitingOnYou], ["n_new", "n_old"])
    }

    func testEqualActivityIsOrderedByIdSoTheListNeverJumps() {
        let tasks = [
            Fx.task("t_b", "B", state: "working", active: 60),
            Fx.task("t_a", "A", state: "working", active: 60),
        ]
        XCTAssertEqual(QuestBoard(tasks: tasks, now: Fx.now).layout[.working], ["t_a", "t_b"])
        XCTAssertEqual(QuestBoard(tasks: tasks.reversed(), now: Fx.now).layout[.working], ["t_a", "t_b"])
    }

    func testLastActivityFallsBackToUpdatedAt() {
        var json = Fx.taskJSON("t_old", "A", state: "working", active: 2 * Fx.hour)
        json["last_activity_at"] = nil
        let tasks: [HubTask] = [Fx.decode(json), Fx.task("t_new", "B", state: "working", active: Fx.hour)]
        XCTAssertEqual(QuestBoard(tasks: tasks, now: Fx.now).layout[.working], ["t_new", "t_old"])
    }

    func testFinishedQuestsStayForSevenDays() {
        let tasks = [
            Fx.task("t_6d", "A", state: "done", active: 6 * Fx.day),
            Fx.task("t_7d", "B", state: "failed", active: 7 * Fx.day),
            Fx.task("t_8d", "C", state: "stopped", active: 8 * Fx.day),
            Fx.task("t_working_8d", "D", state: "working", active: 8 * Fx.day),
        ]
        let board = QuestBoard(tasks: tasks, now: Fx.now)
        XCTAssertEqual(board.layout[.finished], ["t_6d", "t_7d"])
        XCTAssertEqual(board.layout[.working], ["t_working_8d"], "only finished quests age out")
    }

    func testOnlyAnOpenOrDraftPullRequestMakesAQuestReadyForReview() {
        let tasks = [
            Fx.task("t_open", "A", state: "done"),
            Fx.task("t_draft", "B", state: "done"),
            Fx.task("t_merged", "C", state: "done"),
            Fx.task("t_closed", "D", state: "done"),
            Fx.task("t_none", "E", state: "done"),
            Fx.task("t_working_pr", "F", state: "working"),
        ]
        let prs = [
            Fx.pr("p1", task: "t_open"), Fx.pr("p2", task: "t_draft", state: "draft"),
            Fx.pr("p3", task: "t_merged", state: "merged"), Fx.pr("p4", task: "t_closed", state: "closed"),
            Fx.pr("p5", task: "t_working_pr"),
        ]
        let board = QuestBoard(tasks: tasks, artifacts: prs, now: Fx.now)
        XCTAssertEqual(Set(board.layout[.readyForReview] ?? []), ["t_open", "t_draft"])
        XCTAssertEqual(Set(board.layout[.finished] ?? []), ["t_merged", "t_closed", "t_none"])
        XCTAssertEqual(board.layout[.working], ["t_working_pr"], "a quest still working is not ready yet")
    }

    func testTheNewestPullRequestOfAQuestCounts() {
        let tasks = [Fx.task("t1", "A", state: "done")]
        let prs = [Fx.pr("p_old", task: "t1", number: 40, state: "closed", updated: Fx.hour),
                   Fx.pr("p_new", task: "t1", number: 41, state: "open", updated: Fx.minute)]
        let board = QuestBoard(tasks: tasks, artifacts: prs, now: Fx.now)
        XCTAssertEqual(board.layout[.readyForReview], ["t1"])
        XCTAssertTrue(board.row("t1")?.detail.contains("PR #41") ?? false)
    }

    func testArchivedQuestsLeaveTheLogAndAreAllTheArchivedFilterShows() {
        let tasks = [
            Fx.task("t_live", "A", state: "done"),
            Fx.task("t_arch_old", "B", state: "done", active: 20 * Fx.day, extra: ["archived_at": Fx.ago(10 * Fx.day)]),
            Fx.task("t_arch_new", "C", state: "failed", extra: ["archived_at": Fx.ago(Fx.hour)]),
        ]
        let live = QuestBoard(tasks: tasks, now: Fx.now)
        XCTAssertEqual(live.layout, [.finished: ["t_live"]])

        let archived = QuestBoard(tasks: tasks, filter: QuestFilter(archived: true), now: Fx.now)
        XCTAssertEqual(archived.layout, [.archived: ["t_arch_new", "t_arch_old"]],
                       "newest archived first, and no seven-day limit")
        XCTAssertEqual(archived.row("t_arch_new")?.canArchive, false)
        XCTAssertEqual(archived.row("t_arch_new")?.canStop, false)
    }

    func testFilteringByProjectHidesTheOtherProjects() {
        let tasks = [
            Fx.task("t_energy", "Energy contracts", state: "working", project: "prj_energy"),
            Fx.task("t_family", "Dentist booking", state: "needs_you", project: "prj_family"),
            Fx.task("t_family2", "Sunday lunch", state: "done", project: "prj_family"),
        ]
        let board = QuestBoard(tasks: tasks, projects: Fx.projects, filter: QuestFilter(projectId: "prj_family"),
                               now: Fx.now)
        XCTAssertEqual(board.layout, [.waitingOnYou: ["t_family"], .finished: ["t_family2"]])
        XCTAssertEqual(board.filterTitle, "Family")
        XCTAssertNil(board.row("t_energy"))
    }

    func testTheProjectMenuListsLiveProjectsByNameWithTheInboxLast() {
        let projects = Fx.projects + [Fx.project("prj_old", "Attic", archived: true)]
        let board = QuestBoard(tasks: [], projects: projects, now: Fx.now)
        XCTAssertEqual(board.projects.map(\.name), ["Family", "Home energy", "Marcel dev", "Inbox"])
    }

    func testFilterTitles() {
        let all = QuestBoard(tasks: [], projects: Fx.projects, now: Fx.now)
        XCTAssertEqual(all.filterTitle, "All projects")
        let archived = QuestBoard(tasks: [], projects: Fx.projects,
                                  filter: QuestFilter(projectId: "prj_energy", archived: true), now: Fx.now)
        XCTAssertEqual(archived.filterTitle, "Archived · Home energy")
    }

    func testAnEmptyLogSaysWhatToDo() {
        XCTAssertEqual(QuestBoard(tasks: [], now: Fx.now).emptyText, "No quests yet. Ask Marcel for something in Chat.")
        XCTAssertEqual(QuestBoard(tasks: [], filter: .init(archived: true), now: Fx.now).emptyText,
                       "No archived quests.")
        XCTAssertEqual(QuestBoard(tasks: [], filter: .init(projectId: "p"), now: Fx.now).emptyText,
                       "No quests in this project right now.")
        XCTAssertEqual(QuestBoard(tasks: [], filter: .init(projectId: "p", archived: true), now: Fx.now).emptyText,
                       "No archived quests in this project.")
    }

    // MARK: What a card shows

    func testACardShowsTitleProjectLocationAgeActivityAndThePullRequest() throws {
        let tasks = [Fx.task("t1", "Avatar sprite in app", state: "done", project: "prj_dev", location: "cloud",
                             created: 3 * Fx.hour, active: 12 * Fx.minute)]
        let board = QuestBoard(tasks: tasks, artifacts: [Fx.pr("p1", task: "t1", checks: "passing")],
                               projects: Fx.projects, now: Fx.now)
        let row = try XCTUnwrap(board.row("t1"))
        XCTAssertEqual(row.title, "Avatar sprite in app")
        XCTAssertEqual(row.project, "Marcel dev")
        XCTAssertEqual(row.state, .ready)
        XCTAssertEqual(row.stateText, "PR")
        XCTAssertEqual(row.detail, "PR #41 · checks passed · Cloud · 3h old · active 12m ago")
        XCTAssertEqual(row.spoken, "Avatar sprite in app. Ready for review. Project: Marcel dev. "
            + "Pull request 41, checks passed. Runs in the cloud. Started 3 hours ago. Last active 12 minutes ago.")
        XCTAssertTrue(row.canArchive)
        XCTAssertFalse(row.canStop)
    }

    func testAQuestWaitingOnYouSaysWhatItWaitsFor() throws {
        let tasks = [Fx.task("t1", "Dentist booking", state: "needs_you", project: "prj_family",
                             extra: ["waiting_for": "Pick Tue 9:10 or Thu 8:30"])]
        let row = try XCTUnwrap(QuestBoard(tasks: tasks, projects: Fx.projects, now: Fx.now).row("t1"))
        XCTAssertEqual(row.detail, "Pick Tue 9:10 or Thu 8:30 · NUC · 2h old · active 5m ago")
        XCTAssertTrue(row.spoken.hasPrefix("Dentist booking. Needs your OK. Project: Family. "
            + "Waiting for: Pick Tue 9:10 or Thu 8:30. Runs on the NUC."))
        XCTAssertTrue(row.canStop)
        XCTAssertFalse(row.canArchive)
    }

    func testStatusLinesForQueuedQuietAndUnreachableQuests() throws {
        let tasks = [
            Fx.task("t_cap", "A", state: "queued", extra: ["queued_reason": "cap"]),
            Fx.task("t_usage", "B", state: "queued", extra: ["queued_reason": "usage"]),
            Fx.task("t_silent", "C", state: "silent"),
            Fx.task("t_unknown", "D", state: "unknown"),
            Fx.task("t_failed", "E", state: "failed"),
        ]
        let board = QuestBoard(tasks: tasks, now: Fx.now)
        XCTAssertTrue(try XCTUnwrap(board.row("t_cap")).detail.hasPrefix("Waits for a free slot · "))
        XCTAssertTrue(try XCTUnwrap(board.row("t_usage")).detail.hasPrefix("Waits for the usage limit · "))
        XCTAssertTrue(try XCTUnwrap(board.row("t_silent")).detail.hasPrefix("No news past its expected time · "))
        XCTAssertTrue(try XCTUnwrap(board.row("t_unknown")).detail.hasPrefix("The NUC can't be reached · "))
        XCTAssertTrue(try XCTUnwrap(board.row("t_failed")).detail.hasPrefix("NUC · "))
        XCTAssertEqual(board.row("t_silent")?.state, .silent)
    }

    func testPullRequestWords() {
        func text(_ state: String, _ checks: String) -> String? {
            QuestBoard.prText(Fx.pr("p", task: "t", number: 7, state: state, checks: checks))?.short
        }
        XCTAssertEqual(text("open", "failing"), "PR #7 · checks failed")
        XCTAssertEqual(text("open", "pending"), "PR #7 · checks running")
        XCTAssertEqual(text("open", "none"), "PR #7")
        XCTAssertEqual(text("draft", "passing"), "PR #7 · draft · checks passed")
        XCTAssertEqual(text("merged", "passing"), "PR #7 · merged")
        XCTAssertEqual(text("closed", "failing"), "PR #7 · closed")
    }

    func testWhichQuestsCanBeStoppedOrArchived() {
        let states = ["queued", "starting", "working", "needs_you", "silent", "unknown", "done", "failed", "stopped"]
        let tasks = states.map { Fx.task("t_\($0)", $0, state: $0) }
        let board = QuestBoard(tasks: tasks, now: Fx.now)
        for state in states {
            let row = board.row("t_\(state)")
            let finished = ["done", "failed", "stopped"].contains(state)
            XCTAssertEqual(row?.canStop, !finished, state)
            XCTAssertEqual(row?.canArchive, finished, state)
        }
    }

    func testAgesInShortAndInWords() {
        func age(_ seconds: TimeInterval) -> Age { Age(from: Fx.now.addingTimeInterval(-seconds), to: Fx.now) }
        XCTAssertEqual(age(20), Age(short: "now", spoken: "just now"))
        XCTAssertEqual(age(60), Age(short: "1m", spoken: "1 minute ago"))
        XCTAssertEqual(age(59 * 60), Age(short: "59m", spoken: "59 minutes ago"))
        XCTAssertEqual(age(3600), Age(short: "1h", spoken: "1 hour ago"))
        XCTAssertEqual(age(26 * 3600), Age(short: "1d", spoken: "1 day ago"))
        XCTAssertEqual(age(-30), Age(short: "now", spoken: "just now"), "a clock slightly ahead is not negative")
        let fresh = QuestBoard(tasks: [Fx.task("t", "A", state: "working", created: 10, active: 10)], now: Fx.now)
        XCTAssertEqual(fresh.row("t")?.detail, "NUC · new · active now")
    }

    func testTheFinishedGroupCarriesItsCountAndStartsFolded() {
        let tasks = [Fx.task("a", "A", state: "done"), Fx.task("b", "B", state: "failed")]
        let section = QuestBoard(tasks: tasks, now: Fx.now).sections[0]
        XCTAssertEqual(section.title, "Done · this week (2)")
        XCTAssertTrue(section.group.startsCollapsed)
        XCTAssertFalse(QuestGroup.waitingOnYou.startsCollapsed)
    }

    func testAProjectKeepsTheSameColourAsInChat() {
        // MarcelChat's ChatFeed.tone uses the same FNV-1a hash of the project id, so the colour is stable.
        XCTAssertEqual(QuestBoard.tone(for: "prj_energy"), QuestBoard.tone(for: "prj_energy"))
        let tones = Set(Fx.projects.map { QuestBoard.tone(for: $0.id) })
        XCTAssertGreaterThan(tones.count, 1, "different projects get different colours")
    }
}

extension Age {
    /// An expected value, for comparing.
    init(short: String, spoken: String) {
        self.init(from: Fx.now, to: Fx.now)
        self.short = short
        self.spoken = spoken
    }
}
