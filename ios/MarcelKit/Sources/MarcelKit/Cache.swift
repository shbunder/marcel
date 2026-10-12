import Foundation
import SwiftData

// The local cache: what the app shows comes from here, so it opens instantly and works offline.
// Each row keeps the hub's JSON for the record plus the fields the app sorts and filters on.
// Projects join this cache when the contract has them (contract PR #10).

@Model
public final class CachedMessage {
    @Attribute(.unique) public var id: String
    public var conversationId: String
    public var taskId: String?
    public var createdAt: Date
    public var payload: Data

    init(id: String, conversationId: String, taskId: String?, createdAt: Date, payload: Data) {
        self.id = id
        self.conversationId = conversationId
        self.taskId = taskId
        self.createdAt = createdAt
        self.payload = payload
    }
}

@Model
public final class CachedTask {
    @Attribute(.unique) public var id: String
    public var state: String
    public var updatedAt: Date
    public var payload: Data

    init(id: String, state: String, updatedAt: Date, payload: Data) {
        self.id = id
        self.state = state
        self.updatedAt = updatedAt
        self.payload = payload
    }
}

@Model
public final class CachedArtifact {
    @Attribute(.unique) public var id: String
    public var taskId: String?
    public var createdAt: Date
    public var payload: Data

    init(id: String, taskId: String?, createdAt: Date, payload: Data) {
        self.id = id
        self.taskId = taskId
        self.createdAt = createdAt
        self.payload = payload
    }
}

/// Where the event stream got to, so a cold start resumes with `since` instead of starting over.
@Model
public final class SyncState {
    @Attribute(.unique) public var key: String
    public var lastEventId: Int?

    init(key: String, lastEventId: Int?) {
        self.key = key
        self.lastEventId = lastEventId
    }
}

/// Re-fetches what the cache holds over REST, for `resync.required`.
@MainActor
public protocol CacheRefresher {
    func refresh(into cache: MarcelCache) async throws
}

@MainActor
public final class MarcelCache: EventSink {
    public static let schema = Schema([CachedMessage.self, CachedTask.self, CachedArtifact.self, SyncState.self])

    public let container: ModelContainer
    public var refresher: (any CacheRefresher)?
    private var context: ModelContext { container.mainContext }
    private let encoder = HubDates.encoder()
    private let decoder = HubDates.decoder()

    public init(container: ModelContainer, refresher: (any CacheRefresher)? = nil) {
        self.container = container
        self.refresher = refresher
    }

    public static func makeContainer(inMemory: Bool = false) throws -> ModelContainer {
        try ModelContainer(for: schema, configurations: ModelConfiguration(isStoredInMemoryOnly: inMemory))
    }

    // MARK: EventSink

    public var lastEventId: Int? { syncState()?.lastEventId }

    public func apply(_ frame: EventFrame) throws {
        switch frame.event {
        case .messageCreated(let message): try upsert(message)
        case .taskCreated(let task), .taskUpdated(let task): try upsert(task)
        case .artifactCreated(let artifact): try upsert(artifact)
        default: break  // other events drive live UI only (avatar state, approvals, usage, transcripts)
        }
        setLastEventId(frame.id)
        try context.save()
    }

    public func resync(upTo lastEventId: Int) async throws {
        try await refresher?.refresh(into: self)
        setLastEventId(lastEventId)
        try context.save()
    }

    // MARK: Writes

    public func upsert(_ message: Message) throws {
        let payload = try encoder.encode(message)
        let id = message.id
        if let row = try context.fetch(FetchDescriptor<CachedMessage>(predicate: #Predicate { $0.id == id })).first {
            row.conversationId = message.conversationId
            row.taskId = message.taskId
            row.createdAt = message.createdAt
            row.payload = payload
        } else {
            context.insert(
                CachedMessage(
                    id: id, conversationId: message.conversationId, taskId: message.taskId,
                    createdAt: message.createdAt, payload: payload))
        }
    }

    public func upsert(_ task: HubTask) throws {
        let payload = try encoder.encode(task)
        let id = task.id
        if let row = try context.fetch(FetchDescriptor<CachedTask>(predicate: #Predicate { $0.id == id })).first {
            row.state = task.state.rawValue
            row.updatedAt = task.updatedAt
            row.payload = payload
        } else {
            context.insert(CachedTask(id: id, state: task.state.rawValue, updatedAt: task.updatedAt, payload: payload))
        }
    }

    public func upsert(_ artifact: Artifact) throws {
        let payload = try encoder.encode(artifact)
        let id = artifact.id
        if let row = try context.fetch(FetchDescriptor<CachedArtifact>(predicate: #Predicate { $0.id == id })).first {
            row.taskId = artifact.taskId
            row.createdAt = artifact.createdAt
            row.payload = payload
        } else {
            context.insert(
                CachedArtifact(id: id, taskId: artifact.taskId, createdAt: artifact.createdAt, payload: payload))
        }
    }

    public func save() throws {
        try context.save()
    }

    /// Empties the cache, for unpairing.
    public func clear() throws {
        try context.delete(model: CachedMessage.self)
        try context.delete(model: CachedTask.self)
        try context.delete(model: CachedArtifact.self)
        try context.delete(model: SyncState.self)
        try context.save()
    }

    // MARK: Reads

    /// A conversation's messages, oldest first.
    public func messages(in conversationId: String) throws -> [Message] {
        let rows = try context.fetch(
            FetchDescriptor<CachedMessage>(
                predicate: #Predicate { $0.conversationId == conversationId },
                sortBy: [SortDescriptor(\.createdAt)]))
        return try rows.map { try decoder.decode(Message.self, from: $0.payload) }
    }

    /// Tasks, most recently changed first.
    public func tasks() throws -> [HubTask] {
        let rows = try context.fetch(FetchDescriptor<CachedTask>(sortBy: [SortDescriptor(\.updatedAt, order: .reverse)]))
        return try rows.map { try decoder.decode(HubTask.self, from: $0.payload) }
    }

    /// Artifacts, newest first.
    public func artifacts() throws -> [Artifact] {
        let rows = try context.fetch(
            FetchDescriptor<CachedArtifact>(sortBy: [SortDescriptor(\.createdAt, order: .reverse)]))
        return try rows.map { try decoder.decode(Artifact.self, from: $0.payload) }
    }

    // MARK: Sync state

    private static let syncKey = "events"

    private func syncState() -> SyncState? {
        let key = Self.syncKey
        return try? context.fetch(FetchDescriptor<SyncState>(predicate: #Predicate { $0.key == key })).first
    }

    private func setLastEventId(_ id: Int) {
        if let state = syncState() {
            state.lastEventId = id
        } else {
            context.insert(SyncState(key: Self.syncKey, lastEventId: id))
        }
    }
}
