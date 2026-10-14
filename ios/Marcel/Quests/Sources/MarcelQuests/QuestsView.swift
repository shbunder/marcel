import Foundation
import MarcelKit
import MarcelQuest
import PixelKit
import SwiftData
import SwiftUI

/// The Quests tab (wireframe Q2): every thread as a quest card on the soft background, its state as the card's frame
/// colour, grouped waiting on you (pinned), working, queued, ready for review and done in the last 7 days (folded),
/// each group with its count. The project filter is at the top, "Archived quests ›" under the log. Swipe a running
/// quest to stop it, a finished one to archive it.
///
/// Everything comes from the cache (`MarcelKit`'s SwiftData rows), which the live event stream keeps current, so a
/// `task.updated` moves a card as soon as it arrives. Needs the app's `.modelContainer`.
public struct QuestsView: View {
    @Query private var tasks: [CachedTask]
    @Query private var artifacts: [CachedArtifact]
    @Query private var projects: [CachedProject]
    @State private var model: QuestsModel
    /// The quest opened by tapping its card.
    @State private var opened: [String] = []
    @Environment(\.modelContext) private var context
    @Environment(\.colorScheme) private var scheme

    /// - Parameter service: the hub; nil reads the paired hub from pairing's stores (address and Keychain token).
    public init(service: (any QuestService)? = nil) {
        _model = State(initialValue: QuestsModel(service: service))
    }

    public var body: some View {
        NavigationStack(path: $opened) {
            log
                .toolbar(.hidden, for: .navigationBar)
                .navigationDestination(for: String.self) { QuestView(taskId: $0) }
        }
    }

    private var log: some View {
        // Ages on the cards ("active 5m ago") move on once a minute.
        TimelineView(.periodic(from: .now, by: 60)) { timeline in
            let board = QuestBoard.read(tasks: tasks, artifacts: artifacts, projects: projects, filter: model.filter,
                                        now: timeline.date)
            content(board)
        }
        .background(ChatPalette(scheme).well.color)
        .onAppear {
            if model.cache == nil { model.cache = MarcelCache(container: context.container) }
            if model.service == nil { model.service = LiveQuestService.paired() }
        }
        .confirmationDialog(
            model.pendingStop.map { "Stop \($0.title)?" } ?? "", isPresented: stopDialogShown,
            titleVisibility: .visible, presenting: model.pendingStop
        ) { _ in
            Button("Stop the quest", role: .destructive) { Task { await model.confirmStop() } }
            Button("Keep it running", role: .cancel) { model.cancelStop() }
        } message: { _ in
            Text("Marcel stops the session. It can't be picked up again.")
        }
    }

    private var stopDialogShown: Binding<Bool> {
        Binding(get: { model.pendingStop != nil }, set: { if !$0 { model.cancelStop() } })
    }

    @ViewBuilder
    private func content(_ board: QuestBoard) -> some View {
        VStack(spacing: 0) {
            QuestLogHeader { filterMenu(board) }
            List {
                if let problem = model.problem {
                    ProblemLine(text: problem) { model.problem = nil }
                        .questListRow()
                }
                if board.isEmpty {
                    EmptyLog(text: model.loadingArchived ? "Fetching archived quests…" : board.emptyText)
                        .questListRow()
                }
                ForEach(board.sections) { section in
                    let collapsible = board.startsCollapsed(section.group)
                    let open = !collapsible || model.showsFinished
                    SectionTitle(section: section, open: open,
                                 toggle: collapsible ? { model.showsFinished.toggle() } : nil)
                        .padding(.top, QuestLogMetrics.sectionGap)
                        .questListRow()
                    if open {
                        ForEach(section.rows) { row in
                            QuestRowView(row: row, busy: model.busy.contains(row.id))
                                .contentShape(Rectangle())
                                .onTapGesture { opened.append(row.id) }
                                .accessibilityAddTraits(.isButton)
                                .accessibilityHint("Opens the quest")
                                .accessibilityAction { opened.append(row.id) }
                                .questListRow()
                                .swipeActions(edge: .trailing, allowsFullSwipe: false) { swipes(for: row) }
                        }
                    }
                }
                ArchiveLink(archived: board.filter.archived) {
                    Task { await model.showArchived(!board.filter.archived) }
                }
                .padding(.top, QuestLogMetrics.sectionGap)
                .questListRow()
            }
            .listStyle(.plain)
            .scrollContentBackground(.hidden)
            .environment(\.defaultMinListRowHeight, 0)
            .refreshable { if model.filter.archived { await model.refreshArchived() } }
        }
    }

