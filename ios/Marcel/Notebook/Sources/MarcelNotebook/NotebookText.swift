import Foundation
import PixelKit

/// The words the notebook shows, worked out from the hub's data. Pure, so tests read them.
public struct NotebookText: Sendable {
    public var now: Date
    public var timeZone: TimeZone

    public init(now: Date = .now, timeZone: TimeZone = .current) {
        self.now = now
        self.timeZone = timeZone
    }

    private var calendar: Calendar {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = timeZone
        return calendar
    }

    private var english: Locale { Locale(identifier: "en_GB") }

    /// "21:00", "yesterday 23:00", "Mon 5 Oct, 09:00".
    public func when(_ date: Date) -> String {
        let time = date.formatted(Date.FormatStyle(timeZone: timeZone).hour(.twoDigits(amPM: .omitted))
            .minute(.twoDigits).locale(english))
        if calendar.isDate(date, inSameDayAs: now) { return "today \(time)" }
        if let yesterday = calendar.date(byAdding: .day, value: -1, to: now),
           calendar.isDate(date, inSameDayAs: yesterday) {
            return "yesterday \(time)"
        }
        let day = date.formatted(Date.FormatStyle(timeZone: timeZone).weekday(.abbreviated).day().month(.abbreviated)
            .locale(english))
        return "\(day), \(time)"
    }

    /// A dated note's title: "Sun 4 Oct 2026". The date is the file's name, so it is read in UTC.
    public func day(_ date: Date) -> String {
        date.formatted(Date.FormatStyle(timeZone: TimeZone(identifier: "UTC")!).weekday(.abbreviated).day()
            .month(.abbreviated).year().locale(english))
    }

    /// A note's title on its own screen and in the list.
    public func title(_ path: MemoryPath, projectNames: [String: String]) -> String {
        switch path.section {
        case .aboutYou: "About you"
        case .project(let slug): projectNames[slug] ?? slug
        case .dated(let date): day(date)
        case .other: path.raw
        }
    }

    /// "Updated yesterday 18:12 by Marcel".
    public func updated(_ file: MemoryFile) -> String {
        let by = switch file.updatedBy {
        case .agent: " by Marcel"
        case .app: " in the app"
        case nil: ""
        }
        return "Updated \(when(file.updatedAt))\(by)"
    }

    /// The first lines of a note, for the "About you" card: "· Prefers mornings for appointments".
    public func preview(_ markdown: String, lines: Int = 5) -> [String] {
        let parts = ItemMarkdown.parse(markdown).filter {
            if case .heading = $0.block { false } else if case .rule = $0.block { false } else { true }
        }
        return ItemMarkdown.plainText(parts).split(separator: "\n").prefix(lines).map { "· \($0)" }
    }

    // MARK: What Marcel carries

    /// Why a rollover happened, in plain words.
    public func reason(_ code: String) -> String {
        switch code {
        case "context_full": "its working memory was full"
        case "crashed": "the old session had stopped"
        case "daily": "the daily fresh start"
        case "manual": "you asked for it"
        default: "another reason"
        }
    }

    /// "Context rolled over yesterday 04:00 (the daily fresh start). Kept: … Your chat history is unchanged."
    public func rollover(_ rollover: Rollover) -> String {
        let kept = rollover.kept.trimmingCharacters(in: .whitespacesAndNewlines)
        return "Context rolled over \(when(rollover.at)) (\(reason(rollover.reason))). "
            + (kept.isEmpty ? "" : "Kept: \(kept) ") + "Your chat history is unchanged."
    }

    public static let noRollovers = "No fresh start seen yet. When Marcel starts a fresh session, it says here what it "
        + "kept. Your chat history never changes."

    public static let freshStartConfirm = "Marcel starts a new session with its notes, recent messages and open "
        + "threads, and says hello in the chat when it's ready. Your chat history stays as it is."

    // MARK: Conflicts

    /// What the "changed elsewhere" card says about the hub's version.
    public func changedElsewhere(_ theirs: MemoryFile) -> String {
        let who = switch theirs.updatedBy {
        case .agent: "Marcel changed this note"
        case .app: "Someone changed this note in the app"
        case nil: "This note was changed"
        }
        return "\(who) \(when(theirs.updatedAt)), after you started editing. Your changes are not saved yet, and "
            + "nothing was overwritten."
    }
}
