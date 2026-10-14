import Foundation
import MarcelKit
import MarcelQuests
import PixelKit

/// A project room as data (wireframe 5B): the project, its pinned board, its quests in groups, its items and where
/// its quests can be moved. Built from the same cached rows as the map, so a `task.moved` or `project.updated` that
/// reaches the cache changes the room at once. Pure, so the tests check exactly what the screen shows.
public struct ProjectRoom: Equatable, Sendable {
    public var id: String
    public var name: String
    /// The project's goal, under its name.
    public var goal: String?
    public var slug: String
    public var tone: ProjectChip.Tone
    public var claudeProject: URL?
    /// The coding project's tracker (B-46); nil hides "Open tracker".
    public var tracker: URL?
    public var board: BoardCard
    /// The project's quests as the quest log picks them (live, finished in the last week).
    public var quests: QuestBoard
    /// The same quests in Claude Code Projects' groups (ADR-011, S-12.14), each group hidden when empty.
    public var sections: [RoomSection]
    /// Its items besides the board, newest first.
    public var items: [RoomItem]
    /// The live projects a quest here can move to: every other one, inbox last.
    public var moveTargets: [MoveTarget]

    /// Nil when the cache no longer has the project (archived elsewhere, or not synced yet).
    public init?(projectId: String, tasks: [HubTask], artifacts: [Artifact], projects: [Project], now: Date) {
        guard let project = projects.first(where: { $0.id == projectId }) else { return nil }
        id = project.id
        name = project.name
        goal = project.goal.flatMap { $0.isEmpty ? nil : $0 }
        slug = project.slug
        tone = ProjectMap.tone(for: project.id)
        claudeProject = project.claudeProjectUrl.flatMap(URL.init(string:))
        tracker = TrackerButton.url(project.trackerUrl)

        let mine = artifacts.filter { $0.projectId == project.id }
        let pinned = artifacts.first { $0.id == project.boardArtifactId }
        board = BoardCard(artifactId: project.boardArtifactId, version: pinned?.currentVersion)

        quests = QuestBoard(tasks: tasks, artifacts: artifacts, projects: projects,
                            filter: QuestFilter(projectId: project.id), now: now)
        sections = RoomSection.grouped(quests, tasks: tasks, artifacts: artifacts)
        items = mine.filter { $0.kind != .board && $0.id != project.boardArtifactId }
            .sorted { $0.updatedAt != $1.updatedAt ? $0.updatedAt > $1.updatedAt : $0.id < $1.id }
            .map(RoomItem.init)
        moveTargets = projects.filter { !$0.archived && $0.id != project.id }
            .sorted { lhs, rhs in
                // In the menu: by name, the inbox last.
                let l = ProjectMap.isInbox(lhs), r = ProjectMap.isInbox(rhs)
                if l != r { return r }
                return lhs.name.localizedStandardCompare(rhs.name) == .orderedAscending
            }
            .map { MoveTarget(id: $0.id, name: $0.name) }
    }

    /// The quest ids in each group, top to bottom.
    public var questIds: [String] {
        sections.flatMap { $0.rows.map(\.id) }
    }

    /// What VoiceOver says for the room's title strip.
    public var spokenTitle: String {
        [name, goal].compactMap { $0 }.joined(separator: ". ")
    }
}

/// The pinned board: a white card labelled "Project board · Pinned · v7". Its rows come from the board's
/// content, which the room fetches per version.
public struct BoardCard: Equatable, Sendable {
    public var artifactId: String
    /// Nil until the board's artifact is in the cache.
    public var version: Int?

    public var title: String { "Project board" }
    public var badge: String { version.map { "Pinned · v\($0)" } ?? "Pinned" }
    public var spokenBadge: String { version.map { "Pinned, version \($0)" } ?? "Pinned" }
    /// One fetch per version: a new version is fetched again.
    public var key: String { "\(artifactId)@\(version ?? 0)" }
}

/// A project a quest can be moved to, in its card's menu.
public struct MoveTarget: Identifiable, Hashable, Sendable {
    public var id: String
    public var name: String
}

