import MarcelKit
import PixelKit
import SwiftData
import SwiftUI

extension EnvironmentValues {
    /// The cache the app's live event stream writes into. The quest view listens to it for `task.event` and approval
    /// frames, which the cache does not store. The app sets it once at the root (`RootView`); without it the view
    /// still shows the history and refreshes when pulled.
    @Entry public var liveEvents: MarcelCache? = nil
}

/// Inside one quest (wireframe 2A, in the chat's language since S-18.4): its steps first, then the live detail folded away (text, tool rows that open,
/// diffs in colour, approval prompts you answer in place), a composer that talks to this quest only, and the actions:
/// stop, hand back to Marcel, move to another project, open in Claude.
///
/// The transcript is `GET /tasks/{id}/events` merged with the live stream's `task.event` frames by `seq`, so nothing
/// shows twice and a skipped event is fetched. The list is lazy, so a quest with thousands of events opens at once.
/// Opened from the Chat and Quests tabs; the back button dismisses it however it was shown.
public struct QuestView: View {
    let taskId: String
    @State private var model: QuestViewModel
    @Query private var projectRows: [CachedProject]
    @Query private var taskRows: [CachedTask]
    @State private var confirmingStop = false
    @State private var handingBack = false
    @State private var handBackNote = ""
    @State private var observer: UUID?
    @Environment(\.modelContext) private var context
    @Environment(\.liveEvents) private var live
    @Environment(\.dismiss) private var dismiss
    @Environment(\.openURL) private var openURL
    @Environment(\.colorScheme) private var scheme

    /// - Parameter service: the hub; nil reads the paired hub from pairing's stores (address and Keychain token).
    public init(taskId: String, service: (any QuestViewService)? = nil) {
        self.taskId = taskId
        _model = State(initialValue: QuestViewModel(taskId: taskId, service: service))
        _taskRows = Query(filter: #Predicate<CachedTask> { $0.id == taskId })
        _projectRows = Query(filter: #Predicate<CachedProject> { !$0.archived }, sort: \CachedProject.updatedAt,
                             order: .reverse)
    }

    public var body: some View {
        let projects = QuestProjects(rows: projectRows)
        let task = model.task ?? QuestProjects.decode(HubTask.self, taskRows.first?.payload)
        VStack(spacing: 0) {
            QuestHeader(title: task?.title ?? "Quest", stop: model.canStop ? { confirmingStop = true } : nil,
                        back: { dismiss() })
            QuestScroll(model: model, meta: QuestMetaLine.text(
                project: task.flatMap { projects.name(of: $0.projectId) }, place: task.map(QuestTone.place),
                state: task.map { QuestTone.badge($0.state) }))
            QuestToolbar(
                busy: model.busy, claudeURL: model.claudeURL,
                handBack: { handingBack = true },
                open: { openURL($0) }
            ) {
                moveMenu(projects, current: task?.projectId)
            }
            QuestComposerBox(model: model)
        }
        .background(ChatPalette(scheme).well.color)
        .toolbar(.hidden, for: .navigationBar)
        .task {
            if model.cache == nil { model.cache = MarcelCache(container: context.container) }
            if model.service == nil { model.service = LiveQuestViewService.paired() }
            if let live, observer == nil {
                observer = live.observeFrames { [model] frame in model.receive(frame) }
            }
            await model.load()
        }
        .onDisappear {
            if let observer { live?.stopObservingFrames(observer) }
            observer = nil
        }
        .confirmationDialog("Stop \(task?.title ?? "this quest")?", isPresented: $confirmingStop,
                            titleVisibility: .visible) {
            Button("Stop the quest", role: .destructive) { Task { await model.stop() } }
            Button("Keep it running", role: .cancel) {}
        } message: {
            Text("Marcel stops the session. It can't be picked up again.")
        }
        .alert("Hand back to Marcel?", isPresented: $handingBack) {
            TextField("What should Marcel know? (optional)", text: $handBackNote)
            Button("Hand back") {
                let note = handBackNote
                handBackNote = ""
                Task { await model.handBack(note: note) }
            }
            Button("Cancel", role: .cancel) { handBackNote = "" }
        } message: {
            Text("Marcel reads the quest's report and decides what happens next.")
        }
    }

    private func moveMenu(_ projects: QuestProjects, current: String?) -> some View {
        let theme = PixelTheme(scheme)
        let others = projects.list.filter { $0.id != current }
        return Menu {
            ForEach(others, id: \.id) { project in
                Button(project.name) { Task { await model.move(to: project.id) } }
            }
        } label: {
            QuestChip(title: "Move to project", ink: ProjectChip.Tone.gold.color(in: theme))
        }
        .disabled(others.isEmpty)
        .accessibilityLabel("Move to another project")
    }
}

/// The scrolling part: problem, steps, and the live detail in a lazy stack, so only the rows on screen are built.
struct QuestScroll: View {
    let model: QuestViewModel
    var meta: String

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: QuestMetrics.rowGap) {
                QuestBody(model: model, meta: meta)
            }
            .padding(QuestMetrics.inset)
        }
        .refreshable { await model.load() }
    }
}

