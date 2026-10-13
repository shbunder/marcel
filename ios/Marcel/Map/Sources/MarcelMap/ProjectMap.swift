import Foundation
import MarcelKit
import MarcelQuests
import PixelKit

/// One tile on the map: everything it shows and says, worked out from the project, its items and its board.
public struct ProjectTile: Identifiable, Hashable, Sendable {
    public var id: String
    public var name: String
    public var tone: ProjectChip.Tone
    /// "1 needs you" on the gold badge; nil when nothing waits on you.
    public var needsYou: String?
    /// "1 working · 4 items · PR ready", or "quiet · 2 items".
    public var detail: String
    /// "Board updated 11:52".
    public var updated: String
    /// The linked Claude Code Project (B-33a), shown as a chip.
    public var claudeProject: URL?
    /// The coding project's tracker (B-46): "Open tracker" shows only when there is one the app may open.
    public var tracker: URL? = nil
    /// What VoiceOver says for the whole tile, in full words.
    public var spoken: String
}

/// The map as data (wireframe 5A): one tile per live project, in the hub's order. Pure, so the tests check exactly
/// what the screen shows.
public struct ProjectMap: Equatable, Sendable {
    public var tiles: [ProjectTile]

    /// - Parameter timeZone: the phone's; the tests pin one so "11:52" does not depend on where they run.
    public init(projects: [Project], artifacts: [Artifact] = [], now: Date, timeZone: TimeZone = .current) {
        let artifactsByProject = Dictionary(grouping: artifacts, by: \.projectId)
        let boards = Dictionary(artifacts.filter { $0.kind == .board }.map { ($0.id, $0) },
                                uniquingKeysWith: { a, _ in a })
        tiles = Self.ordered(projects.filter { !$0.archived }).map { project in
            Self.tile(project, items: artifactsByProject[project.id] ?? [], board: boards[project.boardArtifactId],
                      now: now, timeZone: timeZone)
        }
    }

    public var isEmpty: Bool { tiles.isEmpty }

    /// What the "+ new" tile puts in the chat's composer: Marcel creates projects, so a new one is a request.
    public static let newProjectRequest = "Start a new project: "

    // MARK: Rules

    /// The hub's order (`GET /projects`): the most recent activity first, the inbox last. Equal times sort by name.
    static func ordered(_ projects: [Project]) -> [Project] {
        projects.sorted { lhs, rhs in
            let l = isInbox(lhs), r = isInbox(rhs)
            if l != r { return r }
            if lhs.updatedAt != rhs.updatedAt { return lhs.updatedAt > rhs.updatedAt }
            return lhs.name.localizedStandardCompare(rhs.name) == .orderedAscending
        }
    }

    static func isInbox(_ project: Project) -> Bool {
        project.slug == "inbox"
    }

    static func tile(_ project: Project, items: [Artifact], board: Artifact?, now: Date, timeZone: TimeZone)
        -> ProjectTile
    {
        let counts = project.counts
        let things = items.filter { $0.kind != .board }
        let prReady = things.contains { $0.kind == .pr && ($0.pr?.state == .open || $0.pr?.state == .draft) }

        var shown: [String] = []
        var spoken: [String] = [project.name]
        if counts.needsYou > 0 {
            spoken.append(counts.needsYou == 1 ? "1 thing needs you" : "\(counts.needsYou) things need you")
        }
        if counts.working > 0 {
            shown.append("\(counts.working) working")
            spoken.append("\(counts.working) working")
        } else if counts.needsYou == 0 {
            shown.append("quiet")
            spoken.append("Nothing running")
        }
        if counts.done7d > 0 {
            shown.append("\(counts.done7d) done")
            spoken.append("\(counts.done7d) done this week")
        }
        if !things.isEmpty {
            let items = things.count == 1 ? "1 item" : "\(things.count) items"
            shown.append(items)
            spoken.append(items)
        }
        if prReady {
            shown.append("PR ready")
            spoken.append("A pull request is ready for review")
        }
        // The board changes with everything in the project; without it cached, the project's own time says the same.
        let when = When(board?.updatedAt ?? project.updatedAt, now: now, timeZone: timeZone)
        let what = board == nil ? "Updated" : "Board updated"
        spoken.append("\(what) \(when.spoken)")
        let claude = project.claudeProjectUrl.flatMap(URL.init(string:))
        if claude != nil { spoken.append("Linked to a Claude Code project") }
        let tracker = TrackerButton.url(project.trackerUrl)

        return ProjectTile(
            id: project.id, name: project.name, tone: tone(for: project.id),
            needsYou: counts.needsYou > 0 ? "\(counts.needsYou) needs you" : nil,
            detail: shown.joined(separator: " · "), updated: "\(what) \(when.short)", claudeProject: claude,
            tracker: tracker, spoken: spoken.joined(separator: ". ") + ".")
    }

    /// A project's colour, from its id: the same hash as the Chat and Quests tabs, so a project keeps its colour.
    public static func tone(for projectId: String) -> ProjectChip.Tone {
        var hash: UInt32 = 2_166_136_261
        for byte in projectId.utf8 {
            hash = (hash ^ UInt32(byte)) &* 16_777_619
        }
        let tones = ProjectChip.Tone.allCases
        return tones[Int(hash % UInt32(tones.count))]
    }
}

/// When something happened, as a tile says it: "11:52" today, "yesterday", "Mon" this week, else "3 Oct". VoiceOver
/// gets "at 11:52", "yesterday", "on Monday", "on 3 October".
struct When: Equatable {
    var short: String
    var spoken: String

    init(_ date: Date, now: Date, timeZone: TimeZone) {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = timeZone
        calendar.locale = Locale(identifier: "en_GB")
        func format(_ pattern: String) -> String {
            let formatter = DateFormatter()
            formatter.calendar = calendar
            formatter.timeZone = timeZone
            formatter.locale = calendar.locale
            formatter.dateFormat = pattern
            return formatter.string(from: date)
        }
        let days = calendar.dateComponents([.day], from: calendar.startOfDay(for: date),
                                           to: calendar.startOfDay(for: now)).day ?? 0
        switch days {
        case ...0:
            short = format("HH:mm")
            spoken = "at \(short)"
        case 1:
            short = "yesterday"
            spoken = "yesterday"
        case 2...6:
            short = format("EEE")
            spoken = "on \(format("EEEE"))"
        default:
            short = format("d MMM")
            spoken = "on \(format("d MMMM"))"
        }
    }
}
