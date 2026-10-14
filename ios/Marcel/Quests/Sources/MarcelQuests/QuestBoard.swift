import Foundation
import MarcelKit
import PixelKit

/// The state filter (S-18.4): the same states, in the same order, as Claude's own session list.
public enum QuestStatusFilter: String, CaseIterable, Sendable {
    case all, waitingOnYou, readyForReview, working, done, archived

    public var title: String {
        switch self {
        case .all: "All"
        case .waitingOnYou: "Waiting on you"
        case .readyForReview: "Ready for review"
        case .working: "Working"
        case .done: "Done"
        case .archived: "Archived"
        }
    }

    /// The groups this filter shows. "Working" includes the queue; "Done" is this week's finished quests.
    public var groups: Set<QuestGroup> {
        switch self {
        case .all: [.waitingOnYou, .readyForReview, .working, .queued, .finished]
        case .waitingOnYou: [.waitingOnYou]
        case .readyForReview: [.readyForReview]
        case .working: [.working, .queued]
        case .done: [.finished]
        case .archived: [.archived]
        }
    }
}

/// What the quest log shows: which project, and which state (the archived quests are one of them).
public struct QuestFilter: Equatable, Hashable, Sendable {
    /// Only this project's quests; nil for every project.
    public var projectId: String?
    public var status: QuestStatusFilter

    public init(projectId: String? = nil, status: QuestStatusFilter = .all) {
        self.projectId = projectId
        self.status = status
    }

    public init(projectId: String? = nil, archived: Bool) {
        self.init(projectId: projectId, status: archived ? .archived : .all)
    }

    /// Archived quests instead of live ones.
    public var archived: Bool {
        get { status == .archived }
        set { status = newValue ? .archived : (status == .archived ? .all : status) }
    }
}

/// The groups of the quest log, top to bottom (wireframe Q2): waiting on you (pinned), working, the queue, ready for
/// review, done in the last 7 days.
public enum QuestGroup: String, CaseIterable, Sendable {
    case waitingOnYou, working, queued, readyForReview, finished, archived

    public var title: String {
        switch self {
        case .waitingOnYou: "Waiting on you"
        case .working: "Working"
        case .queued: "Queued"
        case .readyForReview: "Ready for review"
        case .finished: "Done · last 7 days"
        case .archived: "Archived"
        }
    }

    /// What VoiceOver says: the title with a comma where the screen shows "·".
    public var spokenTitle: String { title.replacingOccurrences(of: " · ", with: ", ") }

    /// Done and failed quests are folded away until you open them.
    public var startsCollapsed: Bool { self == .finished }
}

/// One quest card: everything the card shows and says, worked out from the task, its PR and its project.
public struct QuestRow: Identifiable, Hashable, Sendable {
    public var id: String
    public var title: String
    public var state: StateBadge.Kind
    /// Replaces the state's own badge text ("PR" for a quest ready for review).
    public var stateText: String?
    public var project: String?
    public var tone: ProjectChip.Tone
    /// The card's meta line after the project, short: "NUC · Pick Tue 9:10 · PR #41 · checks passed".
    public var detail: String
    /// What VoiceOver says for the whole card, in full words.
    public var spoken: String
    /// A running or queued quest can be stopped (swipe).
    public var canStop: Bool
    /// A finished quest that is not archived yet can be archived (swipe).
    public var canArchive: Bool
    /// When anything last happened; the order inside a group.
    public var activity: Date
}

public struct QuestSection: Identifiable, Hashable, Sendable {
    public var group: QuestGroup
    public var rows: [QuestRow]
    public var id: QuestGroup { group }

    /// "Waiting on you · 1", "Done · last 7 days · 6": every group carries its count (wireframe Q2).
    public var title: String {
        "\(group.title) · \(rows.count)"
    }
}

/// A project in the filter menu.
public struct ProjectChoice: Identifiable, Hashable, Sendable {
    public var id: String
    public var name: String
}

/// The quest log as data: the cached tasks, their PRs and projects, grouped and ordered for the screen. Pure, so the
/// tests check exactly what the screen shows.
public struct QuestBoard: Equatable, Sendable {
    public var sections: [QuestSection]
    public var projects: [ProjectChoice]
    public var filter: QuestFilter

    /// How long a done, failed or stopped quest stays on the log.
    public static let finishedWindow: TimeInterval = 7 * 24 * 3600

