import Foundation

/// Unsaved edits, one per note, kept on this phone until they are saved or thrown away. They survive leaving the
/// screen, closing the app and a save that failed (offline, the hub down, changed elsewhere).
public struct Draft: Codable, Equatable, Sendable {
    /// What was typed.
    public var text: String
    /// The etag of the version the edit started from; nil for a note that did not exist yet (or was removed).
    public var base: String?
    public var at: Date

    public init(text: String, base: String?, at: Date) {
        self.text = text
        self.base = base
        self.at = at
    }
}

/// Where drafts are kept: this phone's user defaults, under `notebook.draft.<path>`. Bounded, so drafts never fill the
/// phone: at most `maxDrafts` notes, each at most `maxBytes` (the hub's own limit for a memory file).
public struct DraftStore: @unchecked Sendable {
    static let prefix = "notebook.draft."
    public static let maxDrafts = 50
    public static let maxBytes = 1_000_000
    let defaults: UserDefaults
    let maxDrafts: Int
    let maxBytes: Int

    public init(defaults: UserDefaults = .standard, maxDrafts: Int = DraftStore.maxDrafts,
                maxBytes: Int = DraftStore.maxBytes) {
        self.defaults = defaults
        self.maxDrafts = maxDrafts
        self.maxBytes = maxBytes
    }

    /// What `keep` did.
    public enum Kept: Equatable, Sendable {
        case kept
        /// Over `maxBytes`: not kept (an older draft of the note stays as it was).
        case tooBig
        /// Kept, after throwing away the oldest drafts of other notes to stay at `maxDrafts`.
        case madeRoom(dropped: [MemoryPath])

        /// What the editor says, or nil when there is nothing to say.
        public var words: String? {
            switch self {
            case .kept: nil
            case .tooBig: "This note is over 1 MB: the phone can't keep it as a draft, and the hub won't save it. "
                + "Make it shorter."
            case .madeRoom(let dropped):
                dropped.count == 1
                    ? "The phone keeps at most \(DraftStore.maxDrafts) unsaved notes, so the oldest one "
                        + "(\(dropped[0].raw)) was thrown away."
                    : "The phone keeps at most \(DraftStore.maxDrafts) unsaved notes, so the \(dropped.count) oldest "
                        + "were thrown away."
            }
        }
    }

    public func draft(for path: MemoryPath) -> Draft? {
        defaults.data(forKey: Self.prefix + path.raw).flatMap { try? JSONDecoder().decode(Draft.self, from: $0) }
    }

    @discardableResult
    public func keep(_ draft: Draft, for path: MemoryPath) -> Kept {
        guard draft.text.utf8.count <= maxBytes, let data = try? JSONEncoder().encode(draft) else { return .tooBig }
        var dropped: [MemoryPath] = []
        if self.draft(for: path) == nil {
            let others = paths.compactMap { other in self.draft(for: other).map { (other, $0.at) } }
            if others.count >= maxDrafts {
                dropped = others.sorted { $0.1 < $1.1 }.prefix(others.count - maxDrafts + 1).map(\.0)
                dropped.forEach(discard)
            }
        }
        defaults.set(data, forKey: Self.prefix + path.raw)
        return dropped.isEmpty ? .kept : .madeRoom(dropped: dropped)
    }

    public func discard(_ path: MemoryPath) {
        defaults.removeObject(forKey: Self.prefix + path.raw)
    }

    /// The notes that have a draft.
    public var paths: Set<MemoryPath> {
        Set(defaults.dictionaryRepresentation().keys.filter { $0.hasPrefix(Self.prefix) }
            .compactMap { MemoryPath(String($0.dropFirst(Self.prefix.count))) })
    }

    /// Forgets every draft (when the phone is unpaired).
    public func discardAll() {
        for path in paths { discard(path) }
    }
}
