import Foundation
import MarcelKit
import XCTest

@testable import MarcelQuest

/// Reading task events, folding them into steps and rows, and keeping untrusted text short and inert.
@MainActor
final class QuestDetailTests: XCTestCase {
    static let contracts = URL(fileURLWithPath: #filePath)
        .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
        .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
        .appending(path: "contracts")

    /// Every `data` example in the contracts, as the event type it belongs to: transcript.schema.json's `$defs` and
    /// app-api.yaml's hub-written shapes (copied here from the YAML, which the tests cannot parse).
    func testEveryContractExampleReadsAsItsKind() throws {
        let schema = try JSONSerialization.jsonObject(
            with: Data(contentsOf: Self.contracts.appending(path: "transcript.schema.json"))) as! [String: Any]
        let defs = schema["$defs"] as! [String: Any]
        let transcript: [(String, String)] = [
            ("text", "Text"), ("tool_call", "ToolCall"), ("tool_result", "ToolResult"), ("diff", "Diff"),
            ("subagent_start", "SubagentStart"), ("subagent_stop", "SubagentStop"), ("error", "Error"), ("raw", "Raw"),
            ("workflow_start", "WorkflowStart"), ("workflow_stop", "WorkflowStop"),
            ("workflow_agent_start", "WorkflowAgentStart"), ("workflow_agent_stop", "WorkflowAgentStop"),
        ]
        var seq = 0
        for (type, def) in transcript {
            let example = ((defs[def] as! [String: Any])["examples"] as! [[String: Any]])[0]
            seq += 1
            let entry = QuestEvent(Fx.event(seq, type, example)).entry
            if case .unreadable = entry { XCTFail("\(type): the contract's example did not read") }
        }
        let hubWritten: [(String, [String: Any])] = [
            ("state", ["from": "working", "to": "needs_you", "reason": "Permission request: Bash"]),
            ("progress", ["text": "Found the race; writing the regression test now."]),
            ("artifact", ["artifact_id": "art_01J9ZT1", "kind": "pr", "title": "Fix flaky login test"]),
            ("note", ["author": "user", "text": "Also add a test for the empty case."]),
            ("permission", ["approval_id": "apr_01J9ZR2", "tool": "Bash", "summary": "Run `docker system df -v`"]),
        ]
        for (type, data) in hubWritten {
            seq += 1
            if case .unreadable = QuestEvent(Fx.event(seq, type, data)).entry {
                XCTFail("\(type): the contract's example did not read")
            }
        }
        XCTAssertEqual(QuestEvent(Fx.event(1, "diff", Fx.modelDiff)).entry,
                       .diff(toolUseId: "toolu_edit", filePath: "energy/model.py",
                             hunks: [DiffHunk(oldStart: 12, newStart: 12,
                                              lines: (Fx.modelDiff["hunks"] as! [[String: Any]])[0]["lines"]
                                                as! [String])]))
    }

    func testAnEventMissingItsFieldsIsOneLineNotACrash() {
        let event = QuestEvent(Fx.event(4, "tool_result", ["is_error": false]))
        XCTAssertEqual(event.entry, .unreadable(type: "tool_result"))
        let rows = QuestDetail.rows([event])
        XCTAssertEqual(rows.map(\.kind), [.line(.other, text: "An update the app couldn't read (tool_result)")])
    }

    func testStepsAreTheProgressReports() {
        let events = Fx.energyHistory.map(QuestEvent.init)
        let working = QuestStep.steps(events, state: .working)
        XCTAssertEqual(working.map(\.text), ["Read the last 12 months of bills", "Fetched offers from 4 suppliers",
                                             "Modelling with and without solar"])
        XCTAssertEqual(working.map(\.mark), [.done, .done, .current])
        XCTAssertEqual(QuestStep.steps(events, state: .done).map(\.mark), [.done, .done, .done])
        XCTAssertEqual(QuestStep.steps(events, state: .stopped).map(\.mark), [.done, .done, .stopped])
        XCTAssertEqual(QuestStep.steps([], state: .queued).map(\.mark), [.next])
        XCTAssertEqual(QuestStep.steps([], state: .working).map(\.text), ["Working on it, no steps reported yet"])
        XCTAssertEqual(QuestStep.steps([], state: .done), [])
    }

    /// S-12.11: the latest report's `next` steps follow as "· next" while the quest runs; each report replaces the
    /// list, and a report without `next` shows none.
    func testTheLatestReportsNextStepsFollowAsNext() {
        var history = Fx.energyHistory
        history[3] = Fx.event(4, "progress", ["text": "Fetched offers from 4 suppliers",
                                               "next": ["Old plan that was replaced"]])
        history[7] = Fx.event(8, "progress", ["text": "Modelling with and without solar",
                                               "next": ["Compare the yearly totals", "Write up the advice"]])
        let events = history.map(QuestEvent.init)
        let working = QuestStep.steps(events, state: .needsYou)
        XCTAssertEqual(working.map(\.text), ["Read the last 12 months of bills", "Fetched offers from 4 suppliers",
                                             "Modelling with and without solar", "Compare the yearly totals",
                                             "Write up the advice"])
        XCTAssertEqual(working.map(\.mark), [.done, .done, .current, .next, .next])
        XCTAssertEqual(Set(working.map(\.id)).count, working.count, "every line has its own id")
        XCTAssertEqual(QuestStep.steps(events, state: .done).map(\.mark), [.done, .done, .done], "done: no plan left")
        XCTAssertEqual(QuestStep.steps(events, state: .stopped).map(\.mark), [.done, .done, .stopped])

        // A newer report without `next` replaces the plan with none.
        let later = events + [QuestEvent(Fx.event(14, "progress", ["text": "Comparing totals"]))]
        XCTAssertEqual(QuestStep.steps(later, state: .working).map(\.mark), [.done, .done, .done, .current])
    }

    func testRowsFoldAToolsAnswerIntoItsCallAndKeepStepsOut() {
        let rows = QuestDetail.rows(Fx.energyHistory.map(QuestEvent.init), openIds: ["apr_engie"])
        XCTAssertEqual(rows.map(\.id), ["e1", "e2", "e5", "e6", "e9", "e10", "e12", "e13"],
                       "progress reports are steps, and a tool's answer joins its call")
        guard case .tool("Bash", "python model.py --solar 4.2kWp", let result?) = rows[3].kind else {
            return XCTFail("the Bash call carries its answer: \(rows[3].kind)")
        }
        XCTAssertEqual(result.text, "yearly saving: €212 → €486")
        XCTAssertEqual(rows.last?.kind, .approval(approvalId: "apr_engie", tool: "Bash",
                                                  summary: "Fetch Engie's price list", status: .open))
        XCTAssertTrue(rows[3].folds)
        XCTAssertFalse(rows[2].folds)
    }

    func testAnApprovalsStatusComesFromWhatTheAppHeard() {
        let permission = [QuestEvent(Fx.event(1, "permission", Fx.permission("apr_1")))]
        func status(_ approvals: [String: Approval] = [:], answering: [String: Bool] = [:], open: Set<String>?,
                    finished: Bool = false) -> ApprovalStatus? {
            guard case .approval(_, _, _, let status) = QuestDetail.rows(
                permission, approvals: approvals, answering: answering, openIds: open, finished: finished)[0].kind
            else { return nil }
            return status
        }
        XCTAssertEqual(status(open: nil), .open, "before the task is read, the prompt can be answered")
        XCTAssertEqual(status(open: ["apr_1"]), .open)
        XCTAssertEqual(status(answering: ["apr_1": true], open: ["apr_1"]), .sending(approve: true))
        XCTAssertEqual(status(["apr_1": Fx.approval("apr_1", state: "approved", via: "terminal")], open: ["apr_1"]),
                       .approved(elsewhere: true))
        XCTAssertEqual(status(["apr_1": Fx.approval("apr_1", state: "denied", via: "app")], open: []),
                       .denied(elsewhere: false))
        XCTAssertEqual(status(open: [], finished: true), .expired)
        XCTAssertEqual(status(open: []), .closed)
    }

    func testEveryOtherKindIsOnePlainLine() {
        let events = [
            Fx.event(1, "subagent_start", ["tool_use_id": "t", "description": "Find buggy function"]),
            Fx.event(2, "subagent_stop", ["agent_id": "a", "status": "completed", "summary": "It is sub()."]),
            Fx.event(3, "error", ["message": "You've hit your session limit · resets 2:29pm", "usage_limit": true]),
            Fx.event(4, "raw", ["entry_type": "unknown-future-type"]),
            Fx.event(5, "state", ["from": "working", "to": "needs_you", "reason": "Permission request: Bash"]),
            Fx.event(6, "artifact", ["artifact_id": "art_1", "kind": "doc", "title": "Energy offers compared"]),
        ].map(QuestEvent.init)
        XCTAssertEqual(QuestDetail.rows(events).map(\.kind), [
            .line(.helper, text: "Helper started: Find buggy function"),
            .line(.helper, text: "Helper finished: It is sub()."),
            .line(.error, text: "Out of Claude usage for now. You've hit your session limit · resets 2:29pm"),
            .line(.other, text: "A step the app can't show (unknown-future-type)"),
            .line(.state, text: "Now: Needs you · Permission request: Bash"),
            .line(.artifact, text: "Made a document: Energy offers compared"),
        ])
    }

    // MARK: Workflows (S-12.15, app API 0.7.3)

    /// Ten agents started and seven stopped, matched by `agent_id`: one row, "7 of 10 done".
    func testTenAgentStartsAndSevenStopsFoldIntoOneRow() throws {
        var events: [TaskEvent] = [
            Fx.event(1, "workflow_start", ["tool_use_id": "t", "run_id": "wf_1", "name": "summarize-notes",
                                           "description": "Summarise each note"]),
        ]
        for n in 0..<10 {
            events.append(Fx.event(2 + n, "workflow_agent_start", ["run_id": "wf_1", "agent_id": "a\(n)",
                                                                   "label": "summarize:note\(n).txt"]))
        }
        for n in [3, 0, 9, 1, 5, 7, 2] {  // out of order: matched by agent_id, not by position
            events.append(Fx.event(20 + n, "workflow_agent_stop", ["run_id": "wf_1", "agent_id": "a\(n)",
                                                                   "summary": "Note \(n) summarised."]))
        }
        let rows = QuestDetail.rows(events.map(QuestEvent.init))
        XCTAssertEqual(rows.count, 1, "the workflow and its agents are one row")
        guard case .workflow(let flow) = try XCTUnwrap(rows.first).kind else { return XCTFail("a workflow row") }
        XCTAssertEqual(flow.progress, "7 of 10 done")
        XCTAssertEqual(flow.title, "Summarise each note")
        XCTAssertEqual(flow.status, .running)
        XCTAssertEqual(flow.agents.first { $0.id == "a9" }?.summary, "Note 9 summarised.")
        XCTAssertEqual(flow.agents.first { $0.id == "a4" }?.done, false, "a4 has no stop yet")
        XCTAssertTrue(rows[0].folds, "folded until opened")
    }

    func testAFailedWorkflowStopShowsTheFailedMark() throws {
        let events = [
            Fx.event(1, "workflow_start", ["tool_use_id": "t", "run_id": "wf_1", "name": "summarize-notes"]),
            Fx.event(2, "workflow_agent_start", ["run_id": "wf_1", "agent_id": "a1", "label": "summarize:note8.txt",
                                                 "phase": "Summarize"]),
            Fx.event(3, "workflow_stop", ["tool_use_id": "t", "status": "failed", "summary": "One agent crashed."]),
        ].map(QuestEvent.init)
        guard case .workflow(let flow) = try XCTUnwrap(QuestDetail.rows(events).first).kind else {
            return XCTFail("a workflow row")
        }
        XCTAssertEqual(flow.status, .failed)
        XCTAssertEqual(flow.title, "summarize-notes", "no description: its name")
        XCTAssertEqual(flow.summary, "One agent crashed.")
        XCTAssertEqual(WorkflowRow(flow: flow, expanded: false, toggle: {}).mark?.text, "Failed")
        for (status, mark) in [("completed", "Finished"), ("stopped", "Stopped"), ("something", "Ended")] {
            let ended = QuestDetail.rows([
                Fx.event(1, "workflow_start", ["tool_use_id": "t", "run_id": "wf_1", "name": "n"]),
                Fx.event(2, "workflow_stop", ["tool_use_id": "t", "status": status]),
            ].map(QuestEvent.init))
            guard case .workflow(let flow) = ended[0].kind else { return XCTFail(status) }
            XCTAssertEqual(WorkflowRow(flow: flow, expanded: false, toggle: {}).mark?.text, mark, status)
        }
    }

    func testAgentsAreGroupedByPhaseInTheOrderTheyStarted() throws {
        let events = [
            Fx.event(1, "workflow_start", ["tool_use_id": "t", "run_id": "wf_1", "name": "review"]),
            Fx.event(2, "workflow_agent_start", ["run_id": "wf_1", "agent_id": "a1", "label": "read", "phase": "Read"]),
            Fx.event(3, "workflow_agent_start", ["run_id": "wf_1", "agent_id": "a2", "label": "check", "phase": "Check"]),
            Fx.event(4, "workflow_agent_start", ["run_id": "wf_1", "agent_id": "a3", "label": "read 2", "phase": "Read"]),
            Fx.event(5, "workflow_agent_start", ["run_id": "wf_1", "agent_id": "a4", "label": "loose"]),
        ].map(QuestEvent.init)
        guard case .workflow(let flow) = try XCTUnwrap(QuestDetail.rows(events).first).kind else {
            return XCTFail("a workflow row")
        }
        XCTAssertEqual(flow.groups.map(\.phase), ["Read", "Check", nil])
        XCTAssertEqual(flow.groups.map { $0.agents.map(\.label) }, [["read", "read 2"], ["check"], ["loose"]])
    }

    /// History the hub no longer keeps: an agent or a stop whose start is gone still lands in one workflow row.
    func testEventsWhoseStartIsGoneStillFoldIntoOneRow() throws {
        let events = [
            Fx.event(1, "workflow_agent_stop", ["run_id": "wf_9", "agent_id": "b1", "summary": "Done."]),
            Fx.event(2, "workflow_agent_start", ["run_id": "wf_9", "agent_id": "b2", "label": "second"]),
            Fx.event(3, "workflow_stop", ["tool_use_id": "t9", "run_id": "wf_9", "status": "completed"]),
        ].map(QuestEvent.init)
        let rows = QuestDetail.rows(events)
        XCTAssertEqual(rows.count, 1)
        guard case .workflow(let flow) = rows[0].kind else { return XCTFail("a workflow row") }
        XCTAssertEqual(flow.title, "A workflow")
        XCTAssertEqual(flow.progress, "1 of 2 done")
        XCTAssertEqual(flow.status, .completed)
    }

    /// Review fix: a stop that names no run, for a workflow known only from its agents, joins that row.
    func testAStopWithoutARunJoinsTheWorkflowItsAgentsAreIn() throws {
        let events = [
            Fx.event(1, "workflow_agent_start", ["run_id": "wf_9", "agent_id": "b1", "label": "first"]),
            Fx.event(2, "workflow_agent_stop", ["run_id": "wf_9", "agent_id": "b1", "summary": "Done."]),
            Fx.event(3, "workflow_stop", ["tool_use_id": "t9", "status": "completed"]),
        ].map(QuestEvent.init)
        let rows = QuestDetail.rows(events)
        XCTAssertEqual(rows.count, 1, "one workflow, one row")
        guard case .workflow(let flow) = rows[0].kind else { return XCTFail("a workflow row") }
        XCTAssertEqual(flow.progress, "1 of 1 done")
        XCTAssertEqual(flow.status, .completed)
    }

    func testTwoWorkflowsAtOnceKeepTheirOwnAgents() throws {
        let events = [
            Fx.event(1, "workflow_start", ["tool_use_id": "t1", "run_id": "wf_1", "name": "one"]),
            Fx.event(2, "workflow_start", ["tool_use_id": "t2", "run_id": "wf_2", "name": "two"]),
            Fx.event(3, "workflow_agent_start", ["run_id": "wf_1", "agent_id": "a1", "label": "1a"]),
            Fx.event(4, "workflow_agent_start", ["run_id": "wf_2", "agent_id": "a1", "label": "2a"]),
            Fx.event(5, "workflow_agent_stop", ["run_id": "wf_2", "agent_id": "a1", "summary": "Two's."]),
            Fx.event(6, "workflow_agent_start", ["run_id": "wf_1", "agent_id": "a2", "label": "1b"]),
            Fx.event(7, "workflow_stop", ["tool_use_id": "t2", "status": "completed"]),
        ].map(QuestEvent.init)
        let flows = QuestDetail.rows(events).compactMap { row -> DetailRow.Workflow? in
            if case .workflow(let flow) = row.kind { flow } else { nil }
        }
        XCTAssertEqual(flows.map(\.title), ["one", "two"])
        XCTAssertEqual(flows.map(\.progress), ["0 of 2 done", "1 of 1 done"], "the same agent id in two runs is two agents")
        XCTAssertEqual(flows.map(\.status), [.running, .completed])
        XCTAssertEqual(flows[1].agents.first?.summary, "Two's.")
    }

    /// Review fix: the row's id comes from its run, so loading an older page (with the start) keeps it open.
    func testTheRowIdStaysWhenOlderHistoryLoads() {
        let recent = [
            Fx.event(40, "workflow_agent_start", ["run_id": "wf_1", "agent_id": "a1", "label": "x"]),
        ]
        let all = [Fx.event(3, "workflow_start", ["tool_use_id": "t", "run_id": "wf_1", "name": "n",
                                                  "description": "Real title"])] + recent
        let before = QuestDetail.rows(recent.map(QuestEvent.init)).map(\.id)
        let after = QuestDetail.rows(all.map(QuestEvent.init)).map(\.id)
        XCTAssertEqual(before, ["wf-wf_1"])
        XCTAssertEqual(after, ["wf-wf_1"])
    }

    func testAnEmptyDescriptionFallsBackToTheName() throws {
        let rows = QuestDetail.rows([Fx.event(1, "workflow_start", ["tool_use_id": "t", "run_id": "wf_1",
                                                                     "name": "summarize-notes", "description": "  "])]
            .map(QuestEvent.init))
        guard case .workflow(let flow) = try XCTUnwrap(rows.first).kind else { return XCTFail("a workflow row") }
        XCTAssertEqual(flow.title, "summarize-notes")
    }

    func testAWorkflowEventWithoutItsIdsIsUnreadableNotACrash() {
        for type in ["workflow_start", "workflow_stop", "workflow_agent_start", "workflow_agent_stop"] {
            let entry = QuestEvent(Fx.event(1, type, ["label": "x"])).entry
            XCTAssertEqual(entry, .unreadable(type: type), type)
        }
    }

    /// Untrusted text: an agent's summary (up to 2 KB from the runner) is capped to a few lines.
    func testALongAgentSummaryIsCapped() {
        let long = (1...40).map { "line \($0)" }.joined(separator: "\n")
        let capped = CappedText(long, maxLines: WorkflowRow.summaryLines)
        XCTAssertEqual(capped.text.split(separator: "\n").count, WorkflowRow.summaryLines)
        XCTAssertEqual(capped.moreLine, "…and 37 more lines")
    }

    // Untrusted text: very long output is cut with "…and N more lines", very long lines with "…".
    func testLongTextIsCappedWithTheNumberOfLinesLeft() {
        let output = (1...100).map { "line \($0)" }.joined(separator: "\n") + "\n"
        let capped = CappedText(output, maxLines: 30)
        XCTAssertEqual(capped.text.split(separator: "\n").count, 30)
        XCTAssertEqual(capped.moreLine, "…and 70 more lines")
        XCTAssertNil(CappedText("one\ntwo\n", maxLines: 2).moreLine)
        let wide = CappedText(String(repeating: "x", count: 5000), maxLines: 3)
        XCTAssertEqual(wide.text.count, CappedText.maxLineLength + 1)
        XCTAssertTrue(wide.text.hasSuffix("…"))
    }

    func testAHugeDiffIsCapped() {
        let lines = (1...500).map { "+line \($0)" }
        let diff = DiffLines([DiffHunk(oldStart: 1, newStart: 1, lines: lines)])
        XCTAssertEqual(diff.lines.count, DiffLines.maxLines)
        XCTAssertEqual(diff.moreLine, "…and 441 more lines")
        XCTAssertEqual(diff.added, 500)
        XCTAssertEqual(diff.counts, "+500 −0")
        XCTAssertEqual(diff.spokenCounts, "500 lines added, 0 lines removed")
    }

    func testDiffLinesAreColouredByTokenWithoutChangingTheText() {
        let python = SyntaxColours.language(for: "energy/model.py")
        let line = "    return offer.price * 3500 # \"yearly\""
        let runs = SyntaxColours.runs(line, language: python)
        XCTAssertEqual(runs.map(\.0).joined(), line, "colouring never changes the text")
        XCTAssertTrue(runs.contains { $0 == ("return", .keyword) })
        XCTAssertTrue(runs.contains { $0 == ("3500", .number) })
        XCTAssertTrue(runs.contains { $0 == ("# \"yearly\"", .comment) })
        let swift = SyntaxColours.runs(#"let url = "https://x.invalid" // a link, not a button"#,
                                       language: SyntaxColours.language(for: "A.swift"))
        XCTAssertTrue(swift.contains { $0 == (#""https://x.invalid""#, .string) })
        XCTAssertEqual(SyntaxColours.runs("plain", language: SyntaxColours.language(for: "notes.txt")).count, 1)
    }

    /// The text is drawn as written: Markdown and links in a transcript are never turned into anything.
    func testTranscriptTextIsNeverInterpreted() {
        let hostile = "[tap me](https://evil.invalid) **bold** <script>alert(1)</script>"
        let rows = QuestDetail.rows([QuestEvent(Fx.event(1, "text", Fx.text(hostile)))])
        XCTAssertEqual(rows[0].kind, .text(fromUser: false, text: hostile))
        let coloured = DiffLineView.coloured(.init(kind: .added, text: hostile), language: nil, theme: .light)
        XCTAssertEqual(String(coloured.characters), hostile)
        XCTAssertTrue(coloured.runs.allSatisfy { $0.link == nil }, "no link survives into the drawn text")
    }

    // "Open in Claude": cloud_url for a cloud quest, remote_url for a NUC one; https only.
    func testOpenInClaudeIsOnlyAnHTTPSAddress() {
        XCTAssertEqual(QuestViewModel.claudeURL(for: Fx.task(location: "cloud",
                                                              extra: ["cloud_url": "https://claude.ai/code/s_1"])),
                       URL(string: "https://claude.ai/code/s_1"))
        XCTAssertEqual(QuestViewModel.claudeURL(for: Fx.task(extra: ["remote_url": "https://claude.ai/code/s_2"])),
                       URL(string: "https://claude.ai/code/s_2"))
        XCTAssertNil(QuestViewModel.claudeURL(for: Fx.task()), "a NUC quest without Remote Control has no button")
        XCTAssertNil(QuestViewModel.claudeURL(for: Fx.task(extra: ["cloud_url": "https://claude.ai/code/s_3"])),
                     "a NUC quest opens only its remote_url")
        XCTAssertNil(QuestViewModel.claudeURL(for: Fx.task(location: "cloud",
                                                           extra: ["remote_url": "https://claude.ai/code/s_4"])),
                     "a cloud quest opens only its cloud_url")
        XCTAssertEqual(QuestViewModel.claudeURL(for: Fx.task(location: "cloud", extra: [
            "cloud_url": "https://claude.ai/code/s_5", "remote_url": "https://claude.ai/code/s_6",
        ])), URL(string: "https://claude.ai/code/s_5"))
        for bad in ["http://claude.ai/code/s", "javascript:alert(1)", "file:///etc/passwd", "https://", "claude.ai/x",
                    "https://user:pw@claude.ai/code/s",
                    "https://evil.invalid/code/s", "https://claude.ai.evil.invalid/code/s", "https://claude.ai/login",
                    "https://claude.ai:8443/code/s", "https://claude.ai/codex"] {
            XCTAssertNil(QuestViewModel.claudeURL(for: Fx.task(extra: ["remote_url": bad])), bad)
        }
    }
}