    public init(tasks: [HubTask], artifacts: [Artifact] = [], projects: [Project] = [], filter: QuestFilter = .init(),
                now: Date) {
        self.filter = filter
        let projectsById = Dictionary(projects.map { ($0.id, $0) }, uniquingKeysWith: { a, _ in a })
        let prs = Self.pullRequests(artifacts)
        self.projects = projects.filter { !$0.archived }
            .sorted { lhs, rhs in
                // The inbox is where unfiled quests land; it goes last, the rest by name.
                let l = Self.isInbox(lhs), r = Self.isInbox(rhs)
                if l != r { return r }
                return lhs.name.localizedStandardCompare(rhs.name) == .orderedAscending
            }
            .map { ProjectChoice(id: $0.id, name: $0.name) }

        var buckets: [QuestGroup: [QuestRow]] = [:]
        for task in tasks {
            if let projectId = filter.projectId, task.projectId != projectId { continue }
            let pr = prs[task.id]
            guard let group = Self.group(for: task, pr: pr, archivedView: filter.archived, now: now),
                  filter.status.groups.contains(group) else { continue }
            buckets[group, default: []].append(
                Self.row(task: task, pr: pr, project: projectsById[task.projectId], group: group, now: now))
        }
        sections = QuestGroup.allCases.compactMap { group in
            guard var rows = buckets[group], !rows.isEmpty else { return nil }
            rows.sort { lhs, rhs in
                // The queue runs oldest first; everywhere else the newest activity is on top.
                if lhs.activity != rhs.activity {
                    return group == .queued ? lhs.activity < rhs.activity : lhs.activity > rhs.activity
                }
                return lhs.id < rhs.id
            }
            return QuestSection(group: group, rows: rows)
        }
    }

    public var isEmpty: Bool { sections.isEmpty }

    /// "Done · last 7 days" starts folded away in "All"; picked on its own, it is open.
    public func startsCollapsed(_ group: QuestGroup) -> Bool {
        group.startsCollapsed && filter.status == .all
    }

    /// The project filter's own title ("All projects", "Home energy"); the state is the chips'.
    public var projectTitle: String {
        filter.projectId.map { id in projects.first { $0.id == id }?.name ?? "One project" } ?? "All projects"
    }


    /// What an empty log says, in plain words.
    public var emptyText: String {
        switch filter.status {
        case .waitingOnYou: return "Nothing waits on you."
        case .readyForReview: return "Nothing is ready for review."
        case .working: return "Nothing is running."
        case .done: return "Nothing finished this week."
        case .all, .archived: break
        }
        return switch (filter.archived, filter.projectId != nil) {
        case (true, false): "No archived quests."
        case (true, true): "No archived quests in this project."
        case (false, false): "No quests yet. Ask Marcel for something in Chat."
        case (false, true): "No quests in this project right now."
        }
    }

    // MARK: Rules

    /// Which group a task belongs in, or nil when the log does not show it (archived, or finished over a week ago).
    static func group(for task: HubTask, pr: Artifact?, archivedView: Bool, now: Date) -> QuestGroup? {
        if archivedView { return task.archivedAt == nil ? nil : .archived }
        guard task.archivedAt == nil else { return nil }
        switch task.state {
        case .needsYou: return .waitingOnYou
        case .starting, .working, .silent, .unknown: return .working
        case .queued: return .queued
        case .done where Self.isOpen(pr): return .readyForReview
        case .done, .failed, .stopped:
            return now.timeIntervalSince(activity(task)) <= finishedWindow ? .finished : nil
        }
    }

    static func activity(_ task: HubTask) -> Date {
        task.lastActivityAt ?? task.updatedAt
    }

    static func isOpen(_ pr: Artifact?) -> Bool {
        guard let state = pr?.pr?.state else { return false }
        return state == .open || state == .draft
    }

    static func isInbox(_ project: Project) -> Bool {
        project.slug == "inbox" || project.id == "prj_inbox"
    }

    /// Each task's newest pull request.
    static func pullRequests(_ artifacts: [Artifact]) -> [String: Artifact] {
        var prs: [String: Artifact] = [:]
        for artifact in artifacts where artifact.kind == .pr {
            guard let taskId = artifact.taskId else { continue }
            if let known = prs[taskId], known.updatedAt >= artifact.updatedAt { continue }
            prs[taskId] = artifact
        }
        return prs
    }

