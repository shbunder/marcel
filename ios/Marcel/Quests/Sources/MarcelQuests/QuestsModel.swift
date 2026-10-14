import Foundation
import MarcelKit
import Observation

/// The quest log's state besides the cache: the filter, whether "Done · last 7 days" is open, what is being sent, and
/// the last problem. Stop and archive go to the hub; the answer is written into the cache straight away, so the card
/// moves at once, and the live stream's `task.updated` confirms it later.
@MainActor
@Observable
public final class QuestsModel {
    public var filter = QuestFilter()
    public var showsFinished = false
    /// Quests with a stop or archive on its way.
    public private(set) var busy: Set<String> = []
    /// The last thing that went wrong, in plain words; cleared by the next action.
    public var problem: String?
    public private(set) var loadingArchived = false

    @ObservationIgnored public var service: (any QuestService)?
    @ObservationIgnored public var cache: MarcelCache?

    public init(service: (any QuestService)? = nil, cache: MarcelCache? = nil) {
        self.service = service
        self.cache = cache
    }

    public func selectProject(_ projectId: String?) {
        filter.projectId = projectId
    }

    /// Picks a state in the filter; "Archived" fetches the archived quests from the hub.
    public func selectStatus(_ status: QuestStatusFilter) async {
        if status == .archived {
            await showArchived(true)
        } else {
            filter.status = status
        }
    }

    /// Shows archived quests (fetched from the hub, since the stream does not carry them) or the live ones.
    public func showArchived(_ archived: Bool) async {
        filter.archived = archived
        guard archived else { return }
        await refreshArchived()
    }

    public func refreshArchived() async {
        guard let service, let cache else { return }
        loadingArchived = true
        defer { loadingArchived = false }
        do {
            for task in try await service.archivedTasks() { try cache.upsert(task) }
            try cache.save()
        } catch {
            problem = Self.words(for: error)
        }
    }

    /// The quest a Stop swipe asked about. Stopping cannot be undone, so a swipe only asks; the quest stops when the
    /// question is confirmed.
    public private(set) var pendingStop: QuestRow?

    /// Swipe → Stop: asks "Stop …?" and sends nothing yet.
    public func askToStop(_ row: QuestRow) {
        guard row.canStop else { return }
        problem = nil
        pendingStop = row
    }

    /// "Stop the quest" in the confirmation.
    public func confirmStop() async {
        guard let row = pendingStop else { return }
        pendingStop = nil
        await stop(row.id)
    }

    /// "Keep it running", or the confirmation dismissed.
    public func cancelStop() {
        pendingStop = nil
    }

    /// Stops a running or queued quest. Called by `confirmStop`, never straight from a swipe.
    public func stop(_ taskId: String) async {
        await send(taskId) { try await $0.stop(taskId: taskId) }
    }

    /// Swipe → Archive: a done, failed or stopped quest leaves the log for "Archived".
    public func archive(_ taskId: String) async {
        await send(taskId) { try await $0.archive(taskId: taskId) }
    }

    private func send(_ taskId: String, _ action: @MainActor (any QuestService) async throws -> HubTask) async {
        problem = nil
        guard let service else {
            problem = QuestActionError.notPaired.errorDescription
            return
        }
        guard busy.insert(taskId).inserted else { return }
        defer { busy.remove(taskId) }
        do {
            let task = try await action(service)
            try cache?.upsert(task)
            try cache?.save()
        } catch {
            problem = Self.words(for: error)
        }
    }

    static func words(for error: any Error) -> String {
        (error as? QuestActionError)?.errorDescription
            ?? "Something went wrong talking to the hub. Try again in a moment."
    }
}
