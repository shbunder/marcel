import Foundation
import MarcelKit

/// The words Settings and the status screen show, worked out from the hub's data. Pure, so tests read them.
public struct SettingsText: Sendable {
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

    /// "21:00", "tomorrow 07:00", "Mon 5 Oct, 09:00".
    public func when(_ date: Date) -> String {
        let time = date.formatted(Date.FormatStyle(timeZone: timeZone).hour(.twoDigits(amPM: .omitted))
            .minute(.twoDigits).locale(Locale(identifier: "en_GB")))
        if calendar.isDate(date, inSameDayAs: now) { return time }
        if let tomorrow = calendar.date(byAdding: .day, value: 1, to: now), calendar.isDate(date, inSameDayAs: tomorrow) {
            return "tomorrow \(time)"
        }
        if let yesterday = calendar.date(byAdding: .day, value: -1, to: now),
           calendar.isDate(date, inSameDayAs: yesterday) {
            return "yesterday \(time)"
        }
        let day = date.formatted(Date.FormatStyle(timeZone: timeZone).weekday(.abbreviated).day().month(.abbreviated)
            .locale(Locale(identifier: "en_GB")))
        return "\(day), \(time)"
    }

    // MARK: Usage

    public struct Meter: Equatable, Sendable {
        public var label: String
        /// 0…1
        public var fraction: Double
        public var value: String
        public var detail: String?
    }

    public func meters(_ usage: Usage) -> [Meter] {
        [meter("Next 5 hours", usage.fiveHour), meter("This week", usage.weekly)]
    }

    private func meter(_ label: String, _ window: Components.Schemas.UsageWindow) -> Meter {
        let pct = min(max(window.usedPct, 0), 100)
        return Meter(label: label, fraction: pct / 100, value: "\(Int(pct.rounded())) %",
                     detail: window.resetsAt.map { "resets \(when($0))" })
    }

    /// The guard's own words while it holds work back, and a warning when the reading is old.
    public func usageNotes(_ usage: Usage) -> [String] {
        var notes: [String] = []
        if usage._guard.active {
            notes.append(usage._guard.reason ?? "New quests wait until the plan's usage resets.")
            if usage._guard.queued > 0 {
                notes.append(usage._guard.queued == 1 ? "1 quest is waiting." : "\(usage._guard.queued) quests are waiting.")
            }
        }
        if usage.stale {
            notes.append("This reading is old" + (usage.takenAt.map { " (\(when($0)))" } ?? "") + ", so it may be off.")
        }
        return notes
    }

    // MARK: Schedules

    public func scheduleLine(_ schedule: Schedule) -> String {
        let place = switch schedule.location {
        case .brain: "Marcel"
        case .nuc: "NUC"
        case .cloud: "Cloud"
        }
        if schedule.paused { return "Paused · \(place)" }
        guard let next = schedule.nextRunAt else { return "No run planned · \(place)" }
        return "Next: \(when(next)) · \(place)"
    }

    // MARK: Devices

    public func deviceLine(_ device: Device) -> String {
        let push = switch device.push {
        case .apns: "notifications by Apple"
        case .ntfy: "notifications by ntfy"
        case .relay: "notifications by Marcel's relay"
        case .none: "no notifications"
        }
        if device.current == true { return "This phone · \(push)" }
        let seen = device.lastSeenAt.map { "Last seen \(when($0))" } ?? "Paired \(when(device.createdAt))"
        return "\(seen) · \(push)"
    }

    // MARK: Status

    public struct StatusLine: Equatable, Sendable {
        public var label: String
        public var value: String
        public var ok: Bool
    }

    public func statusLines(_ health: Health) -> [StatusLine] {
        var lines = [
            StatusLine(label: "Hub", value: health.ok ? "Running · v\(health.version)" : "Not well · v\(health.version)",
                       ok: health.ok),
            StatusLine(label: "NUC runner",
                       value: health.runner.reachable ? "Reachable"
                           : "Not reachable" + (health.runner.reason.map { ": \($0)" } ?? ""),
                       ok: health.runner.reachable),
        ]
        if let alive = health.brainAlive {
            lines.append(StatusLine(label: "Marcel's brain", value: alive ? "Running" : "Not running", ok: alive))
        }
        return lines
    }

    public func alertLine(_ alert: PushAlert) -> String {
        "\(alert.title): \(alert.body) (\(when(alert.at)))"
    }
}
