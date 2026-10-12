import Foundation
import MarcelChat
import MarcelItems
import MarcelKit
import MarcelQuests
import PixelKit
import SwiftData
import SwiftUI

/// The Map tab (wireframes 5A and 5B): one tile per project, and each project's room behind it.
///
/// Everything comes from the cache (`MarcelKit`'s SwiftData rows), which the live event stream keeps current, so a
/// `project.updated` changes a tile and a `task.moved` moves a quest between rooms as soon as they arrive. Needs the
/// app's `.modelContainer`.
public struct MapView: View {
    @Query private var projects: [CachedProject]
    @Query private var artifacts: [CachedArtifact]
    @Query private var tasks: [CachedTask]
    @State private var model: MapModel
    @State private var path: [String] = []
    @Environment(\.modelContext) private var context
    @Environment(\.colorScheme) private var scheme
    private let newProject: (String) -> Void
    /// A project room to open (a `marcel://project/<slug>` link); cleared once it is open.
    @Binding private var opening: String?

    /// - Parameters:
    ///   - service: the hub; nil reads the paired hub from pairing's stores (address and Keychain token).
    ///   - conversationId: the main conversation, when the chat already knows it.
    ///   - opening: a project id whose room to open now (a link); the map sets it back to nil.
    ///   - newProject: "+ new" — opens the chat with this request in its composer.
    public init(service: (any ProjectService)? = nil, conversationId: String? = nil,
                opening: Binding<String?> = .constant(nil), newProject: @escaping (String) -> Void = { _ in }) {
        _model = State(initialValue: MapModel(service: service, conversationId: conversationId))
        _opening = opening
        self.newProject = newProject
    }

    public var body: some View {
        NavigationStack(path: $path) {
            // Board times ("11:52", "yesterday") move on once a minute.
            TimelineView(.periodic(from: .now, by: 60)) { timeline in
                let input = MapInput.read(tasks: tasks, artifacts: artifacts, projects: projects)
                map(ProjectMap(projects: input.projects, artifacts: input.artifacts, now: timeline.date))
            }
            .background(PixelTheme(scheme).background.color)
            .toolbar(.hidden, for: .navigationBar)
            .navigationDestination(for: String.self) { projectId in
                ProjectRoomView(projectId: projectId, model: model, tasks: tasks, artifacts: artifacts,
                                projects: projects, back: { path.removeLast() })
            }
        }
        .onAppear {
            if model.cache == nil { model.cache = MarcelCache(container: context.container) }
            if model.service == nil { model.service = LiveProjectService.paired() }
        }
        .task { await model.refreshProjects() }
        .onChange(of: opening, initial: true) { _, projectId in
            guard let projectId else { return }
            path = [projectId]
            opening = nil
        }
    }

    private func map(_ map: ProjectMap) -> some View {
        VStack(spacing: 0) {
            MapHeader()
            ScrollView {
                MapContent(map: map, problem: model.problem, dismiss: { model.problem = nil },
                           tile: { tile in
                               Button { path.append(tile.id) } label: { TileView(tile: tile) }
                                   .buttonStyle(.plain)
                                   .accessibilityHint("Opens the project room")
                           },
                           new: {
                               Button { newProject(ProjectMap.newProjectRequest) } label: { NewTile() }
                                   .buttonStyle(.plain)
                           })
            }
            .refreshable { await model.refreshProjects() }
        }
    }
}

/// A project's room (wireframe 5B).
struct ProjectRoomView: View {
    var projectId: String
    var model: MapModel
    var tasks: [CachedTask]
    var artifacts: [CachedArtifact]
    var projects: [CachedProject]
    var back: () -> Void

    @State private var showing: Sheet?
    @FocusState private var typing: Bool
    @Environment(\.colorScheme) private var scheme

    /// What a tap on the board or an item opens: the artifact viewer.
    enum Sheet: Identifiable {
        case board(BoardCard)
        case item(RoomItem)

        var id: String {
            switch self {
            case .board(let board): "board-\(board.artifactId)"
            case .item(let item): "item-\(item.id)"
            }
        }

        var artifactId: String {
            switch self {
            case .board(let board): board.artifactId
            case .item(let item): item.id
            }
        }
    }

