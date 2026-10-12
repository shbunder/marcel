import Foundation
import MarcelKit
import SwiftData
import XCTest

@testable import MarcelQuests

/// Hub objects and event frames, written as the contract's JSON so they go through the same decoding as the app.
enum Fx {
    /// "Now" in every test: 2026-10-03 07:20 UTC. Times below are seconds before it.
    static let now = Date(timeIntervalSince1970: 1_791_000_000)

    static func ago(_ seconds: TimeInterval) -> String {
        now.addingTimeInterval(-seconds).formatted(Date.ISO8601FormatStyle())
    }

    static let minute: TimeInterval = 60
    static let hour: TimeInterval = 3600
    static let day: TimeInterval = 86_400

    static func taskJSON(
        _ id: String, _ title: String, state: String, project: String = "prj_energy", location: String = "nuc",
        created: TimeInterval? = nil, active: TimeInterval = 5 * 60, extra: [String: Any] = [:]
    ) -> [String: Any] {
        // Unless a test says otherwise, a quest started two hours before its last activity.
        let created = created ?? active + 2 * 3600
        return [
            "id": id, "agent_id": "agt_marcel", "project_id": project, "title": title, "location": location,
            "state": state, "model": "sonnet", "origin": "marcel", "created_at": ago(created),
            "updated_at": ago(active), "last_activity_at": ago(active),
        ].merging(extra) { _, new in new }
    }

    static func task(
        _ id: String, _ title: String, state: String, project: String = "prj_energy", location: String = "nuc",
        created: TimeInterval? = nil, active: TimeInterval = 5 * 60, extra: [String: Any] = [:]
    ) -> HubTask {
        decode(taskJSON(id, title, state: state, project: project, location: location, created: created,
                        active: active, extra: extra))
    }

    static func projectJSON(_ id: String, _ name: String, slug: String? = nil, archived: Bool = false) -> [String: Any] {
        [
            "id": id, "agent_id": "agt_marcel", "slug": slug ?? id.replacingOccurrences(of: "prj_", with: ""),
            "name": name, "board_artifact_id": "art_board_\(id)", "archived": archived,
            "counts": ["working": 0, "needs_you": 0, "done_7d": 0], "created_at": ago(30 * day),
            "updated_at": ago(day),
        ]
    }

    static func project(_ id: String, _ name: String, slug: String? = nil, archived: Bool = false) -> Project {
        decode(projectJSON(id, name, slug: slug, archived: archived))
    }

    static func prJSON(_ id: String, task: String, number: Int = 41, state: String = "open",
                       checks: String = "passing", project: String = "prj_dev", updated: TimeInterval = 10 * 60)
        -> [String: Any]
    {
        [
            "id": id, "task_id": task, "project_id": project, "kind": "pr", "title": "PR \(number)",
            "current_version": 1, "url": "https://github.com/shbunder/marcel/pull/\(number)",
            "pr": ["number": number, "state": state, "checks": checks, "review": "requested"],
            "created_at": ago(updated), "updated_at": ago(updated),
        ]
    }

    static func pr(_ id: String, task: String, number: Int = 41, state: String = "open", checks: String = "passing",
                   updated: TimeInterval = 10 * 60) -> Artifact {
        decode(prJSON(id, task: task, number: number, state: state, checks: checks, updated: updated))
    }

    static let projects = [
        project("prj_inbox", "Inbox", slug: "inbox"),
        project("prj_energy", "Home energy"),
        project("prj_family", "Family"),
        project("prj_dev", "Marcel dev"),
    ]

    static func decode<T: Decodable>(_ json: [String: Any]) -> T {
        try! QuestJSON.decoder().decode(T.self, from: try! JSONSerialization.data(withJSONObject: json))
    }

    static func frame(_ type: String, _ data: Any, id: Int) -> EventFrame {
        let json: [String: Any] = ["id": id, "type": type, "at": ago(0), "data": data]
        return try! EventFrame.decode(try! JSONSerialization.data(withJSONObject: json))
    }
}

/// An in-memory cache fed with frames (what the live stream does), read back the way the screen reads it.
@MainActor
final class FedCache {
    let cache: MarcelCache
    private var nextId = 1

    init() throws {
        cache = MarcelCache(container: try MarcelCache.makeContainer(inMemory: true))
    }

    func apply(_ type: String, _ data: [String: Any]) throws {
        try cache.apply(Fx.frame(type, data, id: nextId))
        nextId += 1
    }

    func board(_ filter: QuestFilter = .init()) throws -> QuestBoard {
        try QuestBoard.read(from: cache.container.mainContext, filter: filter, now: Fx.now)
    }
}

/// A hub that answers stop and archive the way the contract says, and remembers what it was asked.
@MainActor
final class FakeQuestService: QuestService {
    var tasks: [String: [String: Any]]
    var archived: [[String: Any]] = []
    var stopError: QuestActionError?
    var asked: [String] = []

    init(tasks: [[String: Any]]) {
        self.tasks = Dictionary(tasks.map { ($0["id"] as! String, $0) }, uniquingKeysWith: { a, _ in a })
    }

    func stop(taskId: String) async throws -> HubTask {
        asked.append("stop \(taskId)")
        if let stopError { throw stopError }
        guard var task = tasks[taskId] else { throw QuestActionError.notFound }
        task["state"] = "stopped"
        task["updated_at"] = Fx.ago(0)
        task["last_activity_at"] = Fx.ago(0)
        tasks[taskId] = task
        return Fx.decode(task)
    }

    func archive(taskId: String) async throws -> HubTask {
        asked.append("archive \(taskId)")
        guard var task = tasks[taskId] else { throw QuestActionError.notFound }
        guard ["done", "failed", "stopped"].contains(task["state"] as? String) else {
            throw QuestActionError.notFinished
        }
        task["archived_at"] = Fx.ago(0)
        tasks[taskId] = task
        archived.append(task)
        return Fx.decode(task)
    }

    func archivedTasks() async throws -> [HubTask] {
        asked.append("archived")
        return archived.map { Fx.decode($0) }
    }
}

extension QuestBoard {
    /// The task ids in each group, top to bottom: what the tests compare.
    var layout: [QuestGroup: [String]] {
        Dictionary(uniqueKeysWithValues: sections.map { ($0.group, $0.rows.map(\.id)) })
    }

    var groups: [QuestGroup] { sections.map(\.group) }

    func row(_ id: String) -> QuestRow? {
        sections.lazy.flatMap(\.rows).first { $0.id == id }
    }
}