/// The rows of the quest, shared by the screen (inside a lazy stack) and the snapshot still (inside a plain one).
struct QuestBody: View {
    let model: QuestViewModel
    /// "HOME ENERGY · NUC · SONNET" (`QuestMetaLine`).
    var meta: String

    var body: some View {
        @Bindable var model = model
        if let problem = model.problem {
            QuestProblem(text: problem) { model.problem = nil }
        }
        if !meta.isEmpty { QuestMetaLine(text: meta) }
        if model.steps.isEmpty {
            QuestNote(text: model.loading ? "Reading the quest…" : "No steps reported.")
        } else {
            StepsList(steps: model.steps)
        }
        QuestSectionTitle(title: "Live detail", open: model.detailOpen) { model.detailOpen.toggle() }
        if model.detailOpen {
            if model.rows.isEmpty, model.pending.isEmpty, !model.loading {
                QuestNote(text: "Nothing in the transcript yet.")
            }
            let actions = DetailActions(
                toggle: { id in
                    if model.expanded.contains(id) { model.expanded.remove(id) } else { model.expanded.insert(id) }
                },
                answer: { id, approve in Task { await model.answer(id, approve: approve) } })
            ForEach(model.rows) { row in
                DetailRowView(row: row, expanded: model.expanded.contains(row.id), actions: actions)
            }
            ForEach(model.pending) { note in
                PendingNoteView(note: note)
            }
        }
    }
}

/// A muted line of words.
struct QuestNote: View {
    var text: String
    @Environment(\.colorScheme) private var scheme

    var body: some View {
        Text(text)
            .font(.subheadline)
            .foregroundStyle(ChatPalette(scheme).muted.color)
            .fixedSize(horizontal: false, vertical: true)
    }
}

/// The composer in its own view, so typing redraws only the composer, not thousands of rows.
struct QuestComposerBox: View {
    let model: QuestViewModel

    var body: some View {
        @Bindable var model = model
        QuestComposer(text: $model.draft) { Task { await model.send() } }
    }
}

/// The projects a quest can be moved to, read from the cache.
struct QuestProjects {
    var list: [Project]

    init(rows: [CachedProject]) {
        list = rows.compactMap { Self.decode(Project.self, $0.payload) }
    }

    init(_ list: [Project]) {
        self.list = list
    }

    func name(of id: String) -> String? { list.first { $0.id == id }?.name }

    static func decode<T: Decodable>(_: T.Type, _ data: Data?) -> T? {
        guard let data else { return nil }
        return try? QuestJSON.decoder().decode(T.self, from: data)
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
                throw DecodingError.dataCorruptedError(in: container, debugDescription: "Not an RFC 3339 date: \(text)")
            }
            return date
        }
        return decoder
    }
}

/// The quest drawn in a plain stack for the snapshot tests: `ImageRenderer` draws nothing inside a `ScrollView`.
struct QuestStill: View {
    let model: QuestViewModel
    var title: String
    var project: String?
    var showsToolbar = true

    @Environment(\.colorScheme) private var scheme

    var body: some View {
        let task = model.task
        VStack(spacing: 0) {
            QuestHeader(title: title, stop: showsToolbar && model.canStop ? {} : nil, back: {})
            VStack(alignment: .leading, spacing: QuestMetrics.rowGap) {
                QuestBody(model: model, meta: QuestMetaLine.text(project: project, place: task.map(QuestTone.place),
                                                                 state: task.map { QuestTone.badge($0.state) }))
            }
            .padding(QuestMetrics.inset)
            .frame(maxWidth: .infinity, alignment: .leading)
            if showsToolbar {
                QuestToolbar(busy: model.busy, claudeURL: model.claudeURL, handBack: {}, open: { _ in }) {
                    QuestChip(title: "Move to project", ink: ProjectChip.Tone.gold.color(in: PixelTheme(scheme)))
                }
                QuestComposerBox(model: model)
            }
        }
        .environment(\.questStill, true)
        .background(ChatPalette(scheme).well.color)
    }
}
