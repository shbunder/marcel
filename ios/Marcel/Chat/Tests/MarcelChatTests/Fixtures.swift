import Foundation
import MarcelKit
import SwiftData
import XCTest

@testable import MarcelChat

/// Hub objects and event frames, written as the contract's JSON so they go through the same decoding as the app.
enum Fx {
    static let conversation = "cnv_main_marcel"
    static let base = Date(timeIntervalSince1970: 1_791_000_000)  // 2026-10-03

    static func at(_ second: Int) -> String {
        base.addingTimeInterval(TimeInterval(second)).formatted(Date.ISO8601FormatStyle())
    }

    static func message(
        _ id: String, at second: Int, role: String = "agent", kind: String = "text", text: String? = nil,
        extra: [String: Any] = [:]
    ) -> [String: Any] {
        var json: [String: Any] = [
            "id": id, "conversation_id": conversation, "role": role, "kind": kind, "created_at": at(second),
        ]
        if let text { json["text"] = text }
        return json.merging(extra) { _, new in new }
    }

    static func milestone(_ id: String, at second: Int, task: String, title: String, event: String,
                          summary: String? = nil) -> [String: Any] {
        var milestone: [String: Any] = ["task_id": task, "task_title": title, "event": event, "location": "nuc"]
        if let summary { milestone["summary"] = summary }
        return message(id, at: second, kind: "milestone", extra: ["milestone": milestone, "task_id": task])
    }

    static func approvalMessage(_ id: String, at second: Int, approval: String, task: String) -> [String: Any] {
        message(id, at: second, kind: "approval", extra: ["approval_id": approval, "task_id": task])
    }

    static func artifactMessage(_ id: String, at second: Int, artifact: String, project: String,
                                task: String? = nil) -> [String: Any] {
        var extra: [String: Any] = ["artifact_id": artifact, "project_id": project]
        if let task { extra["task_id"] = task }
        return message(id, at: second, kind: "artifact", extra: extra)
    }

    static func task(_ id: String, title: String, state: String, project: String = "prj_energy",
                     location: String = "nuc", at second: Int = 0, extra: [String: Any] = [:]) -> [String: Any] {
        [
            "id": id, "agent_id": "agt_marcel", "project_id": project, "title": title, "location": location,
            "state": state, "model": "sonnet", "origin": "marcel", "created_at": at(0), "updated_at": at(second),
        ].merging(extra) { _, new in new }
    }

    static func project(_ id: String, name: String) -> [String: Any] {
        [
            "id": id, "agent_id": "agt_marcel", "slug": id.replacingOccurrences(of: "prj_", with: ""), "name": name,
            "board_artifact_id": "art_board_\(id)", "archived": false,
            "counts": ["working": 0, "needs_you": 0, "done_7d": 0], "created_at": at(0), "updated_at": at(0),
        ]
    }

    static func artifact(_ id: String, title: String, kind: String = "doc", version: Int = 1,
                         project: String = "prj_energy", task: String? = nil) -> [String: Any] {
        var json: [String: Any] = [
            "id": id, "project_id": project, "kind": kind, "title": title, "current_version": version,
            "created_at": at(0), "updated_at": at(version), "updated_by": "Energy contracts",
        ]
        if let task { json["task_id"] = task }
        switch kind {
        case "pr":
            json["url"] = "https://github.com/shbunder/marcel/pull/14"
            json["pr"] = ["number": 14, "state": "open", "checks": "passing", "review": "requested"]
        case "link", "branch": json["url"] = "https://example.com"
        default: json["content_type"] = kind == "doc" ? "text/markdown" : "application/json"
        }
        return json
    }

    static func approval(_ id: String, task: String, summary: String, state: String = "open",
                         detail: String? = nil, via: String? = nil) -> [String: Any] {
        var json: [String: Any] = [
            "id": id, "task_id": task, "tool": "Email", "summary": summary, "state": state, "created_at": at(0),
        ]
        if let detail { json["detail"] = detail }
        if let via { json["answered_via"] = via }
        return json
    }

    // MARK: Decoding

    static func decode<T: Decodable>(_ json: [String: Any], as _: T.Type = T.self) -> T {
        let data = try! JSONSerialization.data(withJSONObject: json)
        return try! ChatJSON.decoder().decode(T.self, from: data)
    }

    // MARK: Frames

    /// One numbered event frame, as `GET /ws` sends it.
    static func frame(_ type: String, _ data: Any, id: Int) -> EventFrame {
        let json: [String: Any] = ["id": id, "type": type, "at": at(id), "data": data]
        return try! EventFrame.decode(try! JSONSerialization.data(withJSONObject: json))
    }
}

/// An in-memory cache fed with frames, read back the way the screen reads it.
@MainActor
final class FedCache {
    let cache: MarcelCache
    private var nextId = 1

    init() throws {
        cache = MarcelCache(container: try MarcelCache.makeContainer(inMemory: true))
    }

    func send(_ type: String, _ data: Any) throws {
        try cache.apply(Fx.frame(type, data, id: nextId))
        nextId += 1
    }

    func input(limit: Int = ChatModel.pageSize) throws -> FeedInput {
        try FeedInput.read(from: cache.container.mainContext, conversationId: Fx.conversation, limit: limit)
    }

    func items(limit: Int = ChatModel.pageSize) throws -> [FeedItem] {
        ChatFeed.items(try input(limit: limit))
    }
}