    var body: some View {
        TimelineView(.periodic(from: .now, by: 60)) { timeline in
            let input = MapInput.read(tasks: tasks, artifacts: artifacts, projects: projects)
            if let room = ProjectRoom(projectId: projectId, tasks: input.tasks, artifacts: input.artifacts,
                                      projects: input.projects, now: timeline.date) {
                content(room)
                    .task(id: room.board.key) { await model.loadBoard(room.board) }
                    .task(id: room.id) { await model.open(room) }
            } else {
                gone
            }
        }
        .background(PixelTheme(scheme).background.color)
        .toolbar(.hidden, for: .navigationBar)
        .sheet(item: $showing) { sheet in
            ArtifactViewer(artifactId: sheet.artifactId, conversationId: model.conversationId)
        }
    }

    private func content(_ room: ProjectRoom) -> some View {
        @Bindable var model = model
        let draft = Binding(get: { model.drafts[room.id] ?? "" }, set: { model.drafts[room.id] = $0 })
        return VStack(spacing: 0) {
            RoomHeader(room: room, back: back)
            ScrollView {
                RoomContent(
                    room: room, notes: model.notes[room.slug], problem: model.problem,
                    dismiss: { model.problem = nil },
                    board: {
                        Button { showing = .board(room.board) } label: {
                            BoardView(board: room.board, state: model.boards[room.board.key])
                        }
                        .buttonStyle(.plain)
                        .accessibilityHint("Opens the board")
                    },
                    quest: { row in quest(row, room: room) },
                    item: { item in
                        Button { showing = .item(item) } label: { ItemChip(item: item, tone: room.tone) }
                            .buttonStyle(.plain)
                    })
            }
            .refreshable {
                await model.open(room)
                await model.loadBoard(room.board)
            }
            RoomComposer(room: room, draft: draft, sent: model.sent[room.id], focused: $typing) {
                Task { await model.send(projectId: room.id, projectName: room.name) }
            }
        }
    }

    /// A quest card; its menu (long press, or VoiceOver's actions) moves it to another project.
    private func quest(_ row: QuestRow, room: ProjectRoom) -> some View {
        RoomQuest(row: row, moving: model.moving.contains(row.id))
            .contextMenu {
                Menu("Move to another project") {
                    ForEach(room.moveTargets) { target in
                        Button(target.name) { Task { await model.move(taskId: row.id, to: target.id) } }
                    }
                }
            }
            .accessibilityActions {
                ForEach(room.moveTargets) { target in
                    Button("Move to \(target.name)") { Task { await model.move(taskId: row.id, to: target.id) } }
                }
            }
    }

    private var gone: some View {
        VStack(spacing: PixelMetrics.steps(6)) {
            PixelText("This project is no longer on the map.", role: .body)
                .foregroundStyle(PixelTheme(scheme).muted.color)
            Button("Back to the map", action: back)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

/// The cached rows the map and the rooms are drawn from, decoded. Rows that no longer decode are skipped: one bad
/// row never blanks the map.
public struct MapInput: Sendable {
    public var tasks: [HubTask]
    public var artifacts: [Artifact]
    public var projects: [Project]

    public static func read(tasks: [CachedTask], artifacts: [CachedArtifact], projects: [CachedProject]) -> MapInput {
        let decoder = MapJSON.decoder()
        func rows<T: Decodable>(_ payloads: [Data], as _: T.Type) -> [T] {
            payloads.compactMap { try? decoder.decode(T.self, from: $0) }
        }
        return MapInput(tasks: rows(tasks.map(\.payload), as: HubTask.self),
                        artifacts: rows(artifacts.map(\.payload), as: Artifact.self),
                        projects: rows(projects.map(\.payload), as: Project.self))
    }

    /// Reads the cache straight from a context (tests, previews): the same rows the screen's `@Query`s hold.
    @MainActor
    public static func read(from context: ModelContext) throws -> MapInput {
        read(tasks: try context.fetch(FetchDescriptor<CachedTask>()),
             artifacts: try context.fetch(FetchDescriptor<CachedArtifact>()),
             projects: try context.fetch(FetchDescriptor<CachedProject>()))
    }
}

/// The hub's JSON dates (RFC 3339, with or without fractions), as the cache stores them.
enum MapJSON {
    static func decoder() -> JSONDecoder {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .custom { decoder in
            let container = try decoder.singleValueContainer()
            let text = try container.decode(String.self)
            let withFraction = Date.ISO8601FormatStyle(includingFractionalSeconds: true)
            guard let date = (try? withFraction.parse(text)) ?? (try? Date.ISO8601FormatStyle().parse(text)) else {
                throw DecodingError.dataCorruptedError(
                    in: container, debugDescription: "Not an RFC 3339 date: \(text)")
            }
            return date
        }
        return decoder
    }
}
