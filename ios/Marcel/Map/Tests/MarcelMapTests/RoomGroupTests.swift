import Foundation
import MarcelKit
import XCTest

@testable import MarcelMap

/// S-12.14 (ADR-011): the project room groups its quests like Claude Code Projects' Overview.
@MainActor
final class RoomGroupTests: XCTestCase {
    typealias PR = Components.Schemas.Artifact.PrPayload.StatePayload

    func testEveryStateWithoutAPullRequest() {
        let expected: [Components.Schemas.TaskState: RoomGroup] = [
            .needsYou: .needsYou, .queued: .working, .starting: .working, .working: .working, .silent: .working,
            .unknown: .working, .done: .done, .failed: .ended, .stopped: .ended,
        ]
        XCTAssertEqual(Set(expected.keys), Set(Components.Schemas.TaskState.allCases), "every state is covered")
        for (state, group) in expected {
            XCTAssertEqual(RoomGroup.of(state, pullRequests: []), group, "\(state)")
        }
    }

    func testADoneQuestGoesByItsPullRequests() {
        let cases: [([PR], RoomGroup)] = [
            ([], .done),
            ([.open], .readyForReview),
            ([.draft], .readyForReview),
            ([.merged], .done),
            ([.closed], .ended),
            ([.merged, .closed], .done),
            ([.merged, .open], .readyForReview),
            ([.closed, .closed], .ended),
        ]
        for (prs, group) in cases {
            XCTAssertEqual(RoomGroup.of(.done, pullRequests: prs), group, "\(prs)")
        }
    }

    func testOnlyADoneQuestLooksAtItsPullRequests() {
        XCTAssertEqual(RoomGroup.of(.working, pullRequests: [.open]), .working)
        XCTAssertEqual(RoomGroup.of(.failed, pullRequests: [.merged]), .ended)
        XCTAssertEqual(RoomGroup.of(.needsYou, pullRequests: [.closed]), .needsYou)
    }

    func testARoomWithAQuestInEachGroupShowsTheFiveGroupsInOrder() throws {
        let room = try RoomGroupTests.everyGroupRoom()
        XCTAssertEqual(room.sections.map(\.group), RoomGroup.allCases)
        XCTAssertEqual(room.sections.map(\.group.title), ["Needs you", "Working", "Ready for review", "Done", "Ended"])
        XCTAssertEqual(room.sections.map { $0.rows.map(\.id) },
                       [["t_need"], ["t_work"], ["t_review"], ["t_done"], ["t_fail"]])
    }

    func testEmptyGroupsAreLeftOut() throws {
        let fed = try FedCache()
        try fed.apply("project.created", Fx.projectJSON("prj_g", "Garden"))
        try fed.apply("task.created", Fx.taskJSON("t_w", "Water the beds", state: "working", project: "prj_g"))
        XCTAssertEqual(try XCTUnwrap(try fed.room("prj_g")).sections.map(\.group), [.working])
    }

    /// A project with one quest in each group: needs you, working, done with an open PR, done with a merged one, failed.
    static func everyGroupRoom() throws -> ProjectRoom {
        let fed = try FedCache()
        try fed.apply("project.created", Fx.projectJSON("prj_app", "Recipe app", goal: "Cook from what's in the fridge"))
        try fed.apply("task.created", Fx.taskJSON("t_need", "Choose the icon", state: "needs_you", project: "prj_app"))
        try fed.apply("task.created", Fx.taskJSON("t_work", "Fix flaky login test", state: "working", project: "prj_app",
                                                  active: 10 * 60))
        try fed.apply("task.created", Fx.taskJSON("t_review", "Add dark mode", state: "done", project: "prj_app",
                                                  active: 2 * Fx.hour))
        try fed.apply("task.created", Fx.taskJSON("t_done", "Speed up CI", state: "done", project: "prj_app",
                                                  active: 3 * Fx.hour))
        try fed.apply("task.created", Fx.taskJSON("t_fail", "Try the old API", state: "failed", project: "prj_app",
                                                  active: 4 * Fx.hour))
        try fed.apply("artifact.created", Fx.artifactJSON("art_pr14", "Add dark mode", kind: "pr", project: "prj_app",
                                                          extra: ["task_id": "t_review",
                                                                  "pr": ["number": 14, "state": "open"]]))
        try fed.apply("artifact.created", Fx.artifactJSON("art_pr12", "Speed up CI", kind: "pr", project: "prj_app",
                                                          extra: ["task_id": "t_done",
                                                                  "pr": ["number": 12, "state": "merged"]]))
        return try XCTUnwrap(try fed.room("prj_app"))
    }
}