    static func row(task: HubTask, pr: Artifact?, project: Project?, group: QuestGroup, now: Date) -> QuestRow {
        let state = kind(for: task.state)
        let ready = group == .readyForReview
        let status = statusLine(task)
        let prText = pr.flatMap(Self.prText)
        let started = Age(from: task.createdAt, to: now)
        let active = Age(from: activity(task), to: now)
        let place = task.location == .nuc ? "NUC" : "Cloud"

        // The card's meta line after the project (wireframe Q2: "HOUSE · NUC · APPROVE FILE READ"): where it runs,
        // what it waits on, its PR. The ages are in what VoiceOver reads.
        var shown = [place]
        if let status { shown.append(status.short) }
        if let prText { shown.append(prText.short) }

        var spoken = [task.title, ready ? "Ready for review" : state.spokenTitle]
        if let project { spoken.append("Project: \(project.name)") }
        if let status { spoken.append(status.spoken) }
        if let prText { spoken.append(prText.spoken) }
        spoken.append(task.location == .nuc ? "Runs on the NUC" : "Runs in the cloud")
        spoken.append("Started \(started.spoken)")
        spoken.append("Last active \(active.spoken)")
        if group == .archived { spoken.append("Archived") }

        return QuestRow(
            id: task.id, title: task.title, state: ready ? .ready : state, stateText: ready ? "PR" : nil,
            project: project?.name, tone: tone(for: task.projectId),
            detail: shown.joined(separator: " · "), spoken: spoken.joined(separator: ". ") + ".",
            canStop: Self.stoppable.contains(task.state) && task.archivedAt == nil,
            canArchive: Self.archivable.contains(task.state) && task.archivedAt == nil,
            activity: group == .queued ? task.createdAt : group == .archived ? (task.archivedAt ?? activity(task))
                : activity(task))
    }

    /// The states the hub stops (`POST /tasks/{id}/stop`; a queued task leaves the queue).
    static let stoppable: Set<Components.Schemas.TaskState> = [.queued, .starting, .working, .needsYou, .silent, .unknown]
    /// The states the hub archives (`POST /tasks/{id}/archive`; anything else is a 409).
    static let archivable: Set<Components.Schemas.TaskState> = [.done, .failed, .stopped]

    struct Words: Equatable {
        var short: String
        var spoken: String
    }

    /// What the quest waits on or how it ended, when that adds something to the badge.
    static func statusLine(_ task: HubTask) -> Words? {
        switch task.state {
        case .needsYou:
            let what = task.waitingFor ?? "Waiting for your answer"
            return Words(short: what, spoken: task.waitingFor.map { "Waiting for: \($0)" } ?? what)
        case .queued:
            let text = task.queuedReason == .usage ? "Waits for the usage limit" : "Waits for a free slot"
            return Words(short: text, spoken: text)
        case .silent:
            return Words(short: "No news past its expected time", spoken: "No news past its expected time")
        case .unknown:
            return Words(short: "The NUC can't be reached", spoken: "The NUC can't be reached right now")
        case .starting, .working, .done, .failed, .stopped:
            return nil
        }
    }

    /// The PR badge: "PR #41 · checks passed".
    static func prText(_ artifact: Artifact) -> Words? {
        guard let pr = artifact.pr else { return nil }
        var short = ["PR #\(pr.number)"]
        var spoken = ["Pull request \(pr.number)"]
        switch pr.state {
        case .draft: short.append("draft"); spoken.append("a draft")
        case .merged: short.append("merged"); spoken.append("merged")
        case .closed: short.append("closed"); spoken.append("closed without merging")
        case .open: break
        }
        if pr.state == .open || pr.state == .draft {
            switch pr.checks {
            case .passing: short.append("checks passed"); spoken.append("checks passed")
            case .failing: short.append("checks failed"); spoken.append("checks failed")
            case .pending: short.append("checks running"); spoken.append("checks still running")
            case .none?, nil: break
            }
        }
        return Words(short: short.joined(separator: " · "), spoken: spoken.joined(separator: ", "))
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

    /// A project's colour, from its id. The same hash as the Chat tab's cards (MarcelChat's `ChatFeed.tone`), so a
    /// project keeps its colour across tabs.
    static func tone(for projectId: String) -> ProjectChip.Tone {
        var hash: UInt32 = 2_166_136_261
        for byte in projectId.utf8 {
            hash = (hash ^ UInt32(byte)) &* 16_777_619
        }
        let tones = ProjectChip.Tone.allCases
        return tones[Int(hash % UInt32(tones.count))]
    }
}

/// How long ago, short for the card ("5m", "2h", "3d") and in words for VoiceOver ("5 minutes ago").
struct Age: Equatable {
    var short: String
    var spoken: String

    init(from date: Date, to now: Date) {
        let seconds = max(0, now.timeIntervalSince(date))
        let minutes = Int(seconds / 60), hours = Int(seconds / 3600), days = Int(seconds / 86_400)
        func words(_ n: Int, _ unit: String) -> String { "\(n) \(unit)\(n == 1 ? "" : "s") ago" }
        if minutes < 1 {
            short = "now"
            spoken = "just now"
        } else if hours < 1 {
            short = "\(minutes)m"
            spoken = words(minutes, "minute")
        } else if days < 1 {
            short = "\(hours)h"
            spoken = words(hours, "hour")
        } else {
            short = "\(days)d"
            spoken = words(days, "day")
        }
    }
}
