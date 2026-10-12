import Foundation
import MarcelKit
import XCTest

@testable import MarcelQuest

/// Hub objects, task events and event frames, written as the contract's JSON so they go through the same decoding as
/// the app.
enum Fx {
    /// "Now" in every test: 2026-10-03 07:20 UTC.
    static let now = Date(timeIntervalSince1970: 1_791_000_000)

    static func ago(_ seconds: TimeInterval) -> String {
        now.addingTimeInterval(-seconds).formatted(Date.ISO8601FormatStyle())
    }

    static let taskId = "tsk_energy"

    static func taskJSON(state: String = "working", location: String = "nuc", project: String = "prj_energy",
                         extra: [String: Any] = [:]) -> [String: Any] {
        [
            "id": taskId, "agent_id": "agt_marcel", "project_id": project, "title": "Energy contracts",
            "location": location, "state": state, "model": "sonnet", "origin": "marcel",
            "created_at": ago(3600), "updated_at": ago(60), "last_activity_at": ago(60),
        ].merging(extra) { _, new in new }
    }

    static func task(state: String = "working", location: String = "nuc", project: String = "prj_energy",
                     extra: [String: Any] = [:]) -> HubTask {
        decode(taskJSON(state: state, location: location, project: project, extra: extra))
    }

    static func eventJSON(_ seq: Int, _ type: String, _ data: [String: Any], task: String = taskId) -> [String: Any] {
        ["task_id": task, "seq": seq, "type": type, "at": ago(TimeInterval(1000 - seq)), "data": data]
    }

    static func event(_ seq: Int, _ type: String, _ data: [String: Any]) -> TaskEvent {
        decode(eventJSON(seq, type, data))
    }

    static func approvalJSON(_ id: String, state: String, via: String? = nil) -> [String: Any] {
        var json: [String: Any] = [
            "id": id, "task_id": taskId, "tool": "Bash", "summary": "Fetch Engie's price list",
            "state": state, "created_at": ago(120),
        ]
        if let via {
            json["answered_via"] = via
            json["answered_at"] = ago(10)
        }
        return json
    }

    static func approval(_ id: String, state: String, via: String? = nil) -> Approval {
        decode(approvalJSON(id, state: state, via: via))
    }

    static func frame(_ type: String, _ data: Any, id: Int) -> EventFrame {
        let json: [String: Any] = ["id": id, "type": type, "at": ago(0), "data": data]
        return try! EventFrame.decode(try! JSONSerialization.data(withJSONObject: json))
    }

    static func decode<T: Decodable>(_ json: [String: Any]) -> T {
        try! QuestJSON.decoder().decode(T.self, from: try! JSONSerialization.data(withJSONObject: json))
    }

    // MARK: Event data, one per kind

    static func text(_ text: String, role: String = "assistant") -> [String: Any] { ["role": role, "text": text] }

    static func toolCall(_ id: String, _ tool: String, _ summary: String) -> [String: Any] {
        ["tool_use_id": id, "tool": tool, "summary": summary, "input": ["command": summary]]
    }

    static func toolResult(_ id: String, _ text: String, error: Bool = false, denied: Bool = false) -> [String: Any] {
        var json: [String: Any] = ["tool_use_id": id, "is_error": error, "text": text]
        if denied { json["denied"] = true }
        return json
    }

    static var modelDiff: [String: Any] { [
        "tool_use_id": "toolu_edit", "file_path": "energy/model.py",
        "hunks": [[
            "old_start": 12, "old_lines": 4, "new_start": 12, "new_lines": 5,
            "lines": [
                " def yearly_saving(offer, solar_kwp):",
                "-    panels = 4.2  # the whole roof",
                "+    # The roof is small: 4 kWp, not 4.2.",
                "+    panels = min(solar_kwp, 4.0)",
                "     return offer.price * 3500 - panels * 950",
            ],
        ]],
    ] }

