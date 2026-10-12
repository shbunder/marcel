import SwiftUI

/// The strip at the top of a screen: an avatar slot (AvatarKit's sprite goes here), a title and a status line, and on
/// the right the hearts with how many things need you. The hearts are decoration; VoiceOver reads the count.
public struct HeartsHeader<Avatar: View>: View {
    public var title: String
    public var subtitle: String?
    public var hearts: Int
    public var needsYou: Int
    public var avatar: Avatar

    @Environment(\.colorScheme) private var scheme
    @Environment(\.dynamicTypeSize) private var dynamicType

    public init(
        title: String, subtitle: String? = nil, hearts: Int = 3, needsYou: Int = 0,
        @ViewBuilder avatar: () -> Avatar
    ) {
        self.title = title
        self.subtitle = subtitle
        self.hearts = max(0, hearts)
        self.needsYou = max(0, needsYou)
        self.avatar = avatar()
    }

    /// "1 needs you", "2 need you", or nil when nothing waits.
    public var needsYouText: String? {
        switch needsYou {
        case 0: nil
        case 1: "1 needs you"
        default: "\(needsYou) need you"
        }
    }

    public var accessibilityText: String {
        var parts = [title]
        if let subtitle { parts.append(subtitle) }
        switch needsYou {
        case 0: break
        case 1: parts.append("1 thing needs you")
        default: parts.append("\(needsYou) things need you")
        }
        return parts.joined(separator: ". ")
    }

    public var body: some View {
        let theme = PixelTheme(scheme)
        // With accessibility text sizes the status line gets the full width under the title row.
        let stacked = dynamicType.isAccessibilitySize
        VStack(alignment: .leading, spacing: PixelMetrics.steps(2)) {
            HStack(alignment: .center, spacing: PixelMetrics.steps(5)) {
                avatar
                    .accessibilityHidden(true)
                VStack(alignment: .leading, spacing: PixelMetrics.steps(2)) {
                    Text(title)
                        .pixelText(.title)
                        .foregroundStyle(theme.text.color)
                    if !stacked { subtitleText(theme) }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                if hearts > 0 || needsYouText != nil {
                    VStack(alignment: .trailing, spacing: PixelMetrics.steps(2)) {
                        if hearts > 0 {
                            HStack(spacing: PixelMetrics.grid) {
                                ForEach(0..<hearts, id: \.self) { _ in
                                    PixelGlyph(.heart, color: theme.accent, pixel: PixelGlyph.pixel(for: dynamicType))
                                }
                            }
                        }
                        if let needsYouText {
                            Text(needsYouText)
                                .pixelText(.smallLabel)
                                .foregroundStyle(theme.accent.color)
                                .multilineTextAlignment(.trailing)
                        }
                    }
                    .fixedSize()
                }
            }
            if stacked { subtitleText(theme) }
        }
        .padding(.horizontal, PixelMetrics.steps(8))
        .padding(.top, PixelMetrics.steps(2))
        .padding(.bottom, PixelMetrics.steps(5))
        .frame(maxWidth: .infinity)
        .background(Rectangle().fill(theme.bar.color))
        .overlay(alignment: .bottom) {
            Rectangle().fill(theme.line.color).frame(height: PixelMetrics.thickBorder)
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(accessibilityText)
        .accessibilityAddTraits(.isHeader)
    }

    @ViewBuilder private func subtitleText(_ theme: PixelTheme) -> some View {
        if let subtitle {
            Text(subtitle)
                .pixelText(.detail)
                .foregroundStyle(theme.muted.color)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}

extension HeartsHeader where Avatar == EmptyView {
    public init(title: String, subtitle: String? = nil, hearts: Int = 3, needsYou: Int = 0) {
        self.init(title: title, subtitle: subtitle, hearts: hearts, needsYou: needsYou) { EmptyView() }
    }
}

#Preview("Hearts header") {
    PixelPreview { _ in
        HeartsHeader(title: "Marcel", subtitle: "Morning · nothing running", needsYou: 2) {
            PixelAvatarPlaceholder()
        }
    }
}
