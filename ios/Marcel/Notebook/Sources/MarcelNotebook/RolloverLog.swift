import Foundation
import MarcelKit
import Observation

/// One `brain.rollover` event (S-06.2): Marcel's main session was replaced by a fresh one.
public struct Rollover: Codable, Equatable, Sendable, Identifiable {
    /// The event id.
    public var id: Int
    public var at: Date
    /// `context_full`, `crashed`, `daily` or `manual`; anything else is shown as "for another reason".
    public var reason: String
    /// What the new session starts with, in the hub's plain words.
    public var kept: String

    public init(id: Int, at: Date, reason: String, kept: String) {
        self.id = id
        self.at = at
        self.reason = reason
        self.kept = kept
    }
}

/// The last rollovers this phone saw, newest first, for "What Marcel carries".
///
/// The app API has no route that lists past rollovers, and the cache does not store them, so the log follows the live
/// stream from the moment the tabs are up (the hub replays what a reconnecting phone missed) and keeps the last ten on
/// this phone. Rollovers from before this phone was listening are not shown.
@MainActor
@Observable
public final class RolloverLog {
    public static let keep = 10
    static let key = "notebook.rollovers"

    public private(set) var rollovers: [Rollover]
    @ObservationIgnored private let defaults: UserDefaults
    @ObservationIgnored private var token: UUID?
    @ObservationIgnored private weak var cache: MarcelCache?

    public init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        rollovers = defaults.data(forKey: Self.key)
            .flatMap { try? JSONDecoder().decode([Rollover].self, from: $0) } ?? []
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
              !rollovers.contains(where: { $0.id == frame.id }) else { return }
        rollovers.append(Rollover(id: frame.id, at: frame.at, reason: reason, kept: kept))
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
