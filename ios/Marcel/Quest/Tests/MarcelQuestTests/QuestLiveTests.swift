import Foundation
import MarcelKit
import XCTest

@testable import MarcelQuest

/// The story's UI test ("steer and approve"), run one layer down. XCUITest needs the iOS 26.5 platform this Mac's
/// Xcode lacks (see ios/README.md), so these drive what the screen is built from: the quest view's model with a fake
/// hub behind it, fed with the live stream's frames through a real in-memory cache, exactly as the screen wires it.
@MainActor
final class QuestLiveTests: XCTestCase {
    private func opened(history: [TaskEvent] = Fx.energyHistory, task: [String: Any]? = nil) async throws
        -> (QuestViewModel, FakeQuestViewService, MarcelCache)
    {
        let hub = FakeQuestViewService(history: history)
        if let task { hub.task = task }
        let cache = MarcelCache(container: try MarcelCache.makeContainer(inMemory: true))
        let model = QuestViewModel(taskId: Fx.taskId, service: hub, cache: cache)
        // What `QuestView` does when it appears: listen to the stream, then read the history.
        cache.observeFrames { model.receive($0) }
        await model.load()
        return (model, hub, cache)
    }

    // UI test: steer and approve.
    func testSteerAndApprove() async throws {
        let (model, hub, cache) = try await opened()
        XCTAssertEqual(model.steps.map(\.mark), [.done, .done, .current])
        XCTAssertEqual(model.statusOf("apr_engie"), .open, "the quest waits on the inline prompt")

        // Approve in place.
        await model.answer("apr_engie", approve: true)
        XCTAssertEqual(hub.asked.last, "approve apr_engie")
        XCTAssertEqual(model.statusOf("apr_engie"), .approved(elsewhere: false))
        guard case .approval(_, _, _, .approved(false)) = model.rows.last?.kind else {
            return XCTFail("the prompt shows the answer: \(String(describing: model.rows.last?.kind))")
        }
        await model.answer("apr_engie", approve: false)
        XCTAssertEqual(hub.asked.filter { $0.hasSuffix("apr_engie") }.count, 1, "an answered prompt takes no second answer")

        // Steer: the message goes to this quest only, shows as sent, and leaves when the hub records it.
        model.draft = "  Also check Luminus  "
        await model.send()
        XCTAssertEqual(hub.asked.last, "steer Also check Luminus")
        XCTAssertEqual(model.draft, "")
        XCTAssertEqual(model.pending.map(\.text), ["Also check Luminus"])
        XCTAssertEqual(model.pending.first?.sent, true)

        try cache.apply(Fx.frame("task.event", Fx.eventJSON(14, "note", Fx.note("Also check Luminus")), id: 501))
        XCTAssertEqual(model.pending, [], "the recorded note replaces the pending one")
        XCTAssertEqual(model.rows.last?.kind, .note(fromUser: true, text: "Also check Luminus", handback: false,
                                                    prompt: false))
        XCTAssertNil(model.problem)
    }

    func testAnApprovalAnsweredInTheTerminalFirstShowsThatAnswer() async throws {
        let (model, hub, _) = try await opened()
        hub.answerError = .alreadyAnswered(Fx.approval("apr_engie", state: "denied", via: "terminal"))
        await model.answer("apr_engie", approve: true)
        XCTAssertEqual(model.statusOf("apr_engie"), .denied(elsewhere: true))
        XCTAssertEqual(model.problem, "It was already denied in the terminal.")
    }

    func testAnApprovalResolvedOnTheStreamClosesThePrompt() async throws {
        let (model, _, cache) = try await opened()
        try cache.apply(Fx.frame("approval.resolved", Fx.approvalJSON("apr_engie", state: "approved", via: "terminal"),
                                 id: 600))
        XCTAssertEqual(model.statusOf("apr_engie"), .approved(elsewhere: true))
        // Another quest's approval changes nothing here.
        var other = Fx.approvalJSON("apr_other", state: "approved", via: "app")
        other["task_id"] = "tsk_other"
        try cache.apply(Fx.frame("approval.resolved", other, id: 601))
        XCTAssertNil(model.approvals["apr_other"])
    }

    func testAMessageToAFinishedQuestIsKeptAndExplained() async throws {
        let (model, hub, _) = try await opened()
        hub.steerError = .finished
        model.draft = "One more thing"
        await model.send()
        XCTAssertEqual(model.pending, [])
        XCTAssertEqual(model.draft, "One more thing", "what you typed is kept, to send elsewhere")
        XCTAssertEqual(model.problem,
                       "This quest has finished, so it can't take messages. Ask Marcel to try again instead.")
    }

    func testAnEmptyMessageIsNotSent() async throws {
        let (model, hub, _) = try await opened()
        model.draft = "  \n "
        await model.send()
        XCTAssertFalse(hub.asked.contains { $0.hasPrefix("steer") })
    }

