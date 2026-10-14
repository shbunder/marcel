import Foundation
import MarcelKit
import SwiftUI
import UIKit
import XCTest

@testable import MarcelQuest

/// "Smooth with 5k events": a quest with 5,000 events loads in under a second on the simulator — the hub's pages
/// decoded from JSON, merged, folded into steps and rows, and the screen's lazy list laid out in a real window.
@MainActor
final class QuestPerformanceTests: XCTestCase {
    static let count = 5_000

    /// 5,000 events of every kind, the mix a long coding quest has: text, tool calls and answers, diffs, progress, and a
    /// workflow with hundreds of agents.
    static func pages() throws -> [Data] {
        var events: [[String: Any]] = []
        var seq = 0
        func add(_ type: String, _ data: [String: Any]) {
            seq += 1
            events.append(Fx.eventJSON(seq, type, data))
        }
        var n = 0
        while seq < count {
            n += 1
            add("text", Fx.text("Looking at step \(n): reading the module and its tests before changing anything."))
            add("tool_call", Fx.toolCall("toolu_\(n)", "Bash", "python3 -m pytest -q tests/test_\(n).py"))
            add("tool_result", Fx.toolResult("toolu_\(n)", (1...40).map { "line \($0) of the output" }
                .joined(separator: "\n")))
            add("diff", Fx.modelDiff)
            if n % 25 == 0 { add("progress", ["text": "Step \(n / 25)"]) }
            // A big workflow running alongside: 400 agents, each started then stopped (S-12.15).
            if n <= 400 {
                add("workflow_agent_start", ["run_id": "wf_big", "agent_id": String(n, radix: 16), "label": "agent \(n)",
                                             "phase": "Phase \(n % 4)"])
                add("workflow_agent_stop", ["run_id": "wf_big", "agent_id": String(n, radix: 16),
                                            "summary": "Agent \(n) is done."])
            }
        }
        events = Array(events.prefix(count))
        return try stride(from: 0, to: events.count, by: 200).map { start in
            let page: [String: Any] = ["items": Array(events[start..<min(start + 200, events.count)]),
                                       "has_more": start + 200 < events.count]
            return try JSONSerialization.data(withJSONObject: page)
        }
    }

    func testFiveThousandEventsLoadInUnderASecond() async throws {
        let pages = try Self.pages()
        let decoder = QuestJSON.decoder()
        let started = Date()

        // What `load()` does with the hub's answers: decode each page, merge it, rebuild steps and rows.
        let model = QuestViewModel(taskId: Fx.taskId, task: Fx.task())
        var history: [TaskEvent] = []
        for page in pages {
            history += try decoder.decode(Components.Schemas.TaskEventPage.self, from: page).items
        }
        let hub = FakeQuestViewService(task: Fx.taskJSON(), history: history)
        model.service = hub
        await model.load()
        let read = Date().timeIntervalSince(started)

        // The screen: the lazy list in a real window, laid out once.
        let host = UIHostingController(rootView: QuestScroll(model: model, meta: "Home energy · NUC · sonnet")
            .frame(width: 390, height: 844)
            .environment(\.colorScheme, .light))
        let window = UIWindow(frame: CGRect(x: 0, y: 0, width: 390, height: 844))
        window.rootViewController = host
        window.makeKeyAndVisible()
        host.view.layoutIfNeeded()
        let total = Date().timeIntervalSince(started)
        window.isHidden = true

        XCTAssertEqual(model.transcript.events.count, Self.count)
        let workflow: Set<Components.Schemas.TaskEventType> = [.workflowAgentStart, .workflowAgentStop]
        let folded = history.filter { $0._type != .toolResult && $0._type != .progress && !workflow.contains($0._type) }
            .count + 1
        XCTAssertEqual(model.rows.count, folded,
                       "a tool's answer joins its call; progress reports are steps; a workflow's agents are one row")
        XCTAssertGreaterThan(model.steps.count, 20)
        XCTAssertLessThan(total, 1.0, "5,000 events took \(String(format: "%.2f", total)) s to load and lay out "
            + "(reading and folding: \(String(format: "%.2f", read)) s)")
    }

    /// A live event on a 5,000-event quest is merged and the rows rebuilt fast enough to keep up with a busy session.
    func testALiveEventOnALongQuestIsCheap() async throws {
        let history = try Self.pages().flatMap {
            try QuestJSON.decoder().decode(Components.Schemas.TaskEventPage.self, from: $0).items
        }
        let model = QuestViewModel(taskId: Fx.taskId, task: Fx.task(), service: FakeQuestViewService(history: history))
        await model.load()
        let started = Date()
        for seq in Self.count + 1...Self.count + 20 {
            model.receive(Fx.frame("task.event", Fx.eventJSON(seq, "text", Fx.text("live \(seq)")), id: seq))
        }
        let each = Date().timeIntervalSince(started) / 20
        XCTAssertEqual(model.transcript.lastSeq, Self.count + 20)
        XCTAssertLessThan(each, 0.05, "one live event took \(String(format: "%.3f", each)) s")
    }
}
