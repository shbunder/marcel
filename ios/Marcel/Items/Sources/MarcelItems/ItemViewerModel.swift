import Foundation
import MarcelKit
import Observation

/// The viewer's top line (wireframe 4B): "Updated 2 min ago by Energy contracts · v3". An older version says it is
/// read-only: "v2 of 3 · read only · 9 Oct". VoiceOver hears it without the dot.
public struct VersionLine: Equatable, Sendable {
    public var text: String
    public var spoken: String
    public var isOld: Bool

    public init(_ artifact: Artifact, showing version: Int?, versions: [ArtifactVersion], now: Date,
                timeZone: TimeZone = .current) {
        let current = artifact.currentVersion
        if let version, version != current {
            let made = versions.first { $0.version == version }.map { Ago($0.createdAt, now: now, timeZone: timeZone) }
            text = ["v\(version) of \(current)", "read only", made?.short].compactMap { $0 }.joined(separator: " · ")
            spoken = ["Version \(version) of \(current)", "read only", made.map { "made \($0.spoken)" }]
                .compactMap { $0 }.joined(separator: ", ")
            isOld = true
            return
        }
        let when = Ago(artifact.updatedAt, now: now, timeZone: timeZone)
        let verb = current > 1 ? "Updated" : "Made"
        let by = artifact.updatedBy.map { " by \($0)" } ?? ""
        text = "\(verb) \(when.short)\(by) · v\(current)"
        spoken = "\(verb) \(when.spoken)\(by), version \(current)"
        isOld = false
    }
}

/// One line in the history list: "v3 · Added the Engie offer" and who made it when.
public struct VersionRow: Identifiable, Equatable, Sendable {
    public var id: Int { version }
    public var version: Int
    public var summary: String
    public var detail: String
    public var isCurrent: Bool
    public var spoken: String

    public init(_ version: ArtifactVersion, current: Int, now: Date, timeZone: TimeZone = .current) {
        self.version = version.version
        summary = version.summary ?? (version.version == 1 ? "The first version." : "What changed wasn’t noted.")
        let when = Ago(version.createdAt, now: now, timeZone: timeZone)
        detail = [version.author, when.short].compactMap { $0 }.joined(separator: " · ")
        isCurrent = version.version == current
        let said = summary.hasSuffix(".") ? String(summary.dropLast()) : summary
        spoken = ["Version \(version.version)\(isCurrent ? ", the current one" : "")", said,
                  [version.author.map { "By \($0)" }, when.spoken].compactMap { $0 }.joined(separator: ", ")]
            .joined(separator: ". ")
    }
}

/// The versions of an item as the history list shows them.
public enum VersionsState: Equatable, Sendable {
    case loading
    case loaded([ArtifactVersion])
    case failed(String)
}

/// The viewer's state besides the cache: the bytes of each version shown, the history, the question being asked and
/// the last problem. The item itself comes from the cache, so an `artifact.updated` on the stream changes the
/// version the screen asks for, and the screen loads the new bytes.
@MainActor
@Observable
public final class ItemViewerModel {
    /// The last thing that went wrong, in plain words; cleared by the next action.
    public var problem: String?
    /// "Ask about this": what you are typing.
    public var draft = ""
    /// Whether the question box is open.
    public var asking = false
    /// "Sent to Marcel…" after a question got through.
    public private(set) var sent: String?
    public private(set) var sending = false
    /// Bytes by `key(id, version)`.
    public private(set) var contents: [String: ContentState] = [:]
    /// The history by `key(id, current version)`: a new version fetches it again.
    public private(set) var versions: [String: VersionsState] = [:]

    @ObservationIgnored public var service: (any ItemsService)?
    @ObservationIgnored public var cache: MarcelCache?
    @ObservationIgnored public var conversationId: String?

    public init(service: (any ItemsService)? = nil, cache: MarcelCache? = nil, conversationId: String? = nil) {
        self.service = service
        self.cache = cache
        self.conversationId = conversationId
    }

    public static func key(_ id: String, _ version: Int) -> String { "\(id)@\(version)" }

    /// What the viewer draws for this item at this version (nil: the current one).
    public func body(_ artifact: Artifact, version: Int? = nil) -> ItemBody {
        let shown = version ?? artifact.currentVersion
        let type = shown == artifact.currentVersion ? artifact.contentType
            : history(artifact).first { $0.version == shown }?.contentType ?? artifact.contentType
        return ItemBody.make(artifact, contentType: type, content: contents[Self.key(artifact.id, shown)])
    }

    /// The versions loaded for the item's current version; empty until they arrive.
    public func history(_ artifact: Artifact) -> [ArtifactVersion] {
        if case .loaded(let list) = versions[Self.key(artifact.id, artifact.currentVersion)] { return list }
        return []
    }

