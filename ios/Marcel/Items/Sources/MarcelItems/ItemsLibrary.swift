import Foundation
import MarcelKit
import PixelKit
import SwiftData

/// The cached rows the tab and the viewer are drawn from, decoded. Rows that no longer decode are skipped: one bad
/// row never blanks the tab.
public struct ItemsInput: Sendable {
    public var artifacts: [Artifact]
    public var projects: [Project]

    public init(artifacts: [Artifact], projects: [Project]) {
        self.artifacts = artifacts
        self.projects = projects
    }

    public static func read(artifacts: [CachedArtifact], projects: [CachedProject]) -> ItemsInput {
        let decoder = ItemsJSON.decoder()
        func rows<T: Decodable>(_ payloads: [Data], as _: T.Type) -> [T] {
            payloads.compactMap { try? decoder.decode(T.self, from: $0) }
        }
        return ItemsInput(artifacts: rows(artifacts.map(\.payload), as: Artifact.self),
                          projects: rows(projects.map(\.payload), as: Project.self))
    }

    /// Reads the cache straight from a context (tests, previews): the same rows the screen's `@Query`s hold.
    @MainActor
    public static func read(from context: ModelContext) throws -> ItemsInput {
        read(artifacts: try context.fetch(FetchDescriptor<CachedArtifact>()),
             projects: try context.fetch(FetchDescriptor<CachedProject>()))
    }

    /// One cached item, decoded; nil when the cache does not have it.
    static func artifact(_ rows: [CachedArtifact]) -> Artifact? {
        rows.first.flatMap { try? ItemsJSON.decoder().decode(Artifact.self, from: $0.payload) }
    }
}

/// The hub's JSON dates (RFC 3339, with or without fractions), as the cache stores them.
enum ItemsJSON {
    static func decoder() -> JSONDecoder {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .custom { decoder in
            let container = try decoder.singleValueContainer()
            let text = try container.decode(String.self)
            guard let date = parse(text) else {
                throw DecodingError.dataCorruptedError(
                    in: container, debugDescription: "Not an RFC 3339 date: \(text)")
            }
            return date
        }
        return decoder
    }

    static func parse(_ text: String) -> Date? {
        let withFraction = Date.ISO8601FormatStyle(includingFractionalSeconds: true)
        return (try? withFraction.parse(text)) ?? (try? Date.ISO8601FormatStyle().parse(text))
    }
}

/// The Items tab as data (wireframe 4C): every item, grouped by project, newest change first. Pure, so the tests
/// check exactly what the screen shows.
public struct ItemsLibrary: Equatable, Sendable {
    public var sections: [ItemSection]

    /// - Parameter timeZone: the phone's; the tests pin one so "11:52" does not depend on where they run.
    public init(artifacts: [Artifact], projects: [Project], now: Date, timeZone: TimeZone = .current) {
        let names = Dictionary(projects.map { ($0.id, $0.name) }, uniquingKeysWith: { a, _ in a })
        let groups = Dictionary(grouping: artifacts, by: \.projectId)
        sections = groups.map { projectId, items in
            let sorted = items.sorted(by: Self.newestFirst)
            return ItemSection(
                id: projectId, name: names[projectId] ?? "Other items", tone: ItemsTone.of(projectId),
                newest: sorted.first?.updatedAt ?? .distantPast,
                tiles: sorted.map { ItemTile($0, now: now, timeZone: timeZone) })
        }
        .sorted { lhs, rhs in
            lhs.newest != rhs.newest ? lhs.newest > rhs.newest
                : lhs.name.localizedStandardCompare(rhs.name) == .orderedAscending
        }
    }

    public var isEmpty: Bool { sections.isEmpty }

    static func newestFirst(_ lhs: Artifact, _ rhs: Artifact) -> Bool {
        lhs.updatedAt != rhs.updatedAt ? lhs.updatedAt > rhs.updatedAt : lhs.id < rhs.id
    }
}

/// One project's items in the grid.
public struct ItemSection: Identifiable, Equatable, Sendable {
    public var id: String
    public var name: String
    public var tone: ProjectChip.Tone
    /// When the newest item here last changed; sections are in this order.
    public var newest: Date
    public var tiles: [ItemTile]
}

/// One item's tile: its name, what it is, and when it changed. Living items that changed today wear an "updated"
/// badge.
public struct ItemTile: Identifiable, Equatable, Sendable {
    public var id: String
    public var title: String
    /// "Document · v3", "Pull request #41 · checks passing", "PDF · 1.2 MB".
    public var detail: String
    /// "Updated 11:52", "Updated yesterday".
    public var updated: String
    /// "Updated" on living items (more than one version, or a board) that changed in the last day.
    public var badge: String?
    /// What VoiceOver says for the whole tile.
    public var spoken: String

    init(_ artifact: Artifact, now: Date, timeZone: TimeZone) {
        id = artifact.id
        title = artifact.title
        let what = ItemWords.kind(artifact)
        var detail = [what.short]
        var spoken = [artifact.title, what.spoken]
        switch artifact.kind {
        case .pr:
            if let checks = artifact.pr?.checks, let words = ItemWords.checks(checks) {
                detail.append(words.lowercased())
                spoken.append(words)
            }
        case .file:
            if let size = artifact.sizeBytes {
                detail.append(ItemWords.size(size))
                spoken.append(ItemWords.size(size, spoken: true))
            }
        case .link, .branch:
            if let host = artifact.url.flatMap({ URL(string: $0)?.host() }) {
                detail.append(host)
                spoken.append("On \(host)")
            }
        case .doc, .dashboard, .board:
            if artifact.currentVersion > 1 {
                detail.append("v\(artifact.currentVersion)")
                spoken.append("Version \(artifact.currentVersion)")
            }
        }
        self.detail = detail.joined(separator: " · ")
        let when = Ago(artifact.updatedAt, now: now, timeZone: timeZone)
        updated = "Updated \(when.short)"
        spoken.append("Updated \(when.spoken)")
        let living = artifact.currentVersion > 1 || artifact.kind == .board
        let recent = now.timeIntervalSince(artifact.updatedAt) < 24 * 3600
        badge = living && recent ? "Updated" : nil
        if badge != nil { spoken.append("Recently updated") }
        self.spoken = spoken.joined(separator: ". ") + "."
    }
}

