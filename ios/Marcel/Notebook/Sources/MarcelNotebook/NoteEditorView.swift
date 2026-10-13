import PixelKit
import SwiftUI

/// One note: read it (the preview), edit it, save it. A note changed elsewhere since it was opened is never
/// overwritten without the person choosing to: the hub's version and a line-by-line diff are shown first.
struct NoteEditorView: View {
    @State var model: NoteEditorModel
    @Environment(\.dismiss) private var dismiss
    @Environment(\.colorScheme) private var scheme

    var body: some View {
        VStack(spacing: 0) {
            NotebookHeader(title: model.title, line: model.state == .ready ? model.line : nil, back: { dismiss() })
            ScrollView {
                NoteEditorContent(model: model, actions: actions)
                    .padding(PixelMetrics.padding)
            }
            .scrollDismissesKeyboard(.interactively)
        }
        .background(ChatPalette(scheme).well.color)
        .toolbar(.hidden, for: .navigationBar)
        .task { if model.state == .loading { await model.load() } }
    }

    private var actions: NoteEditorActions {
        var actions = NoteEditorActions()
        actions.save = { Task { await model.save() } }
        actions.reload = { model.reloadTheirs() }
        actions.saveMine = { Task { await model.saveMineOverTheirs() } }
        actions.discard = { model.discardChanges() }
        actions.retry = { Task { await model.load() } }
        return actions
    }
}

/// What the editor's buttons do. Snapshots pass the empty one.
struct NoteEditorActions {
    var save: () -> Void = {}
    var reload: () -> Void = {}
    var saveMine: () -> Void = {}
    var discard: () -> Void = {}
    var retry: () -> Void = {}
}

/// The editor's body: notices, "changed elsewhere", Edit / Preview, the text or its preview, and Save.
struct NoteEditorContent: View {
    @Bindable var model: NoteEditorModel
    var actions: NoteEditorActions

    @State private var confirming: Confirm?
    @Environment(\.notebookStill) private var still
    @Environment(\.colorScheme) private var scheme

    enum Confirm: String, Identifiable {
        case reload, discard
        var id: String { rawValue }

        var title: String {
            switch self {
            case .reload: "Load the hub's version? Your changes to this note are thrown away."
            case .discard: "Throw away your changes to this note?"
            }
        }

        var button: String {
            switch self {
            case .reload: "Load the hub's version"
            case .discard: "Throw away"
            }
        }
    }

    var body: some View {
        let theme = ChatPalette(scheme)
        VStack(alignment: .leading, spacing: PixelMetrics.steps(6)) {
            switch model.state {
            case .loading:
                NotebookNote(text: "Loading…")
            case .failed(let words):
                NotebookNote(text: words, accent: true)
                NotebookButton(title: "Try again", action: actions.retry)
            case .ready:
                if let problem = model.problem {
                    NotebookBanner(text: problem) { model.problem = nil }
                }
                if let notice = model.notice {
                    NotebookBanner(text: notice, problem: false) { model.notice = nil }
                }
                if let theirs = model.conflict {
                    ConflictCard(text: model.words.changedElsewhere(theirs), diff: model.diff,
                                 reload: { confirming = .reload }, saveMine: actions.saveMine)
                }
                HStack(spacing: PixelMetrics.steps(4)) {
                    NotebookButton(title: "Edit", selected: model.mode == .edit) { model.mode = .edit }
                    NotebookButton(title: "Preview", selected: model.mode == .preview) { model.mode = .preview }
                    Spacer(minLength: 0)
                }
                switch model.mode {
                case .edit: editor(theme)
                case .preview: MarkdownView(parts: ItemMarkdown.parse(model.text), style: .chat)
                }
                ViewThatFits(in: .horizontal) {
                    HStack(spacing: PixelMetrics.steps(4)) { buttons }
                    VStack(alignment: .leading, spacing: PixelMetrics.steps(4)) { buttons }
                }
            }
        }
        .confirmationDialog(confirming?.title ?? "", isPresented: Binding(
            get: { confirming != nil }, set: { if !$0 { confirming = nil } }
        ), titleVisibility: .visible, presenting: confirming) { confirm in
            Button(confirm.button, role: .destructive) {
                switch confirm {
                case .reload: actions.reload()
                case .discard: actions.discard()
                }
            }
            Button("Cancel", role: .cancel) {}
        }
    }