    // History from GET and live task.event frames, merged without duplicates or gaps.
    func testHistoryAndLiveEventsMergeWithoutDuplicatesOrGaps() async throws {
        let (model, hub, cache) = try await opened()
        XCTAssertEqual(model.transcript.events.map(\.seq), Array(1...13))

        // The stream replays an event already read, then sends the next one.
        try cache.apply(Fx.frame("task.event", Fx.eventJSON(13, "permission", Fx.permission("apr_engie")), id: 700))
        try cache.apply(Fx.frame("task.event", Fx.eventJSON(14, "text", Fx.text("Engie is cheaper.")), id: 701))
        XCTAssertEqual(model.transcript.events.map(\.seq), Array(1...14))

        // A reconnect skipped 15 and 16: the hub has them, so they are fetched instead of leaving a hole.
        hub.history += [
            Fx.event(14, "text", Fx.text("Engie is cheaper.")),
            Fx.event(15, "progress", ["text": "Write the comparison"]),
            Fx.event(16, "tool_call", Fx.toolCall("toolu_w", "Write", "energy/compared.md")),
            Fx.event(17, "tool_result", Fx.toolResult("toolu_w", "ok")),
        ]
        try cache.apply(Fx.frame("task.event", Fx.eventJSON(17, "tool_result", Fx.toolResult("toolu_w", "ok")), id: 702))
        XCTAssertEqual(model.transcript.missingAfter, 14)
        await model.fillGap()
        XCTAssertEqual(model.transcript.events.map(\.seq), Array(1...17))
        XCTAssertNil(model.transcript.missingAfter)
        XCTAssertEqual(hub.asked.last, "events after 14")
        XCTAssertEqual(model.steps.last?.text, "Write the comparison")

        // Another quest's events never show here.
        try cache.apply(Fx.frame("task.event", Fx.eventJSON(18, "text", Fx.text("not ours"), task: "tsk_other"),
                                 id: 703))
        XCTAssertEqual(model.transcript.lastSeq, 17)
    }

    func testALongHistoryIsReadPageByPage() async throws {
        let history = (1...450).map { Fx.event($0, "text", Fx.text("line \($0)")) }
        let (model, hub, _) = try await opened(history: history)
        XCTAssertEqual(model.transcript.events.count, 450)
        XCTAssertEqual(hub.asked.filter { $0.hasPrefix("events") }, ["events after start", "events after 200",
                                                                      "events after 400"])
    }

    func testAHistoryThatStartsLaterIsNotAGap() async throws {
        let history = (40...45).map { Fx.event($0, "text", Fx.text("kept \($0)")) }
        let (model, _, _) = try await opened(history: history)
        XCTAssertNil(model.transcript.missingAfter, "the hub kept only the newer events; nothing is missing")
        XCTAssertEqual(model.transcript.contiguousThrough, 45)
    }

    func testTheTaskFollowsTheStream() async throws {
        let (model, _, cache) = try await opened()
        try cache.apply(Fx.frame("task.updated", Fx.taskJSON(state: "done"), id: 800))
        XCTAssertEqual(model.task?.state, .done)
        XCTAssertEqual(model.steps.map(\.mark), [.done, .done, .done])
        XCTAssertEqual(model.statusOf("apr_engie"), .expired, "a finished quest's open question went away")
        XCTAssertFalse(model.canStop)
        try cache.apply(Fx.frame("task.moved", ["task_id": Fx.taskId, "from_project_id": "prj_energy",
                                                "to_project_id": "prj_home"], id: 801))
        XCTAssertEqual(model.task?.projectId, "prj_home")
    }

    // The toolbar: stop, hand back, move.
    func testStopHandBackAndMoveReachTheHub() async throws {
        let (model, hub, cache) = try await opened()
        XCTAssertTrue(model.canStop)
        await model.handBack(note: "  Try the other supplier  ")
        XCTAssertEqual(hub.asked.last, "hand back Try the other supplier")
        await model.handBack(note: "   ")
        XCTAssertEqual(hub.asked.last, "hand back -", "an empty note is not sent")

        await model.move(to: "prj_home")
        XCTAssertEqual(model.task?.projectId, "prj_home")
        XCTAssertEqual(try cache.tasks(inProject: "prj_home").map(\.id), [Fx.taskId], "the cache moves it at once")
        await model.move(to: "prj_archived")
        XCTAssertEqual(model.problem, "That project is archived, so the quest stayed where it was.")

        hub.stopError = .runnerUnreachable
        await model.stop()
        XCTAssertEqual(model.task?.state, .working)
        XCTAssertEqual(model.problem, "The NUC didn't answer, so the quest is still running. Try again in a minute.")
        hub.stopError = nil
        await model.stop()
        XCTAssertEqual(model.task?.state, .stopped)
        XCTAssertNil(model.problem)
    }

    func testWithoutAPairedHubItSaysSo() async {
        let model = QuestViewModel(taskId: Fx.taskId)
        await model.load()
        XCTAssertEqual(model.problem, "This phone isn't paired with a hub yet.")
        XCTAssertFalse(model.loading)
    }
}