/// An item in the room: a white card with its title and a pixel meta line ("PULL REQUEST · OPEN", wireframe Q2).
public struct RoomItem: Identifiable, Equatable, Sendable {
    public var id: String
    public var title: String
    public var kind: Components.Schemas.ArtifactKind
    public var artifact: Artifact

    init(_ artifact: Artifact) {
        id = artifact.id
        title = artifact.title
        kind = artifact.kind
        self.artifact = artifact
    }

    /// The card's meta line: what it is, then its PR state or its version ("Pull request · open", "Document · v3").
    public var meta: String {
        var parts = [kindWord]
        if let state = artifact.pr?.state {
            parts.append(state.rawValue)
        } else if artifact.currentVersion > 1 {
            parts.append("v\(artifact.currentVersion)")
        }
        return parts.joined(separator: " · ")
    }

    public var spoken: String {
        "\(kindWord): \(title)"
    }

    var kindWord: String {
        switch kind {
        case .pr: "Pull request"
        case .branch: "Branch"
        case .doc: "Document"
        case .file: "File"
        case .dashboard, .board: "Dashboard"
        case .link: "Link"
        }
    }
}

/// The project room's groups (ADR-011, S-12.14): the words of Claude Code Projects' Overview, so an owner who uses
/// both sees the same ones. They come from each task's state and its pull requests: no new task state, no contract
/// change.
public enum RoomGroup: String, CaseIterable, Sendable {
    case needsYou, working, readyForReview, done, ended

    public var title: String {
        switch self {
        case .needsYou: "Needs you"
        case .working: "Working"
        case .readyForReview: "Ready for review"
        case .done: "Done"
        case .ended: "Ended"
        }
    }

    /// Where a task goes. A `done` task goes by its pull requests: any open or draft one makes it ready for review;
    /// otherwise any merged one makes it done; otherwise (every one closed) it ended; with none it is done.
    public static func of(_ state: Components.Schemas.TaskState,
                          pullRequests: [Components.Schemas.Artifact.PrPayload.StatePayload]) -> RoomGroup {
        switch state {
        case .needsYou: return .needsYou
        case .queued, .starting, .working, .silent, .unknown: return .working
        case .failed, .stopped: return .ended
        case .done:
            if pullRequests.contains(where: { $0 == .open || $0 == .draft }) { return .readyForReview }
            if pullRequests.isEmpty || pullRequests.contains(.merged) { return .done }
            return .ended
        }
    }
}

/// One group of quest cards in the room.
public struct RoomSection: Identifiable, Hashable, Sendable {
    public var group: RoomGroup
    public var rows: [QuestRow]
    public var id: RoomGroup { group }

    /// The quest log's cards for this project, regrouped: in `RoomGroup` order, each card keeping its place (newest
    /// activity first) inside its group, empty groups left out.
    static func grouped(_ board: QuestBoard, tasks: [HubTask], artifacts: [Artifact]) -> [RoomSection] {
        let states = Dictionary(tasks.map { ($0.id, $0.state) }, uniquingKeysWith: { a, _ in a })
        var prs: [String: [Components.Schemas.Artifact.PrPayload.StatePayload]] = [:]
        for artifact in artifacts where artifact.kind == .pr {
            if let taskId = artifact.taskId, let state = artifact.pr?.state { prs[taskId, default: []].append(state) }
        }
        let rows = board.sections.flatMap(\.rows).sorted { lhs, rhs in
            lhs.activity != rhs.activity ? lhs.activity > rhs.activity : lhs.id < rhs.id
        }
        var buckets: [RoomGroup: [QuestRow]] = [:]
        for row in rows {
            guard let state = states[row.id] else { continue }
            buckets[RoomGroup.of(state, pullRequests: prs[row.id] ?? []), default: []].append(row)
        }
        return RoomGroup.allCases.compactMap { group in
            buckets[group].map { RoomSection(group: group, rows: $0) }
        }
    }
}
