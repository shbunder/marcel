import Foundation
import MarcelKit
import SwiftData
import XCTest

@testable import MarcelMap

/// Hub objects and event frames, written as the contract's JSON so they go through the same decoding as the app.
enum Fx {
    /// "Now" in every test: Saturday 2026-10-03 04:00 UTC. Times below are seconds before it.
    static let now = Date(timeIntervalSince1970: 1_791_000_000)
    static let utc = TimeZone(identifier: "UTC")!

    static func ago(_ seconds: TimeInterval) -> String {
        now.addingTimeInterval(-seconds).formatted(Date.ISO8601FormatStyle())
    }

    static let minute: TimeInterval = 60
    static let hour: TimeInterval = 3600
    static let day: TimeInterval = 86_400

    static let conversation = "cnv_main_marcel"

    static func projectJSON(
        _ id: String, _ name: String, slug: String? = nil, goal: String? = nil, working: Int = 0, needsYou: Int = 0,
        done: Int = 0, updated: TimeInterval = Fx.hour, claude: String? = nil, archived: Bool = false
    ) -> [String: Any] {
        var json: [String: Any] = [
            "id": id, "agent_id": "agt_marcel", "slug": slug ?? id.replacingOccurrences(of: "prj_", with: ""),
            "name": name, "board_artifact_id": "art_board_\(id)", "archived": archived,
            "counts": ["working": working, "needs_you": needsYou, "done_7d": done], "created_at": ago(30 * day),
            "updated_at": ago(updated),
        ]
        if let goal { json["goal"] = goal }
        if let claude { json["claude_project_url"] = claude }
        return json
    }

    static func taskJSON(
        _ id: String, _ title: String, state: String, project: String = "prj_energy", location: String = "nuc",
        active: TimeInterval = 5 * 60, extra: [String: Any] = [:]
    ) -> [String: Any] {
        [
            "id": id, "agent_id": "agt_marcel", "project_id": project, "title": title, "location": location,
            "state": state, "model": "sonnet", "origin": "marcel", "created_at": ago(active + 2 * hour),
            "updated_at": ago(active), "last_activity_at": ago(active),
        ].merging(extra) { _, new in new }
    }

    static func artifactJSON(
        _ id: String, _ title: String, kind: String = "doc", project: String = "prj_energy", version: Int = 1,
        updated: TimeInterval = Fx.hour, extra: [String: Any] = [:]
    ) -> [String: Any] {
        var json: [String: Any] = [
            "id": id, "project_id": project, "kind": kind, "title": title, "current_version": version,
            "created_at": ago(updated + day), "updated_at": ago(updated),
        ]
        switch kind {
        case "doc": json["content_type"] = "text/markdown"
        case "board", "dashboard": json["content_type"] = "application/json"
        case "pr":
            json["url"] = "https://github.com/shbunder/marcel/pull/41"
            json["pr"] = ["number": 41, "state": "open", "checks": "passing"]
        case "link": json["url"] = "https://example.com"
        default: break
        }
        return json.merging(extra) { _, new in new }
    }

    static func boardJSON(_ project: String, version: Int, updated: TimeInterval) -> [String: Any] {
        artifactJSON("art_board_\(project)", "Project board", kind: "board", project: project, version: version,
                     updated: updated)
    }

    static func messageJSON(_ id: String, text: String, project: String?, clientId: String) -> [String: Any] {
        var json: [String: Any] = [
            "id": id, "conversation_id": conversation, "role": "user", "kind": "text", "text": text,
            "client_id": clientId, "created_at": ago(0),
        ]
        if let project { json["project_id"] = project }
        return json
    }

    /// The board's content (`contracts/dashboard.schema.json`), as the mock hub builds it.
    static let boardContent = Data("""
        {"version": 1, "title": "Home energy", "widgets": [
          {"type": "section", "title": "Waiting on you", "empty_text": "Nothing waits on you.", "widgets": []},
          {"type": "section", "title": "In flight", "widgets": [
            {"type": "checklist", "items": [
              {"text": "Contract switched (−€486/yr)", "done": true},
              {"text": "Solar quote Friday", "done": false},
              {"text": "Heat pump: wait for subsidy news", "done": false}]}]}]}
        """.utf8)

    static func decode<T: Decodable>(_ json: [String: Any]) -> T {
        try! MapJSON.decoder().decode(T.self, from: try! JSONSerialization.data(withJSONObject: json))
    }

    static func frame(_ type: String, _ data: Any, id: Int) -> EventFrame {
        let json: [String: Any] = ["id": id, "type": type, "at": ago(0), "data": data]
        return try! EventFrame.decode(try! JSONSerialization.data(withJSONObject: json))
    }

    /// Wireframe 5A's projects: Home energy waits on you, Family, Marcel dev (linked to Claude Code), Health, Inbox.
    static var projects: [[String: Any]] { [
        projectJSON("prj_energy", "Home energy", goal: "Lower the bill by 20% this year", working: 1, needsYou: 1,
                    updated: 28 * minute),
        projectJSON("prj_family", "Family", working: 1, updated: 2 * hour),
        projectJSON("prj_dev", "Marcel dev", working: 3, updated: 3 * hour,
                    claude: "https://claude.ai/project/marcel-dev"),
        projectJSON("prj_health", "Health", updated: 2 * day),
        projectJSON("prj_inbox", "Inbox", slug: "inbox", updated: 5 * minute),
    ] }

