import Foundation
import MarcelKit
import SwiftData

/// Everything the feed is drawn from, decoded from the cache's rows. The live screen fills it from `@Query`
/// results; the tests fill it straight from a cache, through the same `decode` call.
public struct FeedInput: Sendable {
    /// The window of the conversation on screen, oldest first.
    public var messages: [Message]
    public var tasks: [String: HubTask]
    public var artifacts: [String: Artifact]
    public var projects: [String: Project]
    /// True when the cache may hold older messages than `messages` (the window is full).
    public var hasEarlier: Bool

    public init(
        messages: [Message] = [], tasks: [HubTask] = [], artifacts: [Artifact] = [], projects: [Project] = [],
        hasEarlier: Bool = false
    ) {
        self.messages = messages
        self.tasks = Dictionary(tasks.map { ($0.id, $0) }, uniquingKeysWith: { a, _ in a })
        self.artifacts = Dictionary(artifacts.map { ($0.id, $0) }, uniquingKeysWith: { a, _ in a })
        self.projects = Dictionary(projects.map { ($0.id, $0) }, uniquingKeysWith: { a, _ in a })
        self.hasEarlier = hasEarlier
    }

    /// Decodes cached rows. `newestMessages` comes newest first (the order the window is fetched in) and is
    /// turned around here. Rows that no longer decode are skipped: one bad row never blanks the chat.
    public static func decode(
        newestMessages: [CachedMessage], limit: Int, tasks: [CachedTask], artifacts: [CachedArtifact],
        projects: [CachedProject]
    ) -> FeedInput {
        let decoder = ChatJSON.decoder()
        func rows<T: Decodable>(_ payloads: [Data], as _: T.Type) -> [T] {
            payloads.compactMap { try? decoder.decode(T.self, from: $0) }
        }
        return FeedInput(
            messages: rows(newestMessages.reversed().map(\.payload), as: Message.self),
            tasks: rows(tasks.map(\.payload), as: HubTask.self),
            artifacts: rows(artifacts.map(\.payload), as: Artifact.self),
            projects: rows(projects.map(\.payload), as: Project.self),
            hasEarlier: newestMessages.count >= limit
        )
    }

    /// The fetch for the newest `limit` messages of a conversation: what the screen's `@Query` runs.
    public static func messageWindow(conversationId: String, limit: Int) -> FetchDescriptor<CachedMessage> {
        var fetch = FetchDescriptor<CachedMessage>(
            predicate: #Predicate { $0.conversationId == conversationId },
            sortBy: [SortDescriptor(\.createdAt, order: .reverse), SortDescriptor(\.id, order: .reverse)])
        fetch.fetchLimit = limit
        return fetch
    }

    /// Reads the same window straight from a context (tests, previews).
    @MainActor
    public static func read(from context: ModelContext, conversationId: String, limit: Int) throws -> FeedInput {
        decode(
            newestMessages: try context.fetch(messageWindow(conversationId: conversationId, limit: limit)),
            limit: limit,
            tasks: try context.fetch(FetchDescriptor<CachedTask>()),
            artifacts: try context.fetch(FetchDescriptor<CachedArtifact>()),
            projects: try context.fetch(FetchDescriptor<CachedProject>()))
    }
}

/// The hub's JSON dates (RFC 3339, with or without fractions), as the cache stores them.
enum ChatJSON {
    static func decoder() -> JSONDecoder {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .custom { decoder in
            let container = try decoder.singleValueContainer()
            let text = try container.decode(String.self)
            let withFraction = Date.ISO8601FormatStyle(includingFractionalSeconds: true)
            guard let date = (try? withFraction.parse(text)) ?? (try? Date.ISO8601FormatStyle().parse(text)) else {
                throw DecodingError.dataCorruptedError(
                    in: container, debugDescription: "Not an RFC 3339 date: \(text)")
            }
            return date
        }
        return decoder
    }
}
