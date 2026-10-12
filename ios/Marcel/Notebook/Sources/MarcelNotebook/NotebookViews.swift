import PixelKit
import SwiftUI

/// Snapshot tests draw the editor's text as plain text (`ImageRenderer` draws a `TextEditor` as a yellow box).
extension EnvironmentValues {
    @Entry public var notebookStill = false
}

/// "◀ Marcel's notebook": a screen's top strip, with an optional second line.
struct NotebookHeader: View {
    var title: String
    var line: String?
    var back: () -> Void

    @Environment(\.colorScheme) private var scheme

    var body: some View {
        let theme = PixelTheme(scheme)
        HStack(spacing: PixelMetrics.steps(3)) {
            Button(action: back) {
                PixelGlyph(.pointer, color: theme.text, pixel: 3)
                    .scaleEffect(x: -1)
                    .frame(width: PixelMetrics.minTapTarget, height: PixelMetrics.minTapTarget)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Back")
            VStack(alignment: .leading, spacing: PixelMetrics.steps(1)) {
                PixelText(title, role: .title)
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityAddTraits(.isHeader)
                if let line {
                    PixelText(line, role: .detail)
                        .foregroundStyle(theme.muted.color)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            .foregroundStyle(theme.text.color)
            Spacer(minLength: 0)
        }
        .padding(.leading, PixelMetrics.steps(1))
        .padding(.trailing, PixelMetrics.padding)
        .padding(.vertical, PixelMetrics.steps(3))
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(theme.bar.color)
        .overlay(alignment: .bottom) {
            Rectangle().fill(theme.line.color).frame(height: PixelMetrics.thinBorder)
        }
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
        let theme = PixelTheme(scheme)
        VStack(alignment: .leading, spacing: PixelMetrics.steps(4)) {
            PixelText(title, role: .smallLabel)
                .foregroundStyle(theme.accent.color)
                .fixedSize(horizontal: false, vertical: true)
                .accessibilityAddTraits(.isHeader)
            content
        }
        .foregroundStyle(theme.text.color)
        .padding(PixelMetrics.padding + PixelMetrics.thinBorder)
        .frame(maxWidth: .infinity, alignment: .leading)
        .pixelBox(fill: theme.dialog, line: theme.line, shadow: theme.shadow, border: PixelMetrics.thinBorder,
                  offset: PixelMetrics.smallShadow)
        .accessibilityElement(children: .contain)
    }
}

/// A pixel button: a small boxed label, at least 44 pt tall.
struct NotebookButton: View {
    var title: String
    var destructive = false
    var enabled = true
    var selected = false
    var action: () -> Void

    @Environment(\.colorScheme) private var scheme

    var body: some View {
        let theme = PixelTheme(scheme)
        Button(action: action) {
            PixelText(title, role: .smallLabel)
                .foregroundStyle((destructive ? theme.accent : selected ? theme.onHeader : theme.text).color)
                .padding(.horizontal, PixelMetrics.steps(5))
                .frame(minHeight: PixelMetrics.minTapTarget)
                .pixelBox(fill: selected ? theme.header : theme.card, line: destructive ? theme.accent : theme.line,
                          shadow: theme.shadow, border: PixelMetrics.thinBorder, offset: PixelMetrics.smallShadow)
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
        let theme = PixelTheme(scheme)
        Button(action: open) {
            HStack(spacing: PixelMetrics.steps(4)) {
                VStack(alignment: .leading, spacing: PixelMetrics.steps(1)) {
                    PixelText(title, role: .body)
                        .foregroundStyle(theme.text.color)
                        .fixedSize(horizontal: false, vertical: true)
                    PixelText(detail, role: .detail)
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
        let theme = PixelTheme(scheme)
        PixelText(text, role: .detail)
            .foregroundStyle((accent ? theme.accent : theme.muted).color)
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
        let theme = PixelTheme(scheme)
        let tone = problem ? theme.accent : theme.text
        Button(action: dismiss) {
            PixelText(text, role: .detail)
                .foregroundStyle(tone.color)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(PixelMetrics.padding)
                .pixelBox(fill: theme.card, line: problem ? theme.accent : theme.line, shadow: nil,
                          border: PixelMetrics.thinBorder, offset: 0)
        }
        .buttonStyle(.plain)
        .accessibilityLabel(problem ? "Problem: \(text)" : text)
        .accessibilityHint("Double-tap to dismiss")
    }
}
