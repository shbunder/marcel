import Foundation
import MarcelItems
import MarcelKit
import PixelKit

/// One row of the chat, ready to draw. Built by `ChatFeed.items` from the cache plus what the app holds itself
/// (approvals, previews), so what the feed shows is a pure function of what the hub sent: a task that changes
/// changes its card and never adds a row.
public enum FeedItem: Identifiable, Equatable, Sendable {
    /// Something you wrote.
    case user(UserLine)
    /// Marcel speaking: plain formatted text across the full width (ADR-007), and the live card of the thread it is
    /// about.
    case marcel(MarcelLine)
    /// A thread, shown as a quest card that follows its task.
    case quest(QuestLine)
    /// A question from a thread, with approve / deny / details.
    case approval(ApprovalLine)
    /// An item a thread made (a "done" card with its preview), or the morning board.
    case item(ItemLine)
    /// The time, between groups of messages more than 15 minutes apart and above the first one.
    case time(TimeLine)

    public var id: String {
        switch self {
        case .user(let line): line.id
        case .marcel(let line): line.id
        case .quest(let line): line.id
        case .approval(let line): line.id
        case .item(let line): line.id
        case .time(let line): line.id
        }
    }
}

public struct UserLine: Equatable, Sendable {
    public enum Status: Equatable, Sendable {
        /// Stored by the hub.
        case sent
        /// On its way.
        case sending
        /// Not stored; tap to send it again.
        case failed
    }

    public var id: String
    public var text: String
    public var status: Status = .sent
    /// For a message not yet back from the hub: the id that makes a retry safe.
    public var clientId: String?
    /// The project room it was sent from (`project_id`, B-32): drawn as that project's chip under the message.
    public var project: String?
    public var tone: ProjectChip.Tone = .teal
    /// The title of the item attached to it ("Ask about this", `artifact_id`), drawn under the message.
    public var attachment: String?
    /// Not sent because the attached item is gone from the hub: a tap sends it without the item.
    public var itemGone = false

    public init(id: String, text: String, status: Status = .sent, clientId: String? = nil, project: String? = nil,
                tone: ProjectChip.Tone = .teal, attachment: String? = nil) {
        self.id = id
        self.text = text
        self.status = status
        self.clientId = clientId
        self.project = project
        self.tone = tone
        self.attachment = attachment
    }

    /// What VoiceOver reads.
    public var accessibilityText: String {
        let about = (project.map { ", about \($0)" } ?? "") + (attachment.map { ", with the item \($0)" } ?? "")
        return switch status {
        case .sent: "You said\(about): \(text)"
        case .sending: "Sending\(about): \(text)"
        case .failed where itemGone:
            "Not sent\(about): \(text). The item is gone from the hub. Double-tap to send it without the item."
        case .failed: "Not sent\(about): \(text). Double-tap to try again."
        }
    }
}

/// A message you sent that the cache does not hold yet.
public struct PendingMessage: Equatable, Sendable {
    public var clientId: String
    public var text: String
    public var failed: Bool
    /// The hub has it; it shows from the cache once the stream brings it.
    public var stored = false
    /// The item going with it.
    public var attachment: ItemAttachment?
    /// The hub said the item is gone (404 `artifact_not_found`): a retry goes without it.
    public var itemGone = false

    public init(clientId: String, text: String, failed: Bool, stored: Bool = false, attachment: ItemAttachment? = nil) {
        self.clientId = clientId
        self.text = text
        self.failed = failed
        self.stored = stored
        self.attachment = attachment
    }
}

public struct MarcelLine: Equatable, Sendable {
    public var id: String
    /// The reply's Markdown as blocks (PixelKit's `ItemMarkdown`, drawn by `MarkdownView` in its chat style).
    public var parts: [ItemMarkdown.Part]
    /// The thread this message is about (a follow-up Marcel routed to it), drawn live under the text.
    public var quest: QuestLine?

    public init(id: String, markdown: String, quest: QuestLine? = nil) {
        self.id = id
        self.parts = ItemMarkdown.parse(markdown)
        self.quest = quest
    }

    /// The reply's words, for VoiceOver summaries and the side chat's quote.
    public var text: String { ItemMarkdown.plainText(parts) }
}

/// "Today 08:12" between groups of messages.
public struct TimeLine: Hashable, Sendable {
    public var id: String
    public var date: Date

    /// Groups more than this far apart get a time line between them.
    public static let gap: TimeInterval = 15 * 60

