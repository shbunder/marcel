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
                NotebookContent(model: model, rollovers: model.rollovers, open: { open = $0 })
                    .padding(PixelMetrics.padding)
            }
            .refreshable { await model.load() }
        }
        .background(PixelTheme(scheme).background.color)
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
    var rollovers: RolloverLog
    var open: (NoteRow) -> Void

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
                if rollovers.rollovers.isEmpty {
                    NotebookNote(text: NotebookText.noRollovers)
                } else {
                    ForEach(rollovers.rollovers) { rollover in
                        PixelText(model.text.rollover(rollover), role: .detail)
                            .fixedSize(horizontal: false, vertical: true)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                }
            }
        }
    }

    @ViewBuilder private var notes: some View {
        NotebookCard("About you") {
            ForEach(Array(model.aboutYouPreview.enumerated()), id: \.offset) { _, line in
                PixelText(line, role: .body)
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
