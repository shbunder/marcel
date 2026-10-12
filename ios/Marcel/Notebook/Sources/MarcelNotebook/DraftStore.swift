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

/// Where drafts are kept: this phone's user defaults, under `notebook.draft.<path>`.
public struct DraftStore: @unchecked Sendable {
    static let prefix = "notebook.draft."
    let defaults: UserDefaults

    public init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
    }

    public func draft(for path: MemoryPath) -> Draft? {
        defaults.data(forKey: Self.prefix + path.raw).flatMap { try? JSONDecoder().decode(Draft.self, from: $0) }
    }

    public func keep(_ draft: Draft, for path: MemoryPath) {
        guard let data = try? JSONEncoder().encode(draft) else { return }
        defaults.set(data, forKey: Self.prefix + path.raw)
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
