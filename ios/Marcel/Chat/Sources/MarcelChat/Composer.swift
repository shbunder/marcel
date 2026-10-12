import PixelKit
import SwiftUI

/// Where you talk to Marcel: a pixel box with the text field and a ▶ send button. It is never disabled; only an
/// empty message cannot be sent. While a question waits for you it says "Or say something…" (wireframe 3B).
struct Composer: View {
    @Binding var text: String
    var placeholder: String
    var focused: FocusState<Bool>.Binding?
    var send: () -> Void

    @Environment(\.colorScheme) private var scheme
    @Environment(\.chatStillAvatars) private var still

    static func placeholder(questionWaiting: Bool) -> String {
        questionWaiting ? "Or say something…" : "Talk to Marcel…"
    }

    private var canSend: Bool { !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }

    var body: some View {
        let theme = PixelTheme(scheme)
        HStack(alignment: .center, spacing: PixelMetrics.steps(3)) {
            field(theme)
            Button(action: send) {
                PixelGlyph(.pointer, color: canSend ? theme.accent : theme.muted,
                           pixel: 3)
                    .frame(width: PixelMetrics.minTapTarget, height: PixelMetrics.minTapTarget)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .disabled(!canSend)
            .accessibilityLabel("Send")
        }
        .padding(.leading, PixelMetrics.padding)
        .padding(.vertical, PixelMetrics.steps(1))
        .pixelBox(fill: theme.dialog, line: theme.line, shadow: nil)
        .padding(.horizontal, FeedMetrics.inset)
        .padding(.top, PixelMetrics.steps(2))
        .padding(.bottom, PixelMetrics.steps(4))
    }

    @ViewBuilder private func field(_ theme: PixelTheme) -> some View {
        if still {
            // Snapshots: `ImageRenderer` cannot draw a text field, so draw what it shows.
            PixelText(text.isEmpty ? placeholder : text, role: .body)
                .foregroundStyle((text.isEmpty ? theme.muted : theme.text).color)
                .frame(maxWidth: .infinity, alignment: .leading)
        } else {
            editable(theme)
        }
    }

    @ViewBuilder private func editable(_ theme: PixelTheme) -> some View {
        let base = TextField(text: $text, axis: .vertical) {
            Text(placeholder).foregroundStyle(theme.muted.color)
        }
        .lineLimit(1...5)
        .pixelText(.body)
        .foregroundStyle(theme.text.color)
        .tint(theme.accent.color)
        .accessibilityLabel("Message to Marcel")
        if let focused {
            base.focused(focused)
        } else {
            base
        }
    }
}
