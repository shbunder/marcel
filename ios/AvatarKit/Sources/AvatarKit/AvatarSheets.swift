import Foundation
import Observation

/// Each agent's own sprite sheet (B-44), as the hub serves it (`GET /agents/{id}/avatar`), kept on disk so it shows
/// at once next launch and offline. An agent whose sheet the phone has never had plays the bundled giraffe.
///
/// AvatarKit does not talk to the hub itself: the app passes `fetch`, which asks for the sheet with the ETag the phone
/// has (`If-None-Match`) and answers nil when nothing changed (304).
@MainActor
@Observable
public final class AvatarSheets {
    /// One download: the sheet's JSON and PNG, and the ETag both carry.
    public struct Download: Sendable {
        public var json: Data
        public var png: Data
        public var etag: String?

        public init(json: Data, png: Data, etag: String?) {
            self.json = json
            self.png = png
            self.etag = etag
        }
    }

    /// Asks the hub for `agentId`'s sheet, sending `ifNoneMatch` when the phone has one. Nil means "not modified".
    public typealias Fetch = @Sendable (_ agentId: String, _ ifNoneMatch: String?) async throws -> Download?

    /// The sheets the phone has, by agent id.
    public private(set) var sheets: [String: SpriteSheet] = [:]
    @ObservationIgnored private var etags: [String: String] = [:]
    @ObservationIgnored private var loading: Set<String> = []
    @ObservationIgnored private let directory: URL?
    @ObservationIgnored private let fetch: Fetch

    /// - Parameters:
    ///   - directory: where sheets are kept between launches (`defaultDirectory`); nil keeps them in memory only.
    ///   - fetch: how to ask the hub.
    public init(directory: URL?, fetch: @escaping Fetch) {
        self.directory = directory
        self.fetch = fetch
        readDisk()
    }

    /// Sheets that are never fetched: everyone plays the giraffe (previews, snapshots).
    public static func bundled() -> AvatarSheets { AvatarSheets(directory: nil) { _, _ in nil } }

    /// `Library/Caches/avatars`: the system may clear it, and then the next fetch fills it again.
    public static func defaultDirectory() -> URL {
        URL.cachesDirectory.appending(path: "avatars", directoryHint: .isDirectory)
    }

    /// The sheet to play for `agentId`: its own when the phone has it, otherwise the bundled giraffe.
    public func sheet(for agentId: String?) -> SpriteSheet {
        agentId.flatMap { sheets[$0] } ?? .giraffe
    }

    /// Fetches `agentId`'s sheet unless the phone already has the one with `etag` (from `agent.updated`). Offline or
    /// on a bad answer the phone keeps what it has, so the avatar never goes blank.
    public func refresh(agentId: String, etag: String? = nil) async {
        if let etag, etag == etags[agentId], sheets[agentId] != nil { return }
        guard loading.insert(agentId).inserted else { return }
        defer { loading.remove(agentId) }
        let known = sheets[agentId] == nil ? nil : etags[agentId]
        guard let download = try? await fetch(agentId, known),
              let sheet = try? SpriteSheet(json: download.json, png: download.png)
        else { return }
        sheets[agentId] = sheet
        etags[agentId] = download.etag
        writeDisk(agentId: agentId, download)
    }

    // MARK: Disk

    private func files(_ agentId: String) -> (json: URL, png: URL, etag: URL)? {
        // Only plain ids become file names (letters, digits, `_` and `-`); anything else plays from memory only.
        let plain = agentId.unicodeScalars.allSatisfy {
            ("a"..."z").contains($0) || ("A"..."Z").contains($0) || ("0"..."9").contains($0) || $0 == "_" || $0 == "-"
        }
        guard let directory, !agentId.isEmpty, agentId.count <= 100, plain else { return nil }
        return (directory.appending(path: "\(agentId).json"), directory.appending(path: "\(agentId).png"),
                directory.appending(path: "\(agentId).etag"))
    }

    private func readDisk() {
        guard let directory,
              let names = try? FileManager.default.contentsOfDirectory(atPath: directory.path(percentEncoded: false))
        else { return }
        for name in names where name.hasSuffix(".json") {
            let agentId = String(name.dropLast(".json".count))
            guard let files = files(agentId),
                  let json = try? Data(contentsOf: files.json), let png = try? Data(contentsOf: files.png),
                  let sheet = try? SpriteSheet(json: json, png: png)
            else { continue }
            sheets[agentId] = sheet
            etags[agentId] = try? String(contentsOf: files.etag, encoding: .utf8)
        }
    }

    private func writeDisk(agentId: String, _ download: Download) {
        guard let directory, let files = files(agentId) else { return }
        try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        try? download.json.write(to: files.json, options: .atomic)
        try? download.png.write(to: files.png, options: .atomic)
        if let etag = download.etag {
            try? Data(etag.utf8).write(to: files.etag, options: .atomic)
        } else {
            try? FileManager.default.removeItem(at: files.etag)
        }
    }
}
