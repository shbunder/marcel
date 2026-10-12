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