    static var artifacts: [[String: Any]] { [
        boardJSON("prj_energy", version: 7, updated: 28 * minute),
        artifactJSON("art_cmp", "Comparison", updated: 3 * hour),
        artifactJSON("art_bills", "Bills 2026", kind: "file", updated: day,
                     extra: ["content_type": "application/pdf"]),
        artifactJSON("art_f1", "Lunch plan", project: "prj_family"),
        artifactJSON("art_f2", "Dentist times", project: "prj_family", updated: 2 * hour),
        artifactJSON("art_f3", "School trips", project: "prj_family", updated: 3 * hour),
        artifactJSON("art_f4", "Holiday", kind: "link", project: "prj_family", updated: 4 * hour),
        artifactJSON("art_pr", "Avatar sprite", kind: "pr", project: "prj_dev"),
        artifactJSON("art_h1", "Blood test", project: "prj_health"),
        artifactJSON("art_h2", "Running plan", project: "prj_health", updated: 2 * hour),
    ] }

    static var tasks: [[String: Any]] { [
        taskJSON("t_solar", "Solar quotes", state: "working", active: 2 * minute),
        taskJSON("t_contracts", "Energy contracts", state: "done", active: 20 * hour),
        taskJSON("t_meter", "Meter reading", state: "needs_you", active: 30 * minute,
                 extra: ["waiting_for": "Send the photo of the meter"]),
        taskJSON("t_lunch", "Sunday lunch", state: "working", project: "prj_family", location: "cloud",
                 active: 6 * minute),
    ] }
}

/// An in-memory cache fed with frames (what the live stream does), read back the way the screens read it.
@MainActor
final class FedCache {
    let cache: MarcelCache
    private var nextId = 1

    init() throws {
        cache = MarcelCache(container: try MarcelCache.makeContainer(inMemory: true))
    }

    /// The cache after the stream brought wireframe 5A's projects, items and quests.
    static func seeded() throws -> FedCache {
        let fed = try FedCache()
        for project in Fx.projects { try fed.apply("project.created", project) }
        for artifact in Fx.artifacts { try fed.apply("artifact.created", artifact) }
        for task in Fx.tasks { try fed.apply("task.created", task) }
        return fed
    }

    func apply(_ type: String, _ data: [String: Any]) throws {
        try cache.apply(Fx.frame(type, data, id: nextId))
        nextId += 1
    }

    var input: MapInput {
        get throws { try MapInput.read(from: cache.container.mainContext) }
    }

    func map() throws -> ProjectMap {
        let input = try input
        return ProjectMap(projects: input.projects, artifacts: input.artifacts, now: Fx.now, timeZone: Fx.utc)
    }

    func room(_ projectId: String) throws -> ProjectRoom? {
        let input = try input
        return ProjectRoom(projectId: projectId, tasks: input.tasks, artifacts: input.artifacts,
                           projects: input.projects, now: Fx.now)
    }
}

/// A hub that answers the way the contract says, and remembers what it was asked.
@MainActor
final class FakeProjectService: ProjectService {
    var tasks: [String: [String: Any]]
    var archived: Set<String> = []
    var moveError: MapError?
    var sendError: MapError?
    var notesBySlug: [String: String] = [:]
    var asked: [String] = []
    var sent: [(text: String, conversationId: String, projectId: String)] = []

    init(tasks: [[String: Any]] = Fx.tasks) {
        self.tasks = Dictionary(tasks.map { ($0["id"] as! String, $0) }, uniquingKeysWith: { a, _ in a })
    }

    func projects() async throws -> [Project] {
        asked.append("projects")
        return Fx.projects.map { Fx.decode($0) }
    }

    func artifacts(projectId: String) async throws -> [Artifact] {
        asked.append("artifacts \(projectId)")
        return Fx.artifacts.filter { $0["project_id"] as? String == projectId }.map { Fx.decode($0) }
    }

    func artifactContent(id: String) async throws -> Data {
        asked.append("content \(id)")
        return Fx.boardContent
    }

    func notes(slug: String) async throws -> String? {
        asked.append("notes \(slug)")
        return notesBySlug[slug]
    }

    func mainConversationId() async throws -> String {
        asked.append("me")
        return Fx.conversation
    }

    func send(text: String, clientId: String, conversationId: String, projectId: String) async throws -> Message {
        asked.append("send \(projectId)")
        if let sendError { throw sendError }
        sent.append((text, conversationId, projectId))
        return Fx.decode(Fx.messageJSON("msg_\(sent.count)", text: text, project: projectId, clientId: clientId))
    }

    func move(taskId: String, to projectId: String) async throws -> HubTask {
        asked.append("move \(taskId) to \(projectId)")
        if let moveError { throw moveError }
        if archived.contains(projectId) { throw MapError.projectArchived }
        guard var task = tasks[taskId] else { throw MapError.questGone }
        task["project_id"] = projectId
        tasks[taskId] = task
        return Fx.decode(task)
    }
}