    @ViewBuilder
    private func swipes(for row: QuestRow) -> some View {
        if row.canStop {
            // Only asks; the quest stops when the confirmation is answered (`QuestsModel.confirmStop`).
            Button("Stop") { model.askToStop(row) }
                .tint(PixelColor.deep.color)
                .accessibilityLabel("Stop this quest")
        }
        if row.canArchive {
            Button("Archive") { Task { await model.archive(row.id) } }
                .tint(PixelColor.teal.color)
                .accessibilityLabel("Archive this quest")
        }
    }

    private func filterMenu(_ board: QuestBoard) -> some View {
        Menu {
            Picker("Project", selection: Binding(get: { model.filter.projectId }, set: { model.selectProject($0) })) {
                Text("All projects").tag(String?.none)
                ForEach(board.projects) { Text($0.name).tag(Optional($0.id)) }
            }
        } label: {
            FilterLabel(title: board.projectTitle)
        }
        .accessibilityLabel("Project: \(board.projectTitle)")
        .accessibilityHint("Pick a project")
    }
}

extension View {
    /// A list row drawn like the rest of the app: no separators, no system background, the log's own insets.
    fileprivate func questListRow() -> some View {
        self
            .listRowInsets(EdgeInsets(top: QuestLogMetrics.rowSpacing / 2, leading: QuestLogMetrics.inset.leading,
                                      bottom: QuestLogMetrics.rowSpacing / 2, trailing: QuestLogMetrics.inset.trailing))
            .listRowSeparator(.hidden)
            .listRowBackground(Color.clear)
    }
}

extension QuestBoard {
    /// Builds the board from cached rows. Rows that no longer decode are skipped: one bad row never blanks the log.
    public static func read(tasks: [CachedTask], artifacts: [CachedArtifact], projects: [CachedProject],
                            filter: QuestFilter, now: Date) -> QuestBoard {
        let decoder = QuestJSON.decoder()
        func rows<T: Decodable>(_ payloads: [Data], as _: T.Type) -> [T] {
            payloads.compactMap { try? decoder.decode(T.self, from: $0) }
        }
        return QuestBoard(
            tasks: rows(tasks.map(\.payload), as: HubTask.self),
            artifacts: rows(artifacts.map(\.payload), as: Artifact.self),
            projects: rows(projects.map(\.payload), as: Project.self),
            filter: filter, now: now)
    }

    /// Reads the cache straight from a context (tests, previews): the same rows the screen's `@Query`s hold.
    @MainActor
    public static func read(from context: ModelContext, filter: QuestFilter, now: Date) throws -> QuestBoard {
        read(tasks: try context.fetch(FetchDescriptor<CachedTask>()),
             artifacts: try context.fetch(FetchDescriptor<CachedArtifact>()),
             projects: try context.fetch(FetchDescriptor<CachedProject>()),
             filter: filter, now: now)
    }
}

/// The hub's JSON dates (RFC 3339, with or without fractions), as the cache stores them.
enum QuestJSON {
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

#Preview("Quest log") {
    let now = Date(timeIntervalSince1970: 1_791_000_000)
    QuestLogStill(board: QuestBoard(tasks: [], now: now), showsFinished: false)
}
