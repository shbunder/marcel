import SwiftUI

// The chat's pieces (ADR-007, wireframe Q1 v10): your bubble with its notch, the four special-message cards, the time
// line between groups, and the quote box. Body text is the system font (Dynamic Type); the pixel font is only for
// card labels.

/// Your bubble's shape: rounded, with the bottom-right corner squared off (16, 16, 4, 16) so it reads "I said that".
public struct ChatBubbleShape: Shape {
    public static let radius: CGFloat = 16
    public static let notch: CGFloat = 4

    public init() {}

    public func path(in rect: CGRect) -> Path {
        UnevenRoundedRectangle(topLeadingRadius: Self.radius, bottomLeadingRadius: Self.radius,
                               bottomTrailingRadius: Self.notch, topTrailingRadius: Self.radius, style: .continuous)
            .path(in: rect)
    }
}

/// Your message: dark text on your helper's colour, in the notched bubble. The caller places it on the right.
public struct ChatBubble: View {
    public var text: String
    /// The helper's colour (its look, as drawn in light mode); nil for Marcel's rose.
    public var color: PixelColor?

    @Environment(\.colorScheme) private var scheme

    public init(_ text: String, color: PixelColor? = nil) {
        self.text = text
        self.color = color
    }

    public var body: some View {
        let palette = ChatPalette(scheme)
        Text(text)
            .font(.body)
            .foregroundStyle(palette.youInk.color)
            .fixedSize(horizontal: false, vertical: true)
            .padding(.horizontal, 14)
            .padding(.vertical, 10)
            .background(palette.bubble(color).color, in: ChatBubbleShape())
    }
}

/// A special message: a coloured frame on all four sides, a short pixel-font label with its symbol, the label's
/// detail on the right ("NUC · Sonnet", a project), and the card's content under it.
public struct ChatCard<Content: View>: View {
    public var kind: ChatCardKind
    public var label: String
    public var meta: String?
    public var content: Content

    @Environment(\.colorScheme) private var scheme

    public init(_ kind: ChatCardKind, label: String, meta: String? = nil, @ViewBuilder content: () -> Content) {
        self.kind = kind
        self.label = label
        self.meta = meta
        self.content = content()
    }

    public static var radius: CGFloat { 14 }
    public static var frameWidth: CGFloat { 2 }

