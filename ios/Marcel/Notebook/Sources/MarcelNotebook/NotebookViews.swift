import PixelKit
import SwiftUI

/// Snapshot tests draw the editor's text as plain text (`ImageRenderer` draws a `TextEditor` as a yellow box).
extension EnvironmentValues {
    @Entry public var notebookStill = false
}

/// "‹ Marcel's notebook": a screen's top strip on white, with an optional second line.
struct NotebookHeader: View {
    var title: String
    var line: String?
    var back: () -> Void

    var body: some View {
        ChatHeader(title, subtitle: line, back: back)
    }
}

/// A titled box: the section's name in the accent colour ("ABOUT YOU"), its rows underneath.
struct NotebookCard<Content: View>: View {
    var title: String
    var content: Content

    @Environment(\.colorScheme) private var scheme

    init(_ title: String, @ViewBuilder content: () -> Content) {
        self.title = title
        self.content = content()
    }

    var body: some View {
        let theme = ChatPalette(scheme)
        VStack(alignment: .leading, spacing: PixelMetrics.steps(4)) {
            PixelText(title, role: .smallLabel)
                .foregroundStyle(theme.link.color)
                .fixedSize(horizontal: false, vertical: true)
                .accessibilityAddTraits(.isHeader)
            content
        }
        .foregroundStyle(theme.ink.color)
        .padding(PixelMetrics.padding + PixelMetrics.thinBorder)
        .frame(maxWidth: .infinity, alignment: .leading)
        .chatSurface()
        .accessibilityElement(children: .contain)
    }
}

/// A small rounded button with a pixel label, at least 44 pt tall; a selected one is filled in Marcel's rose.
struct NotebookButton: View {
    var title: String
    var destructive = false
    var enabled = true
    var selected = false
    var action: () -> Void

    @Environment(\.colorScheme) private var scheme

    var body: some View {
        let theme = ChatPalette(scheme)
        Button(action: action) {
            PixelText(title, role: .smallLabel)
                .foregroundStyle((destructive ? ChatCardKind.failed.label(in: theme)
                    : selected ? ChatCardKind.done.buttonText(in: theme) : theme.ink).color)
                .padding(.horizontal, 14)
                .frame(minHeight: PixelMetrics.minTapTarget)
                .background(selected ? ChatCardKind.done.label(in: theme).color : theme.card.color, in: Capsule())
                .overlay(Capsule().strokeBorder(destructive ? ChatCardKind.failed.label(in: theme).color
                    : selected ? Color.clear : theme.line.color, lineWidth: 1))
                .opacity(enabled ? 1 : 0.5)
        }
        .buttonStyle(.plain)
        .disabled(!enabled)
        .accessibilityAddTraits(selected ? .isSelected : [])
    }
}

/// One note in a section: its name, when it changed, and ▶. The whole row opens it.
struct NoteRowView: View {
    var title: String
    var detail: String
    var open: () -> Void

    @Environment(\.colorScheme) private var scheme

    var body: some View {
        let theme = ChatPalette(scheme)
        Button(action: open) {
            HStack(spacing: PixelMetrics.steps(4)) {
                VStack(alignment: .leading, spacing: PixelMetrics.steps(1)) {
                    Text(title).font(.body)
                        .foregroundStyle(theme.ink.color)
                        .fixedSize(horizontal: false, vertical: true)
                    Text(detail).font(.footnote)
                        .foregroundStyle(theme.muted.color)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer(minLength: 0)
                PixelGlyph(.pointer, color: theme.muted, pixel: 2)
                    .accessibilityHidden(true)
            }
            .frame(minHeight: PixelMetrics.minTapTarget)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(title), \(detail)")
        .accessibilityHint("Opens the note")
        .accessibilityAddTraits(.isButton)
    }
}

/// A plain sentence in the muted colour: "No notes yet", "Loading…".
struct NotebookNote: View {
    var text: String
    var accent = false

    @Environment(\.colorScheme) private var scheme

    var body: some View {
        let theme = ChatPalette(scheme)
        Text(text).font(.footnote)
            .foregroundStyle((accent ? theme.link : theme.muted).color)
            .fixedSize(horizontal: false, vertical: true)
            .frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// A problem or a notice at the top of a screen; tap to dismiss.
struct NotebookBanner: View {
    var text: String
    var problem = true
    var dismiss: () -> Void

    @Environment(\.colorScheme) private var scheme

    var body: some View {
        let theme = ChatPalette(scheme)
        let tone = problem ? theme.link : theme.ink
        Button(action: dismiss) {
            Text(text).font(.footnote)
                .foregroundStyle(tone.color)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(PixelMetrics.padding)
                .chatSurface()
        }
        .buttonStyle(.plain)
        .accessibilityLabel(problem ? "Problem: \(text)" : text)
        .accessibilityHint("Double-tap to dismiss")
    }
}