    /// "08:12" today, "Yesterday 21:40", "Mon 12 Oct 08:00" before that; "Today" on the first line of a window. The
    /// clock follows the phone's 12- or 24-hour setting (`locale`) and the day its time zone (`calendar`).
    public func text(now: Date, first: Bool, calendar: Calendar = .current, locale: Locale = .current) -> String {
        var style = Date.FormatStyle(locale: locale, calendar: calendar, timeZone: calendar.timeZone)
        let clock = date.formatted(style.hour().minute())
        style = style.weekday(.abbreviated).day().month(.abbreviated)
        if calendar.isDate(date, inSameDayAs: now) { return first ? "Today \(clock)" : clock }
        if let yesterday = calendar.date(byAdding: .day, value: -1, to: now), calendar.isDate(date, inSameDayAs: yesterday) {
            return "Yesterday \(clock)"
        }
        let day = date.formatted(style)
        return "\(day) \(clock)"
    }
}

public struct QuestLine: Hashable, Sendable {
    public var id: String
    public var taskId: String
    public var title: String
    public var state: StateBadge.Kind
    public var project: String?
    public var tone: ProjectChip.Tone
    public var detail: String?
    /// Where it runs, for the "started" card's label ("NUC", "Cloud").
    public var location: Components.Schemas.TaskLocation?

    public init(id: String, taskId: String, title: String, state: StateBadge.Kind, project: String?,
                tone: ProjectChip.Tone, detail: String?, location: Components.Schemas.TaskLocation? = nil) {
        self.id = id
        self.taskId = taskId
        self.title = title
        self.state = state
        self.project = project
        self.tone = tone
        self.detail = detail
        self.location = location
    }
}

public struct ApprovalLine: Equatable, Sendable {
    public enum Status: Equatable, Sendable {
        /// The approval itself has not arrived yet.
        case loading
        case open
        case sending
        case approved(viaTerminal: Bool)
        case denied(viaTerminal: Bool)
        case expired

        public var isOpen: Bool { self == .open }
    }

    public var id: String
    public var approvalId: String
    public var taskId: String
    /// "Fix the flaky login test asks: Run git push origin fix/login-test"
    public var question: String
    public var detail: String?
    public var status: Status
    public var showsDetail: Bool

    /// The resolved state, in plain words; nil while it can still be answered.
    public var outcome: String? {
        switch status {
        case .loading, .open: nil
        case .sending: "Sending your answer…"
        case .approved(let terminal): terminal ? "Approved in a terminal." : "You approved this."
        case .denied(let terminal): terminal ? "Denied in a terminal." : "Not now: the quest won't do this."
        case .expired: "This question went away without an answer."
        }
    }
}

public struct ItemLine: Hashable, Sendable {
    public var id: String
    public var artifactId: String
    public var title: String
    public var savedTo: String?
    public var badge: String
    public var rows: [PreviewRow]
    /// A new item ("You got an item"): only on the message for an item's first version, and only once per item.
    public var showsBanner: Bool
    /// The morning board (a `board` artifact): a preview card, no banner.
    public var isDigest: Bool
    public var url: URL?
}

public struct PreviewRow: Hashable, Sendable {
    public var label: String
    public var value: String?
    public var emphasised: Bool

    public init(_ label: String, value: String? = nil, emphasised: Bool = false) {
        self.label = label
        self.value = value
        self.emphasised = emphasised
    }
}

/// What the app holds besides the cache: approvals as the hub last answered, answers on their way, open "details",
/// and the first lines of items.
public struct ChatLocalState: Sendable {
    public var approvals: [String: Approval] = [:]
    public var answering: Set<String> = []
    public var expanded: Set<String> = []
    /// Preview rows by `ArtifactPreview.key` (id and version).
    public var previews: [String: [PreviewRow]] = [:]
    /// Your messages on their way, oldest first.
    public var pending: [PendingMessage] = []

    public init() {}
}