    static func permission(_ approvalId: String) -> [String: Any] {
        ["approval_id": approvalId, "tool": "Bash", "summary": "Fetch Engie's price list"]
    }

    static func note(_ text: String, author: String = "user", handback: Bool = false, prompt: Bool = false)
        -> [String: Any]
    {
        var json: [String: Any] = ["author": author, "text": text]
        if handback { json["handback"] = true }
        if prompt { json["prompt"] = true }
        return json
    }

    /// Wireframe 2A: the energy quest three steps in, waiting on an approval, after you told it about the roof.
    static var energyHistory: [TaskEvent] {
        [
            event(1, "note", note("Compare gas and electricity offers before the renewal on 1 December.",
                                  author: "marcel", prompt: true)),
            event(2, "state", ["from": "starting", "to": "working", "reason": "The session is working"]),
            event(3, "progress", ["text": "Read the last 12 months of bills"]),
            event(4, "progress", ["text": "Fetched offers from 4 suppliers"]),
            event(5, "text", text("Modelling the yearly cost with and without the solar panels.")),
            event(6, "tool_call", toolCall("toolu_model", "Bash", "python model.py --solar 4.2kWp")),
            event(7, "tool_result", toolResult("toolu_model", "yearly saving: €212 → €486")),
            event(8, "progress", ["text": "Modelling with and without solar"]),
            event(9, "note", note("Use 4 kWp, the roof is small")),
            event(10, "tool_call", toolCall("toolu_edit", "Edit", "energy/model.py")),
            event(11, "tool_result", toolResult("toolu_edit", "The file energy/model.py has been updated.")),
            event(12, "diff", modelDiff),
            event(13, "permission", permission("apr_engie")),
        ]
    }
}

/// A hub that answers the way the contract says, and remembers what it was asked.
@MainActor
final class FakeQuestViewService: QuestViewService {
    var task: [String: Any]
    /// The hub's whole transcript; pages are cut from it by `after`.
    var history: [TaskEvent]
    var pageSize = 200
    var asked: [String] = []
    var answerError: QuestViewError?
    var steerError: QuestViewError?
    var stopError: QuestViewError?

    init(task: [String: Any] = Fx.taskJSON(extra: ["approval_ids": ["apr_engie"]]), history: [TaskEvent] = []) {
        self.task = task
        self.history = history
    }

    func task(id: String) async throws -> HubTask {
        asked.append("task")
        return Fx.decode(task)
    }

    func events(taskId: String, after: Int?) async throws -> (events: [TaskEvent], hasMore: Bool) {
        asked.append("events after \(after.map(String.init) ?? "start")")
        let rest = history.filter { $0.seq > (after ?? 0) }
        return (Array(rest.prefix(pageSize)), rest.count > pageSize)
    }

    func steer(taskId: String, text: String, clientId: String) async throws -> HubTask {
        asked.append("steer \(text)")
        if let steerError { throw steerError }
        return Fx.decode(task)
    }

    func stop(taskId: String) async throws -> HubTask {
        asked.append("stop")
        if let stopError { throw stopError }
        task["state"] = "stopped"
        return Fx.decode(task)
    }

    func handBack(taskId: String, note: String?) async throws -> HubTask {
        asked.append("hand back \(note ?? "-")")
        return Fx.decode(task)
    }

    func move(taskId: String, to projectId: String) async throws -> HubTask {
        asked.append("move \(projectId)")
        if projectId == "prj_archived" { throw QuestViewError.projectArchived }
        task["project_id"] = projectId
        return Fx.decode(task)
    }

    func answer(approvalId: String, approve: Bool) async throws -> Approval {
        asked.append("\(approve ? "approve" : "deny") \(approvalId)")
        if let answerError { throw answerError }
        task["approval_ids"] = []
        return Fx.approval(approvalId, state: approve ? "approved" : "denied", via: "app")
    }
}
