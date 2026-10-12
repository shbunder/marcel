import Foundation
import MarcelChat
import MarcelKit
import Observation

/// The board's rows as the room shows them.
public enum BoardState: Equatable, Sendable {
    case loading
    case rows([PreviewRow])
    case failed(String)
}

/// The project's notes as the room shows them (read-only here; editing them is the notebook's job).
public enum NotesState: Equatable, Sendable {
    case loading
    case text(String)
    /// The project has no notes file yet.
    case empty
    case failed(String)
}

/// The map's and the rooms' state besides the cache: board rows and notes fetched for a room, what you are typing in
/// each room, what is on its way, and the last problem. A move or a message goes to the hub; its answer is written
/// into the cache straight away, so both rooms (and the main chat) change at once, and the live stream confirms it.
@MainActor
@Observable
public final class MapModel {
    /// The last thing that went wrong, in plain words; cleared by the next action.
    public var problem: String?
    /// What you are typing, per project room.
    public var drafts: [String: String] = [:]
    /// "Sent to Marcel…" after a room's message got through, per project.
    public private(set) var sent: [String: String] = [:]
    /// Rooms with a message on its way.
    public private(set) var sending: Set<String> = []
    /// Quests with a move on its way.
    public private(set) var moving: Set<String> = []
    /// Board rows by `BoardCard.key` (id and version).
    public private(set) var boards: [String: BoardState] = [:]
    /// Notes by project slug.
    public private(set) var notes: [String: NotesState] = [:]

    @ObservationIgnored public var service: (any ProjectService)?
    @ObservationIgnored public var cache: MarcelCache?
    /// The main conversation, once the hub has said which it is (or remembered from the chat).
    @ObservationIgnored public var conversationId: String?

    public init(service: (any ProjectService)? = nil, cache: MarcelCache? = nil, conversationId: String? = nil) {
        self.service = service
        self.cache = cache
        self.conversationId = conversationId
    }

    // MARK: The map

    /// Fetches the live projects into the cache, in case the stream has not brought them (a fresh pairing).
    /// Offline, the cached map stays and says nothing: the tab bar's status already says the hub is out of reach.
    public func refreshProjects() async {
        guard let service, let cache else { return }
        do {
            for project in try await service.projects() { try cache.upsert(project) }
            try cache.save()
        } catch MapError.offline {
            return
        } catch {
            problem = MapError.words(for: error)
        }
    }

    // MARK: A room

    /// Opening a room: fetch its items (and so its board's version), then the board's rows and the notes.
    public func open(_ room: ProjectRoom) async {
        guard let service else { return }
        if let cache, let items = try? await service.artifacts(projectId: room.id) {
            for item in items { try? cache.upsert(item) }
            try? cache.save()
        }
        await loadNotes(slug: room.slug)
    }

    /// Fetches the board's rows once per version.
    public func loadBoard(_ board: BoardCard) async {
        guard let service else { return }
        switch boards[board.key] {
        case .loading?, .rows?: return
        case .failed?, nil: break
        }
        boards[board.key] = .loading
        do {
            let data = try await service.artifactContent(id: board.artifactId)
            boards[board.key] = .rows(ArtifactPreview.dashboardRows(data, limit: BoardRows.limit)
                ?? [PreviewRow("Nothing on the board yet.")])
        } catch {
            boards[board.key] = .failed(Self.boardProblem(error))
        }
    }

    static func boardProblem(_ error: any Error) -> String {
        (error as? MapError) == .offline ? "Couldn't load the board. It shows once the hub is back."
            : "Couldn't load the board."
    }

    public func loadNotes(slug: String) async {
        guard let service else { return }
        if notes[slug] == nil { notes[slug] = .loading }
        do {
            notes[slug] = try await service.notes(slug: slug).map(NotesState.text) ?? .empty
        } catch {
            // Keep notes already shown; say so only when there is nothing to show.
            if case .text = notes[slug] { return }
            notes[slug] = .failed((error as? MapError) == .offline
                ? "Couldn't load the notes. They show once the hub is back." : "Couldn't load the notes.")
        }
    }

    /// The room's composer: the message goes to the main conversation, tagged with this project (B-32). It never
    /// waits on Marcel; his answer arrives in Chat.
    public func send(projectId: String, projectName: String) async {
        let text = (drafts[projectId] ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }
        problem = nil
        sent[projectId] = nil
        guard let service else {
            problem = MapError.notPaired.errorDescription
            return
        }
        guard sending.insert(projectId).inserted else { return }
        defer { sending.remove(projectId) }
        do {
            let conversationId = try await mainConversation(service)
            let message = try await service.send(text: text, clientId: UUID().uuidString.lowercased(),
                                                 conversationId: conversationId, projectId: projectId)
            // Into the cache at once, so the main chat shows it before the stream brings it.
            try cache?.upsert(message)
            try cache?.save()
            if drafts[projectId]?.trimmingCharacters(in: .whitespacesAndNewlines) == text { drafts[projectId] = "" }
            sent[projectId] = "Sent to Marcel about \(projectName). His answer comes in Chat."
        } catch {
            // The text stays in the box, so trying again is one tap.
            problem = "Not sent. " + MapError.words(for: error)
        }
    }

    private func mainConversation(_ service: any ProjectService) async throws -> String {
        if let conversationId { return conversationId }
        let id = try await service.mainConversationId()
        conversationId = id
        return id
    }

    /// "Move to …" on a quest's menu (B-31). The hub's answer goes into the cache, so the quest leaves this room and
    /// shows in the other at once; `task.moved` and both projects' `project.updated` follow on the stream.
    public func move(taskId: String, to projectId: String) async {
        problem = nil
        guard let service else {
            problem = MapError.notPaired.errorDescription
            return
        }
        guard moving.insert(taskId).inserted else { return }
        defer { moving.remove(taskId) }
        do {
            let task = try await service.move(taskId: taskId, to: projectId)
            try cache?.upsert(task)
            try cache?.save()
        } catch {
            problem = MapError.words(for: error)
        }
    }
}

enum BoardRows {
    /// The pinned board shows more of itself than a chat preview does.
    static let limit = 8
}
