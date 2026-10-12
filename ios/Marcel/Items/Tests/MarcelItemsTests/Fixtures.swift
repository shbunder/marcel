import Foundation
import MarcelKit
import SwiftData
import XCTest

@testable import MarcelItems

/// Hub objects and event frames. The items, boards, document versions and history come from the mock hub
/// (`MockHub/*.json`, `*.md`: recorded with curl from `make mock-hub`); the dashboard comes from the contract's own
/// examples (`contracts/dashboard.schema.json`). Kinds the mock hub has no item of (an HTML page, a file, a link, a
/// branch) are written here in the contract's shape.
enum Fx {
    /// "Now" in every test: 2026-10-12 08:16 UTC, two minutes after the mock hub's document got its version 3.
    static let now = ItemsJSON.parse("2026-10-12T08:16:00Z")!
    static let utc = TimeZone(identifier: "UTC")!
    static let conversation = "cnv_main_marcel"

    static var folder: URL { URL(fileURLWithPath: #filePath).deletingLastPathComponent() }
    static var repository: URL {
        folder.deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
            .deletingLastPathComponent().deletingLastPathComponent()
    }

    /// A file the mock hub answered with.
    static func mockHub(_ name: String) -> Data {
        try! Data(contentsOf: folder.appendingPathComponent("MockHub").appendingPathComponent(name))
    }

    static func json(_ data: Data) -> Any { try! JSONSerialization.jsonObject(with: data) }

    /// `GET /artifacts` from the mock hub: two boards and a doc in Energy contracts and Marcel dev, a PR, the inbox's
    /// board.
    static var mockArtifacts: [[String: Any]] {
        (json(mockHub("artifacts.json")) as! [String: Any])["items"] as! [[String: Any]]
    }

    static var mockProjects: [[String: Any]] { json(mockHub("projects.json")) as! [[String: Any]] }

    static func mockArtifact(_ id: String) -> [String: Any] {
        mockArtifacts.first { $0["id"] as? String == id }!
    }

    /// The mock hub's Markdown document (Energy offers compared, v3 by Energy contracts) and its board.
    static let docId = "art_01JA0B1"
    static let boardId = "art_01JA0A2"
    static let prId = "art_01J9ZT1"
    static let energy = "prj_01JA0A1"

    /// The contract's dashboard examples: the Docker volumes dashboard (stat, table, chart, checklist, list) and the
    /// Energy contracts board (sections and notes).
    static var contractDashboards: [Data] {
        let schema = try! Data(contentsOf: repository.appendingPathComponent("contracts/dashboard.schema.json"))
        let examples = (json(schema) as! [String: Any])["examples"] as! [Any]
        return examples.map { try! JSONSerialization.data(withJSONObject: $0, options: [.sortedKeys]) }
    }

    /// Wireframe 4B's document, written as a worker would write it, at the mock hub document's version 3.
    static let comparison = Data("""
        # Switch to Supplier A

        Cheapest of 4 offers on our address. With 4 kWp solar the yearly bill drops to **€1,010**.

        | Offer | Per year |
        |---|---|
        | Current | €1,496 |
        | Supplier A | €1,284 |
        | A + solar | €1,010 |

        ## Open questions

        - Solar quote expected Friday
        - Heat pump: wait for the [2027 subsidy](https://www.vlaanderen.be/premies) news

        Sources: [the offers](javascript:alert(1)) are in the bills folder.
        """.utf8)

    static func artifactJSON(
        _ id: String, _ title: String, kind: String, project: String = Fx.energy, version: Int = 1,
        updated: String = "2026-10-12T06:00:00Z", extra: [String: Any] = [:]
    ) -> [String: Any] {
        [
            "id": id, "project_id": project, "kind": kind, "title": title, "current_version": version,
            "created_at": "2026-10-09T20:00:00Z", "updated_at": updated,
        ].merging(extra) { _, new in new }
    }

    static let htmlId = "art_html"
    static let fileId = "art_bills"
    static let linkId = "art_link"
    static let branchId = "art_branch"
    static let dashboardId = "art_volumes"

    static var html: [String: Any] {
        artifactJSON(htmlId, "Offer table", kind: "doc", extra: ["content_type": "text/html", "updated_by": "Energy contracts"])
    }
    static let htmlContent = Data("""
        <html><head><style>td{padding:4px}</style><script>document.title = 'ran'</script></head>
        <body><h1>Offers</h1><p>Luminus &amp; Engie, per year.</p>
        <table><tr><td>Luminus</td><td>&euro;1,084</td></tr></table>
        <a href="https://www.luminus.be/offer">The offer</a></body></html>
        """.utf8)
    static var file: [String: Any] {
        artifactJSON(fileId, "Bills 2026", kind: "file", updated: "2026-10-11T19:00:00Z",
                     extra: ["content_type": "application/pdf", "size_bytes": 1_234_567])
    }
    /// A one-page PDF QuickLook can open.
    static let pdf = Data("""
        %PDF-1.4
        1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj
        2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj
        3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj
        trailer<</Root 1 0 R>>
        %%EOF
        """.utf8)
    static var link: [String: Any] {
        artifactJSON(linkId, "Engie price list", kind: "link", updated: "2026-10-10T10:00:00Z",
                     extra: ["url": "https://www.engie.be/prices"])
    }
    static var branch: [String: Any] {
        artifactJSON(branchId, "fix/avatar-blink", kind: "branch", project: "prj_01J9ZM1",
                     updated: "2026-10-11T10:00:00Z",
                     extra: ["url": "https://github.com/shbunder/marcel/compare/fix/avatar-blink"])
    }
    static var dashboard: [String: Any] {
        artifactJSON(dashboardId, "Docker volumes on the NUC", kind: "dashboard", project: "prj_01J9ZM1",
                     version: 4, updated: "2026-10-12T07:00:00Z",
                     extra: ["content_type": "application/json", "updated_by": "Disk check"])
    }

    /// The mock hub's items plus one of each kind it has none of.
    static var allArtifacts: [[String: Any]] { mockArtifacts + [html, file, link, branch, dashboard] }

    static func decode<T: Decodable>(_ json: [String: Any]) -> T {
        try! ItemsJSON.decoder().decode(T.self, from: try! JSONSerialization.data(withJSONObject: json))
    }

    static func artifact(_ json: [String: Any]) -> Artifact { decode(json) }

    static func version(_ id: String, _ number: Int, summary: String? = nil, author: String? = nil,
                        at: String = "2026-10-12T08:14:00Z") -> [String: Any] {
        var json: [String: Any] = ["artifact_id": id, "version": number, "created_at": at]
        if let summary { json["summary"] = summary }
        if let author { json["author"] = author }
        return json
    }

    static func messageJSON(_ id: String, text: String, project: String?, clientId: String) -> [String: Any] {
        var json: [String: Any] = [
            "id": id, "conversation_id": conversation, "role": "user", "kind": "text", "text": text,
            "client_id": clientId, "created_at": "2026-10-12T08:16:00Z",
        ]
        if let project { json["project_id"] = project }
        return json
    }

    static func frame(_ type: String, _ data: Any, id: Int) -> EventFrame {
        let json: [String: Any] = ["id": id, "type": type, "at": "2026-10-12T08:16:00Z", "data": data]
        return try! EventFrame.decode(try! JSONSerialization.data(withJSONObject: json))
    }
}

/// An in-memory cache fed with frames (what the live stream does), read back the way the screens read it.
@MainActor
final class FedCache {
    let cache: MarcelCache
    private var nextId = 1

    init() throws {
        cache = MarcelCache(container: try MarcelCache.makeContainer(inMemory: true))
    }

    /// The cache after the stream brought the mock hub's projects and items, and the extra kinds.
    static func seeded() throws -> FedCache {
        let fed = try FedCache()
        for project in Fx.mockProjects { try fed.apply("project.created", project) }
        for artifact in Fx.allArtifacts { try fed.apply("artifact.created", artifact) }
        return fed
    }

    func apply(_ type: String, _ data: Any) throws {
        try cache.apply(Fx.frame(type, data, id: nextId))
        nextId += 1
    }

    var input: ItemsInput {
        get throws { try ItemsInput.read(from: cache.container.mainContext) }
    }

    func library() throws -> ItemsLibrary {
        let input = try input
        return ItemsLibrary(artifacts: input.artifacts, projects: input.projects, now: Fx.now, timeZone: Fx.utc)
    }

    /// One item as the viewer's `@Query` reads it.
    func artifact(_ id: String) throws -> Artifact? {
        let rows = try cache.container.mainContext.fetch(
            FetchDescriptor<CachedArtifact>(predicate: #Predicate { $0.id == id }))
        return ItemsInput.artifact(rows)
    }
}

/// A hub that answers the way the contract says, from the mock hub's recordings, and remembers what it was asked.
@MainActor
final class FakeItemsService: ItemsService {
    var contents: [String: Data] = [
        "\(Fx.docId)@3": Fx.mockHub("doc-v3.md"), "\(Fx.docId)@2": Fx.mockHub("doc-v2.md"),
        "\(Fx.boardId)@7": Fx.mockHub("board-energy.json"), "art_01J9ZM2@1": Fx.mockHub("board-dev.json"),
        "\(Fx.htmlId)@1": Fx.htmlContent, "\(Fx.fileId)@1": Fx.pdf, "\(Fx.dashboardId)@4": Fx.contractDashboards[0],
    ]
    var versionsById: [String: [[String: Any]]] = [
        Fx.docId: Fx.json(Fx.mockHub("doc-versions.json")) as! [[String: Any]],
    ]
    var contentError: ItemsError?
    var sendError: ItemsError?
    var asked: [String] = []
    var sent: [(text: String, conversationId: String, projectId: String)] = []

    func artifacts() async throws -> [Artifact] {
        asked.append("artifacts")
        return Fx.allArtifacts.map(Fx.artifact)
    }

    func artifact(id: String) async throws -> Artifact {
        asked.append("artifact \(id)")
        guard let json = Fx.allArtifacts.first(where: { $0["id"] as? String == id }) else { throw ItemsError.gone }
        return Fx.artifact(json)
    }

    func content(id: String, version: Int?) async throws -> Data {
        asked.append("content \(id) v\(version.map(String.init) ?? "current")")
        if let contentError { throw contentError }
        guard let version, let data = contents["\(id)@\(version)"] else { throw ItemsError.gone }
        return data
    }

    func versions(id: String) async throws -> [ArtifactVersion] {
        asked.append("versions \(id)")
        return (versionsById[id] ?? [Fx.version(id, 1)]).map { Fx.decode($0) }
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
}
