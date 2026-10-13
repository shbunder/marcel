import PixelKit
import SwiftUI

/// One line above a reply or a special card that answers an older message of yours (B-41): "↩ re: your 08:12
/// message". Tap it to scroll to that message, which lights up for a moment.
struct ReplyQuoteLine: View {
    var quote: ReplyQuote
    var jump: (String) -> Void

    @Environment(\.colorScheme) private var scheme
    @Environment(\.chatNow) private var now
    @Environment(\.calendar) private var calendar
    @Environment(\.timeZone) private var timeZone
    @Environment(\.locale) private var locale

    var text: String {
        var zoned = calendar
        zoned.timeZone = timeZone
        return quote.text(now: now ?? Date(), calendar: zoned, locale: locale)
    }

    var body: some View {
        let palette = ChatPalette(scheme)
        Button { jump(quote.messageId) } label: {
            HStack(spacing: 4) {
                Image(systemName: "arrow.uturn.left")
                    .font(.caption.weight(.semibold))
                    .accessibilityHidden(true)
                Text(text)
            }
            .font(.footnote)
            .foregroundStyle(palette.link.color)
            .frame(minHeight: 28, alignment: .leading)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Answers \(text.replacingOccurrences(of: "re: ", with: ""))")
        .accessibilityHint("Shows that message")
    }
}
