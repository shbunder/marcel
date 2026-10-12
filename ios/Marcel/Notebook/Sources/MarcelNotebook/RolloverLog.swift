import Foundation
import MarcelKit
import Observation

/// One past rollover (S-06.2): Marcel's main session was replaced by a fresh one. From the hub's list
/// (`GET /agents/{id}/brain/rollovers`, app API 0.6) or from a `brain.rollover` event this phone saw.
public struct Rollover: Codable, Equatable, Sendable, Identifiable {
    /// The hub's rollover id (`rol_…`), or `event-<event id>` for one this phone saw on the live stream.
    public var id: String
    public var at: Date
    /// `context_full`, `crashed`, `daily` or `manual`; anything else is shown as "for another reason".
    public var reason: String
    /// What the new session starts with, in the hub's plain words.
    public var kept: String

    public init(id: String, at: Date, reason: String, kept: String) {
        self.id = id
        self.at = at
        self.reason = reason
        self.kept = kept
    }

    /// A `brain.rollover` event's facts.
    public init(eventId: Int, at: Date, reason: String, kept: String) {
        self.init(id: "event-\(eventId)", at: at, reason: reason, kept: kept)
    }

    /// The hub's `Rollover`.
    public init(_ hub: Components.Schemas.Rollover) {
        self.init(id: hub.id, at: hub.at, reason: hub.reason.rawValue, kept: hub.kept)
    }

    private enum CodingKeys: String, CodingKey { case id, at, reason, kept }

    /// Also reads the log an older build kept, whose ids were the event's number.
    public init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        if let number = try? container.decode(Int.self, forKey: .id) {
            id = "event-\(number)"
        } else {
            id = try container.decode(String.self, forKey: .id)
        }
        at = try container.decode(Date.self, forKey: .at)
        reason = try container.decode(String.self, forKey: .reason)
        kept = try container.decode(String.self, forKey: .kept)
    }

    /// The same rollover told twice (the hub's list and the event this phone saw): same reason, within a minute.
    public func isSame(as other: Rollover) -> Bool {
        id == other.id || (reason == other.reason && abs(at.timeIntervalSince(other.at)) < 60)
    }

    /// The hub's list first, then what only this phone saw, without telling one twice; newest first, at most `limit`.
    public static func merged(hub: [Rollover], phone: [Rollover], limit: Int = 10) -> [Rollover] {
        var all = hub
        for rollover in phone where !all.contains(where: { $0.isSame(as: rollover) }) { all.append(rollover) }
        all.sort { $0.at == $1.at ? $0.id > $1.id : $0.at > $1.at }
        return Array(all.prefix(limit))
    }
}

/// The last rollovers this phone saw, newest first, for "What Marcel carries".
///
/// The notebook reads the hub's list (`GET /agents/{id}/brain/rollovers`, app API 0.6) and merges this log into it, so a
/// rollover that happened while the screen is open shows at once, and a hub that does not serve the list yet (before
/// S-06.7) still shows what this phone saw. The log follows the live stream from the moment the tabs are up (the hub
/// replays what a reconnecting phone missed) and keeps the last ten on this phone.
@MainActor
@Observable
public final class RolloverLog {
    public nonisolated static let keep = 10
    static let key = "notebook.rollovers"

    public private(set) var rollovers: [Rollover]
    @ObservationIgnored private let defaults: UserDefaults
    @ObservationIgnored private var token: UUID?
    @ObservationIgnored private weak var cache: MarcelCache?
    @ObservationIgnored private var unpairToken: UUID?
    @ObservationIgnored private weak var connection: HubConnection?

    public init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        rollovers = defaults.data(forKey: Self.key)
            .flatMap { try? JSONDecoder().decode([Rollover].self, from: $0) } ?? []
    }

    /// The app's wiring: follows the connection's cache, and when the phone is unpaired (by the person or because the
    /// hub refused it) forgets this log and every notebook draft, which belong to that hub.
    public func attach(to connection: HubConnection, drafts: DraftStore = DraftStore()) {
        attach(to: connection.cache)
        if let unpairToken { self.connection?.removeOnUnpair(unpairToken) }
        self.connection = connection
        unpairToken = connection.onUnpair { [weak self] in
            self?.clear()
            drafts.discardAll()
        }
    }

    /// Follows `cache`'s frames. Attaching again (or to another cache) replaces the earlier one.
    public func attach(to cache: MarcelCache) {
        detach()
        self.cache = cache
        token = cache.observeFrames { [weak self] frame in self?.receive(frame) }
    }

    public func detach() {
        if let token { cache?.stopObservingFrames(token) }
        token = nil
        cache = nil
    }

    /// Adds a `brain.rollover` frame; every other frame is ignored.
    public func receive(_ frame: EventFrame) {
        guard case .brainRollover(_, let reason, let kept) = frame.event,
              !rollovers.contains(where: { $0.id == "event-\(frame.id)" }) else { return }
        rollovers.append(Rollover(eventId: frame.id, at: frame.at, reason: reason, kept: kept))
        rollovers.sort { $0.at == $1.at ? $0.id > $1.id : $0.at > $1.at }
        rollovers = Array(rollovers.prefix(Self.keep))
        if let data = try? JSONEncoder().encode(rollovers) { defaults.set(data, forKey: Self.key) }
    }

    /// Forgets them (when the phone is unpaired).
    public func clear() {
        rollovers = []
        defaults.removeObject(forKey: Self.key)
    }
}
