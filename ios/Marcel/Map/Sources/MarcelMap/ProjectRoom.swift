import Foundation
import MarcelKit
import MarcelQuests
import PixelKit

/// A project room as data (wireframe 5B): the project, its pinned board, its quests by state, its items and where
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
    /// The project's quests, grouped like the quest log (waiting on you first).
    public var quests: QuestBoard
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
        quests.sections.flatMap { $0.rows.map(\.id) }
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

/// An item in the room: drawn as a chip in the project's colour.
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

    public var spoken: String {
        let what = switch kind {
        case .pr: "Pull request"
        case .branch: "Branch"
        case .doc: "Document"
        case .file: "File"
        case .dashboard, .board: "Dashboard"
        case .link: "Link"
        }
        return "\(what): \(title)"
    }
}
