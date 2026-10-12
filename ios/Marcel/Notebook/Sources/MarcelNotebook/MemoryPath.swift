import Foundation

/// A memory file's path as the hub names it (`MEMORY.md`, `memory/2026-10-04.md`, `memory/projects/energy.md`).
///
/// Paths come from the hub and are **untrusted**: one is used only if it matches the contract's pattern (letters,
/// digits, `.`, `_`, `-` and `/`, ending in `.md`, never `..`, never starting with `/`, no part starting with `.`, no
/// empty part). Anything else is refused before a request is made. On the wire the generated client percent-encodes
/// the whole path as one segment (`memory%2F2026-10-04.md`), so a `/` never becomes a new path segment.
public struct MemoryPath: Hashable, Sendable, Comparable, CustomStringConvertible {
    public let raw: String

    /// nil when `raw` is not a memory path the app will touch.
    public init?(_ raw: String) {
        guard Self.isSafe(raw) else { return nil }
        self.raw = raw
    }

    public var description: String { raw }

    public static func < (lhs: MemoryPath, rhs: MemoryPath) -> Bool { lhs.raw < rhs.raw }

    /// The contract's pattern `^(?!/)(?!.*\.\.)(?!(.*/)?\.)[A-Za-z0-9._/-]+\.md$`, plus no empty part (`a//b.md`).
    public static func isSafe(_ raw: String) -> Bool {
        guard !raw.isEmpty, raw.utf8.count <= 512, raw.hasSuffix(".md"), !raw.hasPrefix("/"), !raw.contains("..")
        else { return false }
        let allowed = Set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._/-")
        guard raw.allSatisfy({ allowed.contains($0) }) else { return false }
        let parts = raw.split(separator: "/", omittingEmptySubsequences: false)
        return parts.allSatisfy { !$0.isEmpty && !$0.hasPrefix(".") }
    }

    /// The path as one percent-encoded segment, as the contract describes it (`memory%2F2026-10-04.md`). The generated
    /// client does this itself; this is what a test expects to see on the wire.
    public var encoded: String {
        let unreserved = CharacterSet(charactersIn: "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
        return raw.addingPercentEncoding(withAllowedCharacters: unreserved) ?? raw
    }

    /// `MEMORY.md`: facts, preferences and commitments about the owner.
    public static let aboutYou = MemoryPath("MEMORY.md")!

    /// `memory/projects/<slug>.md`: the notes every thread in that project starts with.
    public static func projectNotes(slug: String) -> MemoryPath? { MemoryPath("memory/projects/\(slug).md") }

    /// Which part of the notebook this file belongs in.
    public var section: NotebookSection {
        if self == Self.aboutYou { return .aboutYou }
        let parts = raw.split(separator: "/").map(String.init)
        if parts.count == 3, parts[0] == "memory", parts[1] == "projects" {
            return .project(slug: String(parts[2].dropLast(3)))
        }
        if parts.count == 2, parts[0] == "memory", let day = Self.day(String(parts[1].dropLast(3))) {
            return .dated(day)
        }
        return .other
    }

    /// `2026-10-04` as that day at noon UTC (so it shows as the same date in every time zone), or nil.
    static func day(_ text: String) -> Date? {
        guard text.count == 10 else { return nil }
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "UTC")!
        let parts = text.split(separator: "-")
        guard parts.count == 3, parts[0].count == 4, parts[1].count == 2, parts[2].count == 2,
              let year = Int(parts[0]), let month = Int(parts[1]), let day = Int(parts[2]) else { return nil }
        let components = DateComponents(year: year, month: month, day: day, hour: 12)
        guard let date = calendar.date(from: components),
              calendar.component(.month, from: date) == month, calendar.component(.day, from: date) == day
        else { return nil }
        return date
    }
}

/// The notebook's sections (wireframe 6B).
public enum NotebookSection: Hashable, Sendable {
    /// `MEMORY.md`.
    case aboutYou
    /// `memory/projects/<slug>.md`.
    case project(slug: String)
    /// `memory/YYYY-MM-DD.md`.
    case dated(Date)
    /// Any other Markdown file in the memory repo.
    case other
}