public enum ChatFeed {
    /// The rows for a window of the conversation, oldest first.
    public static func items(_ input: FeedInput, local: ChatLocalState = ChatLocalState()) -> [FeedItem] {
        var items: [FeedItem] = []
        var carded = Set<String>()      // tasks that already have their card in this window
        var shownItems = Set<String>()  // artifacts already announced in this window
        var lastShown: Date?            // the newest message that drew a row
        for message in input.messages {
            let before = items.count
            switch message.kind {
            case .text, .quote:
                let text = message.text ?? ""
                if message.role == .user {
                    // Sent from a project room: tagged with that project (B-32).
                    let project = message.projectId.flatMap { input.projects[$0] }
                    // "Ask about this": the attached item, by its title when the phone has it.
                    let attached = message.artifactId.map { input.artifacts[$0]?.title ?? "An item" }
                    items.append(.user(UserLine(id: message.id, text: text, project: project?.name,
                                                tone: project.map { tone(for: $0.id) } ?? .teal,
                                                attachment: attached)))
                } else {
                    let routed = message.taskId.flatMap { Self.quest(id: "\(message.id)#quest", taskId: $0, input: input) }
                    items.append(.marcel(MarcelLine(id: message.id, markdown: text, quest: routed)))
                }
            case .milestone:
                guard let milestone = message.milestone else { continue }
                if carded.insert(milestone.taskId).inserted {
                    // The thread's card: it follows the task from now on (task.updated changes it in place).
                    items.append(.quest(quest(id: message.id, taskId: milestone.taskId, input: input)
                        ?? QuestLine(id: message.id, taskId: milestone.taskId, title: milestone.taskTitle,
                                     state: kind(for: milestone.event), project: nil, tone: .teal,
                                     detail: milestone.event == .failed
                                         ? milestone.summary ?? location(milestone.location)
                                         : location(milestone.location),
                                     location: milestone.location)))
                } else {
                    // A later milestone of a thread already on screen: one line, never a second card.
                    items.append(.marcel(MarcelLine(id: message.id, markdown: says(milestone))))
                }
            case .approval:
                guard let approvalId = message.approvalId else { continue }
                items.append(.approval(approval(message: message, approvalId: approvalId, input: input, local: local)))
            case .artifact:
                guard let artifactId = message.artifactId else { continue }
                // Later versions only update the card already shown (contract: MessageKind `artifact`).
                guard shownItems.insert(artifactId).inserted else { continue }
                items.append(.item(item(message: message, artifactId: artifactId, input: input, local: local)))
            }
            guard items.count > before else { continue }
            if lastShown.map({ message.createdAt.timeIntervalSince($0) > TimeLine.gap }) ?? true {
                items.insert(.time(TimeLine(id: "time-\(message.id)", date: message.createdAt)), at: before)
            }
            lastShown = message.createdAt
        }
        // Your messages on their way go last, until the hub's copy (same client id) arrives.
        let arrived = Set(input.messages.compactMap(\.clientId))
        for message in local.pending where !arrived.contains(message.clientId) {
            var line = UserLine(id: "pending-\(message.clientId)", text: message.text,
                                status: message.failed ? .failed : message.stored ? .sent : .sending,
                                clientId: message.clientId, attachment: message.attachment?.title)
            line.itemGone = message.itemGone
            items.append(.user(line))
        }
        return items
    }

    /// True while a question in the window waits for you (the composer then says "Or say something…").
    public static func hasOpenQuestion(_ items: [FeedItem]) -> Bool {
        items.contains { if case .approval(let line) = $0 { line.status.isOpen } else { false } }
    }

    // MARK: Threads

    static func quest(id: String, taskId: String, input: FeedInput) -> QuestLine? {
        guard let task = input.tasks[taskId] else { return nil }
        let project = input.projects[task.projectId]
        // A failed quest's card says what went wrong, in the words of its "failed" milestone when there is one.
        let failure = task.state == .failed
            ? input.messages.last { $0.milestone?.taskId == taskId && $0.milestone?.event == .failed }?.milestone?.summary
            : nil
        return QuestLine(
            id: id, taskId: taskId, title: task.title, state: kind(for: task.state), project: project?.name,
            tone: tone(for: task.projectId), detail: failure ?? detail(for: task), location: task.location)
    }

    static func kind(for state: Components.Schemas.TaskState) -> StateBadge.Kind {
        switch state {
        case .queued: .queued
        case .starting: .starting
        case .working: .working
        case .needsYou: .needsYou
        case .done: .done
        case .failed: .failed
        case .stopped: .stopped
        case .silent: .silent
        case .unknown: .unknown
        }
    }

    static func kind(for event: Components.Schemas.Milestone.EventPayload) -> StateBadge.Kind {
        switch event {
        case .started, .adopted: .working
        case .queued: .queued
        case .needsYou: .needsYou
        case .done: .done
        case .failed: .failed
        case .stopped: .stopped
        case .silent: .silent
        }
    }