/// A project's colour, from its id: the same hash as the Chat, Quests and Map tabs, so a project keeps its colour.
enum ItemsTone {
    static func of(_ projectId: String) -> ProjectChip.Tone {
        var hash: UInt32 = 2_166_136_261
        for byte in projectId.utf8 {
            hash = (hash ^ UInt32(byte)) &* 16_777_619
        }
        let tones = ProjectChip.Tone.allCases
        return tones[Int(hash % UInt32(tones.count))]
    }
}

/// How the tab and the viewer name things, shown short and spoken in full.
enum ItemWords {
    struct Words: Equatable {
        var short: String
        var spoken: String
    }

    static func kind(_ artifact: Artifact) -> Words {
        switch artifact.kind {
        case .pr:
            let number = artifact.pr.map { " #\($0.number)" } ?? ""
            return Words(short: "Pull request\(number)", spoken: "Pull request\(number.replacingOccurrences(of: "#", with: "number "))")
        case .branch: return Words(short: "Branch", spoken: "Branch")
        case .doc:
            return isHTML(artifact.contentType) ? Words(short: "Web page", spoken: "Web page")
                : Words(short: "Document", spoken: "Document")
        case .file:
            let type = fileType(artifact.contentType)
            return Words(short: type, spoken: type == "PDF" ? "PDF file" : type)
        case .dashboard: return Words(short: "Dashboard", spoken: "Dashboard")
        case .link: return Words(short: "Link", spoken: "Link")
        case .board: return Words(short: "Board", spoken: "Project board")
        }
    }

    static func isHTML(_ contentType: String?) -> Bool {
        let type = contentType?.lowercased() ?? ""
        return type.hasPrefix("text/html") || type.hasPrefix("application/xhtml")
    }

    static func fileType(_ contentType: String?) -> String {
        let type = (contentType ?? "").lowercased().split(separator: ";").first.map(String.init) ?? ""
        switch type {
        case "application/pdf": return "PDF"
        case "text/csv": return "Spreadsheet"
        case "text/plain": return "Text file"
        case "application/zip": return "Zip file"
        default:
            if type.hasPrefix("image/") { return "Picture" }
            if type.hasPrefix("audio/") { return "Sound" }
            if type.hasPrefix("video/") { return "Video" }
            if type.contains("spreadsheet") || type.contains("excel") { return "Spreadsheet" }
            if type.contains("word") || type.contains("document") { return "Document file" }
            return "File"
        }
    }

    static func checks(_ checks: Components.Schemas.Artifact.PrPayload.ChecksPayload) -> String? {
        switch checks {
        case .passing: "Checks passing"
        case .failing: "Checks failing"
        case .pending: "Checks running"
        case .none: nil
        }
    }

    /// "1.2 MB", "312 KB"; spoken "1.2 megabytes".
    static func size(_ bytes: Int, spoken: Bool = false) -> String {
        let units = spoken ? ["bytes", "kilobytes", "megabytes", "gigabytes"] : ["B", "KB", "MB", "GB"]
        var value = Double(bytes)
        var unit = 0
        while value >= 1000, unit < units.count - 1 {
            value /= 1000
            unit += 1
        }
        let number = unit == 0 || value >= 10 ? String(Int(value.rounded())) : String(format: "%.1f", value)
        return "\(number) \(units[unit])"
    }
}

/// How long ago something changed: "just now", "2 min ago", "3 h ago", "yesterday", "on 3 Oct". VoiceOver gets the
/// words in full ("2 minutes ago", "on 3 October").
struct Ago: Equatable {
    var short: String
    var spoken: String

    init(_ date: Date, now: Date, timeZone: TimeZone) {
        let seconds = max(0, now.timeIntervalSince(date))
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = timeZone
        let days = calendar.dateComponents([.day], from: calendar.startOfDay(for: date),
                                           to: calendar.startOfDay(for: now)).day ?? 0
        switch seconds {
        case ..<60:
            short = "just now"
            spoken = "just now"
        case ..<3600:
            let minutes = Int(seconds / 60)
            short = "\(minutes) min ago"
            spoken = minutes == 1 ? "1 minute ago" : "\(minutes) minutes ago"
        case ..<(12 * 3600):
            let hours = Int(seconds / 3600)
            short = "\(hours) h ago"
            spoken = hours == 1 ? "1 hour ago" : "\(hours) hours ago"
        default:
            func format(_ pattern: String) -> String {
                let formatter = DateFormatter()
                formatter.calendar = calendar
                formatter.timeZone = timeZone
                formatter.locale = Locale(identifier: "en_GB")
                formatter.dateFormat = pattern
                return formatter.string(from: date)
            }
            switch days {
            case ...0:
                short = "at \(format("HH:mm"))"
                spoken = short
            case 1:
                short = "yesterday"
                spoken = "yesterday"
            default:
                short = "on \(format("d MMM"))"
                spoken = "on \(format("d MMMM"))"
            }
        }
    }
}
