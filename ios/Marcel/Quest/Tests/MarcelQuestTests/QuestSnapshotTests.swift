import Foundation
import MarcelKit
import SwiftUI
import UIKit
import XCTest

@testable import MarcelQuest

/// The rows of one event kind, drawn as the live detail draws them.
struct RowsStill: View {
    var events: [TaskEvent]
    var expanded: Set<String> = []
    var approvals: [String: Approval] = [:]
    var openIds: Set<String>? = nil
    var pending: [PendingNote] = []

    var body: some View {
        let rows = QuestDetail.rows(events.map(QuestEvent.init), approvals: approvals, openIds: openIds)
        VStack(alignment: .leading, spacing: QuestMetrics.rowGap) {
            ForEach(rows) { DetailRowView(row: $0, expanded: expanded.contains($0.id), actions: DetailActions()) }
            ForEach(pending) { PendingNoteView(note: $0) }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// The quest view (wireframe 2A) and every event kind it shows, compared pixel for pixel with `__Snapshots__/*.png` in
/// light and dark (and the whole screen at the largest text size). `ImageRenderer` draws nothing inside a
/// `ScrollView` or a text field, so these render `QuestStill` and `RowsStill`: the same rows in a plain stack.
@MainActor
final class QuestSnapshotTests: XCTestCase {
    static let both: [Snapshot.Variant] = [.light, .dark]

    /// Wireframe 2A: three steps in, a command and a diff folded, your message, an approval waiting.
    static func wireframe() async -> QuestViewModel {
        let hub = FakeQuestViewService(history: Fx.energyHistory)
        let model = QuestViewModel(taskId: Fx.taskId, service: hub)
        await model.load()
        model.expanded = ["e6"]
        return model
    }

    func testTheQuestViewLooksLikeWireframe2A() async {
        Snapshot.assert(QuestStill(model: await Self.wireframe(), title: "Energy contracts", project: "Home energy"),
                        named: "Quest", padding: 0)
    }

    func testAFinishedQuestWithAProblemAndAFoldedDetail() async {
        let hub = FakeQuestViewService(task: Fx.taskJSON(state: "done", location: "cloud",
                                                         extra: ["cloud_url": "https://claude.ai/code/s_1"]),
                                       history: Fx.energyHistory)
        let model = QuestViewModel(taskId: Fx.taskId, service: hub)
        await model.load()
        model.detailOpen = false
        model.problem = "Couldn't reach the hub. Check the connection and try again."
        Snapshot.assert(QuestStill(model: model, title: "Energy contracts", project: "Home energy"),
                        named: "Quest-done", variants: [.light], padding: 0)
    }

    // One snapshot per event kind, light and dark.

    func testText() {
        Snapshot.assert(RowsStill(events: [
            Fx.event(1, "text", Fx.text("Opening the bills from the last year. Then I'll fetch four offers.")),
            Fx.event(2, "text", Fx.text("Continue from where the last session stopped.", role: "user")),
            Fx.event(3, "text", Fx.text((1...90).map { "Row \($0) [link](https://x.invalid)" }
                .joined(separator: "\n"))),
        ]), named: "Event-text", variants: Self.both)
    }

    func testToolCallFoldedAndRunning() {
        Snapshot.assert(RowsStill(events: [
            Fx.event(1, "tool_call", Fx.toolCall("t1", "Bash", "python model.py --solar 4.2kWp")),
            Fx.event(2, "tool_call", Fx.toolCall("t2", "Read", "energy/bills/2025.csv")),
        ], expanded: ["e2"]), named: "Event-tool_call", variants: Self.both)
    }

    /// S-12.15: a workflow opened (agents by phase, ✓ with what each gave back, ▶ while running) and one that failed,
    /// folded.
    func testWorkflowOpenAndFailed() {
        var events: [TaskEvent] = [
            Fx.event(1, "workflow_start", ["tool_use_id": "t1", "run_id": "wf_1", "name": "compare-offers",
                                           "description": "Compare the three heat-pump offers"]),
        ]
        let agents: [(String, String, String, String?)] = [
            ("a1", "read:daikin.pdf", "Read", "Daikin: €9.8k, 7-year warranty."),
            ("a2", "read:vaillant.pdf", "Read", "Vaillant: €8.9k, installs in May."),
            ("a3", "read:bosch.pdf", "Read", nil),
            ("a4", "compare", "Compare", nil),
        ]
        for (n, agent) in agents.enumerated() {
            var data: [String: Any] = ["run_id": "wf_1", "agent_id": agent.0, "label": agent.1, "phase": agent.2]
            events.append(Fx.event(2 + n, "workflow_agent_start", data))
            if let summary = agent.3 {
                data = ["run_id": "wf_1", "agent_id": agent.0, "summary": summary]
                events.append(Fx.event(10 + n, "workflow_agent_stop", data))
            }
        }
        events += [
            Fx.event(20, "workflow_start", ["tool_use_id": "t2", "run_id": "wf_2", "name": "tidy-notes"]),
            Fx.event(21, "workflow_agent_start", ["run_id": "wf_2", "agent_id": "b1", "label": "tidy"]),
            Fx.event(22, "workflow_stop", ["tool_use_id": "t2", "status": "failed"]),
        ]
        Snapshot.assert(RowsStill(events: events, expanded: ["wf-wf_1"]), named: "Event-workflow", variants: Self.both)
    }

    func testToolResultOpenFailedAndDenied() {
        Snapshot.assert(RowsStill(events: [
            Fx.event(1, "tool_call", Fx.toolCall("t1", "Bash", "python model.py --solar 4.2kWp")),
            Fx.event(2, "tool_result", Fx.toolResult("t1", "yearly saving: €212 → €486\nwith solar: €486")),
            Fx.event(3, "tool_call", Fx.toolCall("t2", "Bash", "pytest -q")),
            Fx.event(4, "tool_result", Fx.toolResult("t2", (1...45).map { "FAILED test_\($0)" }
                .joined(separator: "\n"), error: true)),
            Fx.event(5, "tool_call", Fx.toolCall("t3", "Bash", "rm -rf build")),
            Fx.event(6, "tool_result", Fx.toolResult("t3", "The user doesn't want to proceed.", error: true,
                                                     denied: true)),
        ], expanded: ["e1", "e3"]), named: "Event-tool_result", variants: Self.both)
    }

    func testDiffOpenAndFolded() {
        var big = Fx.modelDiff
        big["file_path"] = "web/app.ts"
        big["hunks"] = [["old_start": 1, "old_lines": 0, "new_start": 1, "new_lines": 70,
                         "lines": (1...70).map { "+const step\($0) = \"done\"; // \($0)" }]]
        Snapshot.assert(RowsStill(events: [Fx.event(1, "diff", Fx.modelDiff), Fx.event(2, "diff", big)],
                                  expanded: ["e1", "e2"]),
                        named: "Event-diff", variants: Self.both)
    }

    func testPermissionOpenAndAnswered() {
        Snapshot.assert(RowsStill(events: [
            Fx.event(1, "permission", Fx.permission("apr_1")),
            Fx.event(2, "permission", Fx.permission("apr_2")),
            Fx.event(3, "permission", Fx.permission("apr_3")),
        ], approvals: ["apr_2": Fx.approval("apr_2", state: "approved", via: "terminal")], openIds: ["apr_1"]),
                        named: "Event-permission", variants: Self.both)
    }

    func testNotes() {
        Snapshot.assert(RowsStill(events: [
            Fx.event(1, "note", Fx.note("Compare gas and electricity offers.", author: "marcel", prompt: true)),
            Fx.event(2, "note", Fx.note("Use 4 kWp, the roof is small")),
            Fx.event(3, "note", Fx.note("Looks stuck on the solar model.", handback: true)),
        ], pending: [PendingNote(id: "c1", text: "Also check Luminus", sent: false)]),
                        named: "Event-note", variants: Self.both)
    }

    func testProgressAsSteps() {
        let events = Fx.energyHistory.map(QuestEvent.init)
        Snapshot.assert(VStack(alignment: .leading, spacing: 24) {
            StepsList(steps: QuestStep.steps(events, state: .working))
            StepsList(steps: QuestStep.steps(events, state: .failed))
            StepsList(steps: QuestStep.steps([], state: .queued))
        }, named: "Event-progress", variants: Self.both)
    }

    /// S-12.11: the latest report's planned steps as "· next" (wireframe 2A).
    func testProgressWithNextSteps() {
        var history = Fx.energyHistory
        history[7] = Fx.event(8, "progress", ["text": "Modelling with and without solar",
                                               "next": ["Compare the yearly totals", "Write up the advice"]])
        Snapshot.assert(StepsList(steps: QuestStep.steps(history.map(QuestEvent.init), state: .working)),
                        named: "Event-progress-next", variants: Self.both)
    }

    func testOneLineKinds() {
        Snapshot.assert(RowsStill(events: [
            Fx.event(1, "state", ["from": "working", "to": "needs_you", "reason": "Permission request: Bash"]),
            Fx.event(2, "artifact", ["artifact_id": "art_1", "kind": "doc", "title": "Energy offers compared"]),
            Fx.event(3, "subagent_start", ["tool_use_id": "t", "description": "Find the cheapest supplier"]),
            Fx.event(4, "subagent_stop", ["agent_id": "a", "status": "completed", "summary": "Luminus, by €40."]),
            Fx.event(5, "error", ["message": "You've hit your session limit · resets 2:29pm", "usage_limit": true]),
            Fx.event(6, "raw", ["entry_type": "unknown-future-type"]),
        ]), named: "Event-lines", variants: Self.both)
    }
}

/// What VoiceOver reads in the quest view, from the accessibility tree SwiftUI builds (as PixelKit's audit does,
/// since XCUITest's audit cannot run on this Mac).
@MainActor
final class QuestAccessibilityTests: XCTestCase {
    override class func setUp() {
        super.setUp()
        let handle = dlopen("/usr/lib/libAccessibility.dylib", RTLD_NOW)
        typealias Enable = @convention(c) (Bool) -> Void
        if let symbol = dlsym(handle, "_AXSApplicationAccessibilitySetEnabled") {
            unsafeBitCast(symbol, to: Enable.self)(true)
        }
    }

    private var windows: [UIWindow] = []

    override func tearDown() {
        windows.forEach { $0.isHidden = true }
        windows = []
        super.tearDown()
    }

    private func elements<V: View>(of view: V, dynamicType: DynamicTypeSize = .large)
        -> [(label: String, traits: UIAccessibilityTraits, frame: CGRect)]
    {
        let host = UIHostingController(rootView: view.environment(\.dynamicTypeSize, dynamicType))
        let window = UIWindow(frame: CGRect(x: 0, y: 0, width: 390, height: 6000))
        window.rootViewController = host
        window.makeKeyAndVisible()
        windows.append(window)
        host.view.layoutIfNeeded()
        RunLoop.main.run(until: Date().addingTimeInterval(0.3))
        var found: [(String, UIAccessibilityTraits, CGRect)] = []
        func walk(_ object: NSObject, depth: Int) {
            guard depth < 20 else { return }
            if object.isAccessibilityElement {
                found.append((object.accessibilityLabel ?? "", object.accessibilityTraits, object.accessibilityFrame))
            }
            if let children = object.accessibilityElements as? [NSObject], !children.isEmpty {
                children.forEach { walk($0, depth: depth + 1) }
            } else if object.accessibilityElementCount() != NSNotFound, object.accessibilityElementCount() > 0 {
                for index in 0..<object.accessibilityElementCount() {
                    if let child = object.accessibilityElement(at: index) as? NSObject { walk(child, depth: depth + 1) }
                }
            } else if let view = object as? UIView {
                view.subviews.forEach { walk($0, depth: depth + 1) }
            }
        }
        walk(host.view, depth: 0)
        return found
    }

    func testTheQuestReadsInPlainWordsAtEveryTextSize() async {
        let model = await QuestSnapshotTests.wireframe()
        for size in [DynamicTypeSize.large, .accessibility5] {
            let found = elements(of: QuestStill(model: model, title: "Energy contracts", project: "Home energy")
                .environment(\.questStill, true), dynamicType: size)
            let labels = found.map(\.label)
            for expected in [
                "Energy contracts", "Home energy, NUC, sonnet", "Done: Read the last 12 months of bills",
                "Doing now: Modelling with and without solar", "Live detail",
                "Command: python model.py --solar 4.2kWp, done", "Change to energy/model.py: 2 lines added, 1 line removed",
                "Claude: Modelling the yearly cost with and without the solar panels.",
                "Approve", "Not now", "Stop this quest", "Hand back to Marcel", "Send to this quest",
            ] {
                XCTAssertTrue(labels.contains(expected), "\(size): no element reads \"\(expected)\" in \(labels)")
            }
            for label in labels {
                XCTAssertFalse(label.isEmpty, "\(size): an element has no label")
                XCTAssertNil(label.first { "▶▼◀▸▾✓×".contains($0) }, "\(size): \"\(label)\" has a symbol")
                let shouted = label.split { !$0.isLetter }
                    .first { $0.count >= 3 && $0 == $0.uppercased() && $0 != $0.lowercased() && $0 != "NUC" }
                XCTAssertNil(shouted, "\(size): \"\(label)\" is in capitals")
            }
            let headers = found.filter { $0.traits.contains(.header) }.map(\.label)
            XCTAssertTrue(headers.contains("Home energy, NUC, sonnet") && headers.contains("Live detail"), "\(size): titles are headers")
            for button in found where button.traits.contains(.button) {
                XCTAssertGreaterThanOrEqual(button.frame.height, 44, "\(size): \"\(button.label)\" is too small to tap")
            }
        }
    }

    /// S-12.15: an open workflow reads in plain words: the row as one button, each agent, the phases as headers.
    func testAWorkflowReadsInPlainWords() {
        let events = [
            Fx.event(1, "workflow_start", ["tool_use_id": "t", "run_id": "wf_1", "name": "compare-offers",
                                           "description": "Compare the offers"]),
            Fx.event(2, "workflow_agent_start", ["run_id": "wf_1", "agent_id": "a1", "label": "read:daikin.pdf",
                                                 "phase": "Read"]),
            Fx.event(3, "workflow_agent_stop", ["run_id": "wf_1", "agent_id": "a1", "summary": "Daikin is cheapest."]),
            Fx.event(4, "workflow_agent_start", ["run_id": "wf_1", "agent_id": "a2", "label": "compare", "phase": "Compare"]),
            Fx.event(5, "workflow_stop", ["tool_use_id": "t", "status": "failed"]),
        ]
        let found = elements(of: RowsStill(events: events, expanded: ["wf-wf_1"]).padding(16))
        let labels = found.map(\.label)
        for expected in ["Workflow: Compare the offers, 1 of 2 done, failed", "Done: read:daikin.pdf. Daikin is cheapest.",
                         "Working: compare", "Read", "Compare"] {
            XCTAssertTrue(labels.contains(expected), "no element reads \"\(expected)\" in \(labels)")
        }
        for label in labels {
            XCTAssertNil(label.first { "▶▼◀▸▾✓×".contains($0) }, "\"\(label)\" has a symbol")
        }
        let headers = found.filter { $0.traits.contains(.header) }.map(\.label)
        XCTAssertTrue(headers.contains("Read") && headers.contains("Compare"), "phases are headers: \(headers)")
        let row = found.first { $0.label.hasPrefix("Workflow:") }
        XCTAssertTrue(row?.traits.contains(.button) == true)
        XCTAssertGreaterThanOrEqual(row?.frame.height ?? 0, 44)
    }

    func testVoiceOverReadsALongCommandCutLikeTheScreen() {
        let command = (1...200).map { "--flag-\($0)" }.joined(separator: " ")
        let found = elements(of: RowsStill(events: [Fx.event(1, "tool_call", Fx.toolCall("t1", "Bash", command))]))
        let row = found.first { $0.label.hasPrefix("Command: ") }
        XCTAssertNotNil(row)
        XCTAssertLessThanOrEqual(row?.label.count ?? .max, CappedText.maxLineLength + 40,
                                 "VoiceOver reads the command as cut on screen, not all \(command.count) characters")
    }
}
