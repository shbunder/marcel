import SwiftUI

/// "You got: Energy comparison!" — the banner when a thread finishes with a new artifact (B-36). A dark plate with a
/// gold double border, the item's name in gold, and where it was saved underneath.
public struct ItemGetBanner: View {
    public var item: String
    public var savedTo: String?

    @Environment(\.colorScheme) private var scheme
    @Environment(\.dynamicTypeSize) private var dynamicType

    public init(item: String, savedTo: String? = nil) {
        self.item = item
        self.savedTo = savedTo
    }

    public var accessibilityText: String {
        ["You got: \(item)!", savedTo.map { "Saved to \($0)." }].compactMap { $0 }.joined(separator: " ")
    }

    public var body: some View {
        let theme = PixelTheme(scheme)
        VStack(spacing: PixelMetrics.steps(2)) {
            let label = { (text: String) in PixelText.text(text, role: .smallLabel, dynamicType: dynamicType) }
            Text("\(label("You got: "))\(label(item).foregroundStyle(theme.bannerHighlight.color))\(label("!"))")
                .lineSpacing(PixelTextRole.smallLabel.size(for: dynamicType) * 0.2)
            if let savedTo {
                PixelText("Saved to \(savedTo)", role: .smallLabel, capitals: false)
            }
        }
        .foregroundStyle(theme.onBanner.color)
        .multilineTextAlignment(.center)
        .fixedSize(horizontal: false, vertical: true)
        .frame(maxWidth: .infinity)
        .padding(.horizontal, PixelMetrics.padding)
        .padding(.vertical, PixelMetrics.padding)
        .background(Rectangle().fill(theme.banner.color))
        .overlay(DoubleBorder(color: theme.bannerHighlight))
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(accessibilityText)
    }
}

/// A CSS-style `double` border on the pixel grid: a 2-pt line, a 2-pt gap, a 2-pt line.
struct DoubleBorder: View {
    var color: PixelColor

    var body: some View {
        ZStack {
            Rectangle().strokeBorder(color.color, lineWidth: PixelMetrics.grid)
            Rectangle().strokeBorder(color.color, lineWidth: PixelMetrics.grid)
                .padding(PixelMetrics.steps(2))
        }
    }
}

#Preview("Item get") {
    PixelPreview { _ in ItemGetBanner(item: "Energy comparison", savedTo: "Home energy") }
}
