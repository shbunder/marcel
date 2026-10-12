import Foundation
import MarcelKit

/// One line of the steps checklist (wireframe 2A): ✓ done, ▶ the one it is on, · not started, × where it stopped.
public struct QuestStep: Equatable, Sendable, Identifiable {
    public enum Mark: Equatable, Sendable {
        case done, current, next, stopped

        var symbol: String {
            switch self {
            case .done: "✓"
            case .current: "▶"
            case .next: "·"
            case .stopped: "×"
            }
        }

        var spoken: String {
            switch self {
            case .done: "Done"
            case .current: "Doing now"
            case .next: "Not started"
            case .stopped: "Stopped here"
            }
        }
    }

    public var id: Int
    public var text: String
    public var mark: Mark

    /// The steps are the worker's `report(progress)` events, oldest first. Every report but the last is done; the
    /// last one is what it is doing now, done once the quest is done, and where it stopped if it failed or was
    /// stopped. The contract carries no planned steps, so "· next" only shows before the first report.
    public static func steps(_ events: [QuestEvent], state: Components.Schemas.TaskState?) -> [QuestStep] {
        var steps: [QuestStep] = []
        for event in events {
            if case .progress(let text) = event.entry {
                steps.append(QuestStep(id: event.seq, text: text, mark: .done))
            }
        }
        guard let state else { return steps }
        if steps.isEmpty {
            switch state {
            case .queued: return [QuestStep(id: 0, text: "Waiting for its turn", mark: .next)]
            case .starting: return [QuestStep(id: 0, text: "Starting up", mark: .current)]
            case .working, .needsYou, .silent, .unknown:
                return [QuestStep(id: 0, text: "Working on it, no steps reported yet", mark: .current)]
            case .done, .failed, .stopped: return []
            }
        }
        switch state {
        case .done: break
        case .failed, .stopped: steps[steps.count - 1].mark = .stopped
        case .queued, .starting, .working, .needsYou, .silent, .unknown: steps[steps.count - 1].mark = .current
        }
        return steps
    }
}

/// Where an inline approval prompt stands.
public enum ApprovalStatus: Equatable, Sendable {
    case open
    /// Your answer is on its way.
    case sending(approve: Bool)
    case approved(elsewhere: Bool)
    case denied(elsewhere: Bool)
    /// It went away without an answer: the session stopped or moved past it.
    case expired
    /// Not open any more, and the app never heard the answer.
    case closed

    init(_ approval: Approval) {
        let elsewhere = approval.answeredVia == .terminal
        switch approval.state {
        case .open: self = .open
        case .approved: self = .approved(elsewhere: elsewhere)
        case .denied: self = .denied(elsewhere: elsewhere)
        case .expired: self = .expired
        }
    }

    var isOpen: Bool { self == .open }

    /// What the prompt says once it is not waiting for you.
    var words: String? {
        switch self {
        case .open: nil
        case .sending(let approve): approve ? "Approving…" : "Denying…"
        case .approved(let elsewhere): elsewhere ? "Approved in the terminal" : "Approved"
        case .denied(let elsewhere): elsewhere ? "Denied in the terminal" : "Denied"
        case .expired: "No longer waiting: the quest moved on"
        case .closed: "Answered"
        }
    }
}

/// One row of the live detail, folded from the events: text, a tool and what it gave back, a diff, an approval
/// prompt, a note, or a one-line event (state changes, items, helpers, errors).
public struct DetailRow: Identifiable, Equatable, Sendable {
    public enum Kind: Equatable, Sendable {
        /// Claude's words, or a prompt it was given (`user`).
        case text(fromUser: Bool, text: String)
        /// A tool call; `result` is nil until it comes back.
        case tool(tool: String, summary: String, result: ToolResult?)
        case diff(filePath: String, hunks: [DiffHunk])
        case approval(approvalId: String, tool: String, summary: String, status: ApprovalStatus)
        /// You → this quest (`user`), or Marcel → this quest.
        case note(fromUser: Bool, text: String, handback: Bool, prompt: Bool)
        case line(LineKind, text: String)
    }

    public enum LineKind: Equatable, Sendable {
        case state, artifact, helper, error, other
    }

    public struct ToolResult: Equatable, Sendable {
        public var text: String
        public var isError: Bool
        public var denied: Bool
        public var truncated: Bool
    }

    /// `e<seq>` of the event the row starts at: stable while events arrive, so a folded or opened row stays so.
    public var id: String
    public var at: Date
    public var kind: Kind

    /// True for rows that open and close (tools and diffs: the raw detail the wireframe folds away).
    public var folds: Bool {
        switch kind {
        case .tool, .diff: true
        default: false
        }
    }
}

