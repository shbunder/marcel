import Foundation
import MarcelKit
import OpenAPIRuntime

/// One task event, read into what the quest view needs. `data`'s shape depends on `type`
/// (app-api.yaml `TaskEventType`; the transcript kinds are transcript.schema.json's `$defs`, unchanged).
///
/// Everything in here is untrusted text from a Claude Code session: it is only ever shown as plain text, never as
/// Markdown or a link.
public enum QuestEntry: Equatable, Sendable {
    /// `text`: something Claude wrote (`assistant`) or a prompt it was given (`user`).
    case text(role: String, text: String)
    /// `tool_call`: one line for the folded row (the command, the file, the agent's description).
    case toolCall(toolUseId: String, tool: String, summary: String)
    /// `tool_result`: what came back, already truncated to 8 KB by the runner.
    case toolResult(toolUseId: String, isError: Bool, denied: Bool, text: String, truncated: Bool)
    /// `diff`: an Edit, Write or MultiEdit as unified-diff hunks.
    case diff(toolUseId: String, filePath: String, hunks: [DiffHunk])
    /// `permission`: the session asks before running a tool; answered with the approval id.
    case permission(approvalId: String, tool: String, summary: String)
    case subagentStart(description: String, subagentType: String?)
    case subagentStop(status: String, summary: String?)
    case error(message: String, usageLimit: Bool)
    /// `raw`: a transcript line the runner did not recognise.
    case raw(entryType: String)
    case state(from: String?, to: String, reason: String)
    /// `progress`: the worker's `report(progress)`, one step of the checklist, and the steps it still plans after it
    /// (`next`, app API 0.5; empty when it gave none).
    case progress(text: String, next: [String])
    case artifact(artifactId: String, kind: String, title: String)
    /// `note`: a steer message (`user`), Marcel's message or the opening prompt (`marcel`).
    case note(author: String, text: String, handback: Bool, prompt: Bool)
    /// An event whose `data` does not have the fields its type promises. Shown as one plain line, never a crash.
    case unreadable(type: String)
}

public struct DiffHunk: Equatable, Sendable {
    public var oldStart: Int
    public var newStart: Int
    /// Unified-diff lines, each starting with ' ', '+' or '-'.
    public var lines: [String]

    public init(oldStart: Int, newStart: Int, lines: [String]) {
        self.oldStart = oldStart
        self.newStart = newStart
        self.lines = lines
    }
}

/// A task event with its place in the task's sequence.
public struct QuestEvent: Identifiable, Equatable, Sendable {
    public var seq: Int
    public var at: Date
    public var entry: QuestEntry
    public var id: Int { seq }

    public init(seq: Int, at: Date, entry: QuestEntry) {
        self.seq = seq
        self.at = at
        self.entry = entry
    }

    public init(_ event: TaskEvent) {
        self.init(seq: event.seq, at: event.at, entry: QuestEntry(type: event._type, data: event.data.value))
    }
}

extension QuestEntry {
    /// Reads `data` straight from the decoded JSON object (no second decoding pass, so 5,000 events stay cheap).
    init(type: Components.Schemas.TaskEventType, data: [String: (any Sendable)?]) {
        let read = Fields(data)
        let entry: QuestEntry?
        switch type {
        case .text:
            entry = read.string("text").map { .text(role: read.string("role") ?? "assistant", text: $0) }
        case .toolCall:
            entry = read.string("tool_use_id").map { id in
                let tool = read.string("tool") ?? "Tool"
                return .toolCall(toolUseId: id, tool: tool, summary: read.string("summary") ?? tool)
            }
        case .toolResult:
            entry = read.string("tool_use_id").map {
                .toolResult(toolUseId: $0, isError: read.bool("is_error"), denied: read.bool("denied"),
                            text: read.string("text") ?? "", truncated: read.bool("truncated"))
            }
        case .diff:
            entry = read.string("file_path").map { path in
                let hunks = read.objects("hunks").map { hunk in
                    let fields = Fields(hunk)
                    return DiffHunk(oldStart: fields.int("old_start") ?? 0, newStart: fields.int("new_start") ?? 0,
                                    lines: fields.strings("lines"))
                }
                return .diff(toolUseId: read.string("tool_use_id") ?? "", filePath: path, hunks: hunks)
            }
        case .permission:
            entry = read.string("approval_id").map {
                .permission(approvalId: $0, tool: read.string("tool") ?? "a tool",
                            summary: read.string("summary") ?? "")
            }
        case .subagentStart:
            entry = .subagentStart(description: read.string("description") ?? "subagent",
                                   subagentType: read.string("subagent_type"))
        case .subagentStop:
            entry = .subagentStop(status: read.string("status") ?? "unknown", summary: read.string("summary"))
        case .error:
            entry = read.string("message").map { .error(message: $0, usageLimit: read.bool("usage_limit")) }
        case .raw:
            entry = .raw(entryType: read.string("entry_type") ?? "unknown")
        case .state:
            entry = read.string("to").map {
                .state(from: read.string("from"), to: $0, reason: read.string("reason") ?? "")
            }
        case .progress:
            entry = read.string("text").map { .progress(text: $0, next: read.strings("next")) }
        case .artifact:
            entry = read.string("artifact_id").map {
                .artifact(artifactId: $0, kind: read.string("kind") ?? "file", title: read.string("title") ?? "An item")
            }
        case .note:
            entry = read.string("text").map {
                .note(author: read.string("author") ?? "user", text: $0, handback: read.bool("handback"),
                      prompt: read.bool("prompt"))
            }
        }
        self = entry ?? .unreadable(type: type.rawValue)
    }
}

/// Typed reads from an untyped JSON object.
private struct Fields {
    let values: [String: (any Sendable)?]

    init(_ values: [String: (any Sendable)?]) {
        self.values = values
    }

    func string(_ key: String) -> String? { values[key] as? String }

    func bool(_ key: String) -> Bool { (values[key] as? Bool) ?? false }

    func int(_ key: String) -> Int? {
        switch values[key] {
        case let value as Int: value
        case let value as Double: Int(value)
        default: nil
        }
    }

    func strings(_ key: String) -> [String] {
        ((values[key] as? [(any Sendable)?]) ?? []).compactMap { $0 as? String }
    }

    func objects(_ key: String) -> [[String: (any Sendable)?]] {
        ((values[key] as? [(any Sendable)?]) ?? []).compactMap { $0 as? [String: (any Sendable)?] }
    }
}