    static func location(_ location: Components.Schemas.TaskLocation) -> String {
        location == .nuc ? "On the NUC" : "In the cloud"
    }

    /// The card's second line: where it runs, what it waits for, or how it ended.
    static func detail(for task: HubTask) -> String {
        switch task.state {
        case .queued:
            task.queuedReason == .usage ? "Waiting for the usage limit to reset" : "Waiting for a free slot"
        case .starting, .working, .unknown: location(task.location)
        case .needsYou: task.waitingFor ?? "Waiting for your answer"
        case .done:
            switch task.artifactCount ?? 0 {
            case 0: "Finished"
            case 1: "Finished · 1 item"
            case let n: "Finished · \(n) items"
            }
        case .failed: "Stopped with a problem. Open it to see why."
        case .stopped: "Stopped"
        case .silent: "Gone quiet. Open it to check on it."
        }
    }

    /// What Marcel says for a later milestone of a thread that already has its card.
    static func says(_ milestone: Components.Schemas.Milestone) -> String {
        let title = milestone.taskTitle
        let summary = milestone.summary.map { " \($0)" } ?? ""
        switch milestone.event {
        case .started: return "Started \(title) again."
        case .adopted: return "Took over \(title)."
        case .queued: return "\(title) is waiting for a free slot."
        case .needsYou: return "\(title) needs you.\(summary)"
        case .done: return "Done: \(title).\(summary)"
        case .failed: return "\(title) failed.\(summary)"
        case .stopped: return "Stopped \(title).\(summary)"
        case .silent: return "\(title) has gone quiet."
        }
    }

    /// A project's chip colour, the same on every launch and every device (FNV-1a of its id).
    static func tone(for projectId: String) -> ProjectChip.Tone {
        var hash: UInt32 = 2_166_136_261
        for byte in projectId.utf8 {
            hash = (hash ^ UInt32(byte)) &* 16_777_619
        }
        let tones = ProjectChip.Tone.allCases
        return tones[Int(hash % UInt32(tones.count))]
    }

    // MARK: Approvals

    static func approval(message: Message, approvalId: String, input: FeedInput, local: ChatLocalState)
        -> ApprovalLine
    {
        let approval = local.approvals[approvalId]
        let taskId = approval?.taskId ?? message.taskId ?? ""
        let asker = input.tasks[taskId]?.title ?? "A thread"
        let status: ApprovalLine.Status
        if local.answering.contains(approvalId) {
            status = .sending
        } else if let approval {
            let terminal = approval.answeredVia == .terminal
            switch approval.state {
            case .open: status = .open
            case .approved: status = .approved(viaTerminal: terminal)
            case .denied: status = .denied(viaTerminal: terminal)
            case .expired: status = .expired
            }
        } else {
            status = .loading
        }
        let summary = approval.map { ItemMarkdown.plainText(ItemMarkdown.parse($0.summary)) }
            ?? input.tasks[taskId]?.waitingFor ?? "Fetching the question…"
        return ApprovalLine(
            id: message.id, approvalId: approvalId, taskId: taskId, question: "\(asker) asks: \(summary)",
            detail: approval?.detail, status: status, showsDetail: local.expanded.contains(approvalId))
    }

    // MARK: Items

    static func item(message: Message, artifactId: String, input: FeedInput, local: ChatLocalState) -> ItemLine {
        guard let artifact = input.artifacts[artifactId] else {
            return ItemLine(id: message.id, artifactId: artifactId, title: "New item", savedTo: nil,
                            badge: "Arriving", rows: [PreviewRow("Fetching the item…")], showsBanner: false,
                            isDigest: false, url: nil)
        }
        let isDigest = artifact.kind == .board
        let project = input.projects[artifact.projectId]?.name
        let version = artifact.currentVersion
        let badge = isDigest ? "Board · v\(version)" : version > 1 ? "Updated · v\(version)" : "New"
        return ItemLine(
            id: message.id, artifactId: artifactId, title: artifact.title, savedTo: project, badge: badge,
            rows: local.previews[ArtifactPreview.key(artifact)] ?? ArtifactPreview.summaryRows(artifact),
            showsBanner: !isDigest, isDigest: isDigest, url: artifact.url.flatMap(URL.init(string:)).flatMap(ItemMarkdown.webLink))
    }
}