public enum QuestDetail {
    /// Folds the events into rows in one pass: a tool's result joins its call, a permission takes its approval's
    /// state, progress reports stay in the steps. `approvals` holds what the app has heard about each approval;
    /// `openIds` are the task's open approvals (`Task.approval_ids`).
    public static func rows(_ events: [QuestEvent], approvals: [String: Approval] = [:],
                            answering: [String: Bool] = [:], openIds: Set<String>? = nil,
                            finished: Bool = false) -> [DetailRow] {
        var rows: [DetailRow] = []
        rows.reserveCapacity(events.count)
        var toolRow: [String: Int] = [:]
        for event in events {
            let id = "e\(event.seq)"
            switch event.entry {
            case .text(let role, let text):
                rows.append(DetailRow(id: id, at: event.at, kind: .text(fromUser: role == "user", text: text)))
            case .toolCall(let toolUseId, let tool, let summary):
                toolRow[toolUseId] = rows.count
                rows.append(DetailRow(id: id, at: event.at, kind: .tool(tool: tool, summary: summary, result: nil)))
            case .toolResult(let toolUseId, let isError, let denied, let text, let truncated):
                let result = DetailRow.ToolResult(text: text, isError: isError, denied: denied, truncated: truncated)
                if let index = toolRow[toolUseId], case .tool(let tool, let summary, nil) = rows[index].kind {
                    rows[index].kind = .tool(tool: tool, summary: summary, result: result)
                } else {
                    // Its call is older than the history the hub kept.
                    rows.append(DetailRow(id: id, at: event.at, kind: .tool(tool: "Tool", summary: "A tool's answer",
                                                                            result: result)))
                }
            case .diff(_, let filePath, let hunks):
                rows.append(DetailRow(id: id, at: event.at, kind: .diff(filePath: filePath, hunks: hunks)))
            case .permission(let approvalId, let tool, let summary):
                let status = status(of: approvalId, approvals: approvals, answering: answering, openIds: openIds,
                                    finished: finished)
                rows.append(DetailRow(id: id, at: event.at, kind: .approval(approvalId: approvalId, tool: tool,
                                                                            summary: summary, status: status)))
            case .note(let author, let text, let handback, let prompt):
                rows.append(DetailRow(id: id, at: event.at, kind: .note(fromUser: author == "user", text: text,
                                                                        handback: handback, prompt: prompt)))
            case .progress:
                continue  // shown as a step
            case .subagentStart(let description, _):
                rows.append(DetailRow(id: id, at: event.at, kind: .line(.helper, text: "Helper started: \(description)")))
            case .subagentStop(let status, let summary):
                let head = switch status {
                case "completed": "Helper finished"
                case "failed": "Helper failed"
                case "stopped": "Helper stopped"
                default: "Helper ended"
                }
                let text = summary.map { "\(head): \($0)" } ?? head
                rows.append(DetailRow(id: id, at: event.at, kind: .line(.helper, text: text)))
            case .error(let message, let usageLimit):
                let text = usageLimit ? "Out of Claude usage for now. \(message)" : "Something went wrong: \(message)"
                rows.append(DetailRow(id: id, at: event.at, kind: .line(.error, text: text)))
            case .raw(let entryType):
                rows.append(DetailRow(id: id, at: event.at,
                                      kind: .line(.other, text: "A step the app can't show (\(entryType))")))
            case .state(_, let to, let reason):
                rows.append(DetailRow(id: id, at: event.at, kind: .line(.state, text: stateLine(to: to, reason: reason))))
            case .artifact(_, let kind, let title):
                rows.append(DetailRow(id: id, at: event.at,
                                      kind: .line(.artifact, text: "Made \(kindWords(kind)): \(title)")))
            case .unreadable(let type):
                rows.append(DetailRow(id: id, at: event.at,
                                      kind: .line(.other, text: "An update the app couldn't read (\(type))")))
            }
        }
        return rows
    }

    static func status(of approvalId: String, approvals: [String: Approval], answering: [String: Bool],
                       openIds: Set<String>?, finished: Bool) -> ApprovalStatus {
        if let approve = answering[approvalId] { return .sending(approve: approve) }
        if let approval = approvals[approvalId] { return ApprovalStatus(approval) }
        guard let openIds else { return .open }  // the task is not loaded yet
        if openIds.contains(approvalId) { return .open }
        return finished ? .expired : .closed
    }

    /// "Now: Needs you · Permission request: Bash".
    static func stateLine(to: String, reason: String) -> String {
        let words = Components.Schemas.TaskState(rawValue: to).map(stateWords) ?? to
        return reason.isEmpty ? "Now: \(words)" : "Now: \(words) · \(reason)"
    }

    static func stateWords(_ state: Components.Schemas.TaskState) -> String {
        switch state {
        case .queued: "Queued"
        case .starting: "Starting"
        case .working: "Working"
        case .needsYou: "Needs you"
        case .done: "Done"
        case .failed: "Failed"
        case .stopped: "Stopped"
        case .silent: "Gone quiet"
        case .unknown: "State unknown"
        }
    }

    static func kindWords(_ kind: String) -> String {
        switch kind {
        case "pr": "a pull request"
        case "branch": "a branch"
        case "doc": "a document"
        case "dashboard": "a dashboard"
        case "board": "a board"
        case "link": "a link"
        default: "a file"
        }
    }
}

/// Untrusted text cut to what a phone can show: at most `maxLines` lines of at most `maxLineLength` characters,
/// with "…and N more lines" for the rest.
public struct CappedText: Equatable, Sendable {
    public var text: String
    public var hiddenLines: Int

    public static let maxLineLength = 400

    public init(_ text: String, maxLines: Int) {
        var shown: [Substring] = []
        var total = 0
        for line in text.split(separator: "\n", omittingEmptySubsequences: false) {
            total += 1
            if shown.count < maxLines {
                shown.append(line.count > Self.maxLineLength ? Substring(line.prefix(Self.maxLineLength) + "…") : line)
            }
        }
        // A trailing newline is not a line of its own.
        if text.hasSuffix("\n"), total > 0 {
            total -= 1
            if shown.count > total { shown.removeLast() }
        }
        self.text = shown.joined(separator: "\n")
        hiddenLines = max(0, total - shown.count)
    }

    /// "…and 12 more lines", or nil when nothing was cut.
    public var moreLine: String? {
        hiddenLines > 0 ? "…and \(hiddenLines) more line\(hiddenLines == 1 ? "" : "s")" : nil
    }
}
