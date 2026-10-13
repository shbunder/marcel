import PixelKit
import SwiftUI

/// Marcel's notebook (wireframe 6B, B-20): "About you", project notes, dated notes, and what Marcel carries across
/// rollovers. Opened from the avatar header in the chat. Put it in a `NavigationStack`: a note opens on top of it.
public struct NotebookView: View {
    @State private var model: NotebookModel
    @State private var open: NoteRow?
    @Environment(\.dismiss) private var dismiss
    @Environment(\.colorScheme) private var scheme

    public init(model: NotebookModel) {
        _model = State(initialValue: model)
    }

    public var body: some View {
        VStack(spacing: 0) {
            NotebookHeader(title: "Marcel's notebook", back: { dismiss() })
            ScrollView {
                NotebookContent(model: model, open: { open = $0 })
                    .padding(PixelMetrics.padding)
            }
            .refreshable { await model.load() }
        }
        .background(ChatPalette(scheme).well.color)
        .toolbar(.hidden, for: .navigationBar)
        .navigationDestination(item: $open) { row in
            NoteEditorView(model: model.editor(for: row))
                .onDisappear { model.refreshDrafts() }
        }
        .task { await model.load() }
    }
}

/// The sections, top to bottom. The live screen puts it in a scroll view; snapshots draw it as it is.
struct NotebookContent: View {
    @Bindable var model: NotebookModel
    var open: (NoteRow) -> Void

    @State private var confirmingFreshStart = false

    var body: some View {
        VStack(alignment: .leading, spacing: PixelMetrics.steps(7)) {
            if let problem = model.problem {
                NotebookBanner(text: problem) { model.problem = nil }
            }
            switch model.state {
            case .loading:
                NotebookNote(text: "Loading…")
            case .notAvailableYet:
                NotebookNote(text: NotebookError.notAvailableYet.errorDescription ?? "")
            case .failed(let words):
                NotebookNote(text: words, accent: true)
            case .loaded:
                notes
            }
            NotebookCard("What Marcel carries") {
                let carried = model.carried
                if carried.isEmpty {
                    NotebookNote(text: NotebookText.noRollovers)
                } else {
                    ForEach(carried) { rollover in
                        Text(model.text.rollover(rollover)).font(.footnote)
                            .fixedSize(horizontal: false, vertical: true)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                }
                if let note = model.rolloversNote {
                    NotebookNote(text: note, accent: true)
                }
                NotebookButton(title: "Fresh start", enabled: model.canStartFresh) { confirmingFreshStart = true }
                    .accessibilityHint("Asks first. Gives Marcel a new session; your chat stays as it is.")
                if let line = model.freshStartLine {
                    NotebookNote(text: line, accent: { if case .failed = model.freshStart { true } else { false } }())
                }
            }
            .confirmationDialog("Give Marcel a fresh start?", isPresented: $confirmingFreshStart,
                                titleVisibility: .visible) {
                Button("Fresh start") { Task { await model.startFresh() } }
                Button("Cancel", role: .cancel) {}
            } message: {
                Text(NotebookText.freshStartConfirm)
            }
        }
    }

    @ViewBuilder private var notes: some View {
        NotebookCard("About you") {
            ForEach(Array(model.aboutYouPreview.enumerated()), id: \.offset) { _, line in
                Text(line).font(.body)
                    .fixedSize(horizontal: false, vertical: true)
            }
            NoteRowView(title: model.aboutYouPreview.isEmpty ? "What Marcel knows about you" : "Open and edit",
                        detail: model.detail(model.aboutYou)) { open(model.aboutYou) }
        }
        NotebookCard("Project notes") {
            if model.projects.isEmpty {
                NotebookNote(text: "No projects yet. Every thread in a project starts with its notes.")
            }
            ForEach(model.projects) { row in
                NoteRowView(title: row.title, detail: model.detail(row)) { open(row) }
            }
        }
        NotebookCard("Dated notes") {
            if model.dated.isEmpty {
                NotebookNote(text: "No dated notes yet. Marcel writes one on days something is worth remembering.")
            }
            ForEach(model.dated) { row in
                NoteRowView(title: row.title, detail: model.detail(row)) { open(row) }
            }
        }
        if !model.others.isEmpty {
            NotebookCard("Other notes") {
                ForEach(model.others) { row in
                    NoteRowView(title: row.title, detail: model.detail(row)) { open(row) }
                }
            }
        }
        if model.refused > 0 {
            NotebookNote(text: model.refused == 1 ? "1 note has a name the app won't open."
                         : "\(model.refused) notes have names the app won't open.")
        }
    }
}
