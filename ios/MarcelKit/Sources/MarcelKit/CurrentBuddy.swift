import Foundation
import Observation

/// The buddy this phone shows (B-49, ADR-013): the one place every tab reads its agent id from. The hub keeps it as
/// the phone's `Device.agent_id`; the app keeps a copy here so a tab knows it at once, offline too, and follows
/// every switch: a pick in the chat's buddy sheet, a `device.updated` from the hub (another phone switched this one,
/// or its buddy was archived), a pairing link's `&buddy=`, or a notification about another buddy.
///
/// It also remembers the buddy shown before, so a long-press on the chat's avatar switches straight back.
@MainActor
@Observable
public final class CurrentBuddy {
    /// The app's one instance.
    public static let shared = CurrentBuddy()

    static let key = "marcel.currentBuddy"
    static let previousKey = "marcel.previousBuddy"

    /// The buddy shown; nil until the hub (or a pairing link) has named one.
    public private(set) var agentId: String?
    /// The buddy shown before the last switch, if any.
    public private(set) var previousId: String?

    @ObservationIgnored private let defaults: UserDefaults

    public init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        agentId = defaults.string(forKey: Self.key)
        previousId = defaults.string(forKey: Self.previousKey)
    }

    /// Shows `id`; the buddy shown until now becomes the previous one.
    public func show(_ id: String) {
        guard id != agentId else { return }
        if let agentId { previousId = agentId }
        agentId = id
        defaults.set(id, forKey: Self.key)
        defaults.set(previousId, forKey: Self.previousKey)
    }

    /// The hub's word on a phone (`GET /devices`, `PATCH /devices/{id}`, `device.updated`): this phone's buddy
    /// follows it; another phone's change is not ours.
    public func apply(deviceId: String, agentId: String, thisDevice: String?) {
        guard deviceId == thisDevice else { return }
        show(agentId)
    }

    /// The buddy to show among `agents` (`GET /me`'s live buddies): the current one, else the first.
    public func pick(from agents: [Agent]) -> Agent? {
        Self.pick(from: agents, id: agentId)
    }

    public nonisolated static func pick(from agents: [Agent], id: String?) -> Agent? {
        agents.first { $0.id == id } ?? agents.first
    }

    /// For a tab's service, from any thread: the buddy to use among `GET /me`'s agents.
    public nonisolated static func chosen(from agents: [Agent]) async -> Agent? {
        pick(from: agents, id: await shared.agentId)
    }

    /// Forgets both (signing out).
    public func forget() {
        agentId = nil
        previousId = nil
        defaults.removeObject(forKey: Self.key)
        defaults.removeObject(forKey: Self.previousKey)
    }
}

/// What a tab shows of the cache: only the buddy shown (its projects, and the quests and items in them).
public struct BuddyRows {
    public var projects: [CachedProject]
    public var tasks: [CachedTask]
    public var artifacts: [CachedArtifact]
}

extension CurrentBuddy {
    /// The cached rows that belong to the buddy shown. Until a buddy is known (a first launch offline), all of them.
    public func rows(projects: [CachedProject], tasks: [CachedTask], artifacts: [CachedArtifact]) -> BuddyRows {
        guard let agentId else { return BuddyRows(projects: projects, tasks: tasks, artifacts: artifacts) }
        let mine = projects.filter { $0.agentId == agentId }
        let ids = Set(mine.map(\.id))
        return BuddyRows(projects: mine, tasks: tasks.filter { ids.contains($0.projectId) },
                         artifacts: artifacts.filter { ids.contains($0.projectId) })
    }
}