    public var body: some View {
        let palette = ChatPalette(scheme)
        VStack(alignment: .leading, spacing: 6) {
            ViewThatFits(in: .horizontal) {
                HStack(alignment: .center, spacing: 6) {
                    labelView(palette)
                    Spacer(minLength: 8)
                    metaView(palette)
                }
                VStack(alignment: .leading, spacing: 2) {
                    labelView(palette)
                    metaView(palette)
                }
            }
            content
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(kind.ground(in: palette).color, in: RoundedRectangle(cornerRadius: Self.radius))
        .overlay(RoundedRectangle(cornerRadius: Self.radius)
            .strokeBorder(kind.frame(in: palette).color, lineWidth: Self.frameWidth))
    }

    private func labelView(_ palette: ChatPalette) -> some View {
        HStack(spacing: 5) {
            ChatGlyph(kind, color: kind.label(in: palette))
            PixelText(label, role: .smallLabel)
                .foregroundStyle(kind.label(in: palette).color)
                .fixedSize()
        }
    }

    @ViewBuilder private func metaView(_ palette: ChatPalette) -> some View {
        if let meta {
            PixelText(meta, role: .smallLabel)
                .foregroundStyle(palette.muted.color)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}

/// A button inside a special card: filled for the main answer ("Approve"), outlined for the others. 44 pt tall.
public struct ChatCardButton: View {
    public var title: String
    public var kind: ChatCardKind
    public var filled: Bool
    public var action: () -> Void

    @Environment(\.colorScheme) private var scheme
    @Environment(\.isEnabled) private var isEnabled

    public init(_ title: String, kind: ChatCardKind, filled: Bool = false, action: @escaping () -> Void) {
        self.title = title
        self.kind = kind
        self.filled = filled
        self.action = action
    }

    public var body: some View {
        let palette = ChatPalette(scheme)
        let ink = kind.label(in: palette)
        Button(action: action) {
            Text(title)
                .font(.subheadline.weight(.bold))
                .foregroundStyle((filled ? kind.buttonText(in: palette) : ink).color)
                .padding(.horizontal, 14)
                .frame(minHeight: PixelMetrics.minTapTarget)
                .background {
                    if filled {
                        RoundedRectangle(cornerRadius: 10).fill(ink.color)
                    } else {
                        RoundedRectangle(cornerRadius: 10).strokeBorder(kind.frame(in: palette).color, lineWidth: 1.5)
                    }
                }
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .opacity(isEnabled ? 1 : 0.45)
    }
}

/// The time between groups of messages ("Today 08:12"), small and centred.
public struct ChatTimeLine: View {
    public var text: String

    @Environment(\.colorScheme) private var scheme

    public init(_ text: String) {
        self.text = text
    }

    public var body: some View {
        Text(text)
            .font(.caption)
            .foregroundStyle(ChatPalette(scheme).muted.color)
            .frame(maxWidth: .infinity)
            .padding(.vertical, 2)
    }
}

/// A quoted message (the side chat's parent, B-08): who said it and the first lines, in a soft box.
public struct ChatQuote: View {
    public var speaker: String
    public var text: String
    public var lines: Int

    @Environment(\.colorScheme) private var scheme

    public init(speaker: String, text: String, lines: Int = 3) {
        self.speaker = speaker
        self.text = text
        self.lines = lines
    }

    public var body: some View {
        let palette = ChatPalette(scheme)
        VStack(alignment: .leading, spacing: 2) {
            Text(speaker)
                .font(.caption.weight(.bold))
                .foregroundStyle(palette.ink.color)
            Text(text)
                .font(.footnote)
                .foregroundStyle(palette.muted.color)
                .lineLimit(lines)
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 6)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(palette.well.color, in: RoundedRectangle(cornerRadius: 9))
        .overlay(RoundedRectangle(cornerRadius: 9).strokeBorder(palette.line.color, lineWidth: 1))
    }
}

/// What a "done" card shows of the item a quest made: a small document mark, the title and its first lines.
public struct ChatItemPreview: View {
    public var title: String
    public var lines: [String]
    public var kind: ChatCardKind

    @Environment(\.colorScheme) private var scheme

    public init(title: String, lines: [String], kind: ChatCardKind = .done) {
        self.title = title
        self.lines = lines
        self.kind = kind
    }

    public var body: some View {
        let palette = ChatPalette(scheme)
        HStack(alignment: .top, spacing: 10) {
            RoundedRectangle(cornerRadius: 3)
                .strokeBorder(kind.frame(in: palette).color, lineWidth: 1.5)
                .background(palette.card.color, in: RoundedRectangle(cornerRadius: 3))
                .frame(width: 22, height: 26)
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                    .font(.subheadline.weight(.bold))
                    .foregroundStyle(palette.ink.color)
                    .fixedSize(horizontal: false, vertical: true)
                ForEach(Array(lines.enumerated()), id: \.offset) { _, line in
                    Text(line)
                        .font(.footnote)
                        .foregroundStyle(palette.muted.color)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            Spacer(minLength: 0)
        }
        .padding(8)
        .background(palette.well.color, in: RoundedRectangle(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8).strokeBorder(palette.line.color, lineWidth: 1))
    }
}

/// The card labels' symbols, on the pixel grid: ▶ started, ! needs you, ★ done, ✕ failed. Decorative.
public struct ChatGlyph: View {
    public var kind: ChatCardKind
    public var color: PixelColor
    public var pixel: CGFloat

    public init(_ kind: ChatCardKind, color: PixelColor, pixel: CGFloat = PixelMetrics.grid) {
        self.kind = kind
        self.color = color
        self.pixel = pixel
    }

    static func rows(_ kind: ChatCardKind) -> [String] {
        switch kind {
        case .started: ["X..", "XX.", "XXX", "XX.", "X.."]
        case .needsYou: ["X", "X", "X", ".", "X"]
        case .done: ["..X..", ".XXX.", "XXXXX", ".X.X.", "X...X"]
        case .failed: ["X...X", ".X.X.", "..X..", ".X.X.", "X...X"]
        }
    }

    public var body: some View {
        let rows = Self.rows(kind)
        Canvas { context, _ in
            for (y, row) in rows.enumerated() {
                for (x, cell) in row.enumerated() where cell == "X" {
                    context.fill(Path(CGRect(x: CGFloat(x) * pixel, y: CGFloat(y) * pixel, width: pixel, height: pixel)),
                                 with: .color(color.color))
                }
            }
        }
        .frame(width: CGFloat(rows[0].count) * pixel, height: CGFloat(rows.count) * pixel)
        .accessibilityHidden(true)
    }
}

/// A small rounded tag inside a card (a quest's project), in muted text with a thin line.
public struct ChatTag: View {
    public var text: String

    @Environment(\.colorScheme) private var scheme

    public init(_ text: String) {
        self.text = text
    }

    public var body: some View {
        let palette = ChatPalette(scheme)
        Text(text)
            .font(.caption)
            .foregroundStyle(palette.muted.color)
            .padding(.horizontal, 8)
            .padding(.vertical, 2)
            .overlay(Capsule().strokeBorder(palette.line.color, lineWidth: 1))
    }
}