    /// Fetches the item into the cache: when the cache does not have it yet (opened before the stream brought it), and
    /// on pull to refresh.
    public func fetch(id: String) async {
        guard let service, let cache else { return }
        do {
            try cache.upsert(try await service.artifact(id: id))
            try cache.save()
        } catch {
            problem = ItemsError.words(for: error)
        }
    }

    /// Fetches one version's bytes, once. Kinds without bytes (pull requests, links) fetch nothing.
    public func load(_ artifact: Artifact, version: Int? = nil) async {
        guard ItemBody.needsContent(artifact.kind) else { return }
        let shown = version ?? artifact.currentVersion
        let key = Self.key(artifact.id, shown)
        switch contents[key] {
        case .loading?, .loaded?: return
        case .failed?, nil: break
        }
        guard let service else {
            contents[key] = .failed(ItemsError.notPaired.errorDescription ?? "")
            return
        }
        contents[key] = .loading
        do {
            // Always by number, so the bytes are stored under the version they belong to even when a newer one
            // lands while they load.
            let data = try await service.content(id: artifact.id, version: shown)
            contents[key] = .loaded(data)
        } catch {
            contents[key] = .failed(Self.contentProblem(error))
        }
    }

    static func contentProblem(_ error: any Error) -> String {
        switch error as? ItemsError {
        case .offline?: "Couldn't load this item. It shows once the hub is back."
        case .gone?: "The hub no longer has this version."
        case let known?: known.errorDescription ?? "Couldn't load this item."
        case nil: "Couldn't load this item."
        }
    }

    /// Fetches the history, once per current version.
    public func loadVersions(_ artifact: Artifact) async {
        let key = Self.key(artifact.id, artifact.currentVersion)
        switch versions[key] {
        case .loading?, .loaded?: return
        case .failed?, nil: break
        }
        guard let service else { return }
        versions[key] = .loading
        do {
            let list = try await service.versions(id: artifact.id).sorted { $0.version > $1.version }
            versions[key] = .loaded(list)
        } catch {
            versions[key] = .failed((error as? ItemsError) == .offline
                ? "Couldn't load the history. It shows once the hub is back." : "Couldn't load the history.")
        }
    }

    /// The text "Ask about this" posts: your question, then which item (and version) it is about, so Marcel can find
    /// it. The contract has no field to attach an item to a message, so the item travels as its id.
    public static func question(_ text: String, about artifact: Artifact, version: Int?) -> String {
        let shown = version ?? artifact.currentVersion
        return "\(text)\n\n(About the item “\(artifact.title)”, version \(shown), \(artifact.id).)"
    }

    /// "Ask about this": posts the question to the main conversation, tagged with the item's project. It never waits
    /// on Marcel; his answer arrives in Chat.
    public func ask(about artifact: Artifact, version: Int? = nil) async {
        let text = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }
        problem = nil
        sent = nil
        guard let service else {
            problem = ItemsError.notPaired.errorDescription
            return
        }
        guard !sending else { return }
        sending = true
        defer { sending = false }
        do {
            let conversationId = try await mainConversation(service)
            let message = try await service.send(
                text: Self.question(text, about: artifact, version: version), clientId: UUID().uuidString.lowercased(),
                conversationId: conversationId, projectId: artifact.projectId)
            // Into the cache at once, so the main chat shows it before the stream brings it.
            try cache?.upsert(message)
            try cache?.save()
            if draft.trimmingCharacters(in: .whitespacesAndNewlines) == text { draft = "" }
            asking = false
            sent = "Sent to Marcel. His answer comes in Chat."
        } catch {
            // The text stays in the box, so trying again is one tap.
            problem = "Not sent. " + ItemsError.words(for: error)
        }
    }

    private func mainConversation(_ service: any ItemsService) async throws -> String {
        if let conversationId { return conversationId }
        let id = try await service.mainConversationId()
        conversationId = id
        return id
    }
}

/// The Items tab's state besides the cache: the last problem from fetching the list.
@MainActor
@Observable
public final class ItemsModel {
    public var problem: String?
    @ObservationIgnored public var service: (any ItemsService)?
    @ObservationIgnored public var cache: MarcelCache?

    public init(service: (any ItemsService)? = nil, cache: MarcelCache? = nil) {
        self.service = service
        self.cache = cache
    }

    /// Fetches every item into the cache, in case the stream has not brought them (a fresh pairing). Offline, the
    /// cached items stay and nothing is said: the app's status already says the hub is out of reach.
    public func refresh() async {
        guard let service, let cache else { return }
        do {
            for artifact in try await service.artifacts() { try cache.upsert(artifact) }
            try cache.save()
            problem = nil
        } catch ItemsError.offline {
            return
        } catch {
            problem = ItemsError.words(for: error)
        }
    }
}