    @ViewBuilder private var buttons: some View {
        NotebookButton(title: model.saving ? "Saving…" : "Save", enabled: model.canSave, action: actions.save)
            .accessibilityHint(model.conflict != nil ? "Choose a version first" : "")
        if model.hasChanges && model.conflict == nil {
            NotebookButton(title: "Throw away changes", destructive: true) { confirming = .discard }
        }
    }

    @ViewBuilder private func editor(_ theme: ChatPalette) -> some View {
        Group {
            if still {
                Text(model.text).font(.body)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: .infinity, minHeight: 120, alignment: .topLeading)
            } else {
                TextEditor(text: $model.text)
                    .pixelText(.body, capitals: false)
                    .scrollContentBackground(.hidden)
                    .frame(minHeight: 320)
                    .accessibilityLabel("Text of \(model.title)")
            }
        }
        .foregroundStyle(theme.ink.color)
        .padding(PixelMetrics.steps(4))
        .chatSurface()
    }
}

/// "Changed elsewhere": who changed it and when, the hub's lines against yours, and the two ways on.
struct ConflictCard: View {
    var text: String
    var diff: [LineDiff.Line]
    var reload: () -> Void
    var saveMine: () -> Void

    @Environment(\.colorScheme) private var scheme

    var body: some View {
        let theme = ChatPalette(scheme)
        NotebookCard("Changed elsewhere") {
            Text(text).font(.footnote)
                .fixedSize(horizontal: false, vertical: true)
            HStack(spacing: PixelMetrics.steps(5)) {
                key("−", "the hub's version", ProjectChip.Tone.blue.color(in: PixelTheme(scheme)))
                key("+", "yours", ProjectChip.Tone.green.color(in: PixelTheme(scheme)))
            }
            .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: PixelMetrics.steps(1)) {
                if diff.isEmpty {
                    NotebookNote(text: "The words are the same in both.")
                }
                ForEach(Array(diff.enumerated()), id: \.offset) { _, line in
                    DiffLineView(line: line)
                }
            }
            ViewThatFits(in: .horizontal) {
                HStack(spacing: PixelMetrics.steps(4)) { buttons }
                VStack(alignment: .leading, spacing: PixelMetrics.steps(4)) { buttons }
            }
        }
    }

    @ViewBuilder private var buttons: some View {
        NotebookButton(title: "Reload theirs", action: reload)
            .accessibilityHint("Shows the hub's version and throws your changes away")
        NotebookButton(title: "Save mine over it", destructive: true, action: saveMine)
            .accessibilityHint("Replaces the hub's version with yours")
    }

    private func key(_ mark: String, _ label: String, _ color: PixelColor) -> some View {
        Text("\(mark) \(label)").font(.footnote)
            .foregroundStyle(color.color)
    }
}

/// One line of the diff: "−" and the hub's colour, "+" and yours, or the same in both.
struct DiffLineView: View {
    var line: LineDiff.Line

    @Environment(\.colorScheme) private var scheme

    var body: some View {
        let theme = ChatPalette(scheme)
        let (mark, words, color, fill): (String, String, PixelColor, PixelColor?) = switch line {
        case .same(let text): (" ", text, theme.muted, nil)
        case .theirs(let text): ("−", text, ProjectChip.Tone.blue.color(in: PixelTheme(scheme)), theme.card)
        case .yours(let text): ("+", text, ProjectChip.Tone.green.color(in: PixelTheme(scheme)), theme.card)
        case .folded: ("", line.spoken, theme.muted, nil)
        }
        HStack(alignment: .firstTextBaseline, spacing: PixelMetrics.steps(3)) {
            Text(mark)
                .font(.system(.footnote, design: .monospaced))
                .frame(minWidth: 10, alignment: .leading)
            Text(words.isEmpty ? " " : words)
                .font(.system(.footnote, design: .monospaced))
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
        .foregroundStyle(color.color)
        .padding(.horizontal, PixelMetrics.steps(2))
        .padding(.vertical, PixelMetrics.steps(1))
        .background(fill?.color ?? .clear)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(line.spoken)
    }
}
