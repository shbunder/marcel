import SwiftUI

/// The colour roles of the pixel-classic look, in a light and a dark variant. Components read
/// `PixelTheme(colorScheme)`; nothing in PixelKit uses a colour that is not on this list or on `StateBadge.Kind` /
/// `ProjectChip.Tone`, which is what lets `ContrastTests` check every pair of text and background.
public struct PixelTheme: Sendable, Equatable {
    /// The screen behind everything.
    public var background: PixelColor
    /// The header and tab bar strips.
    public var bar: PixelColor
    /// Inside a dialogue box and an artifact card.
    public var dialog: PixelColor
    /// Inside quest cards and tiles.
    public var card: PixelColor
    /// Main text.
    public var text: PixelColor
    /// Secondary text: subtitles, step counts, unselected tabs.
    public var muted: PixelColor
    /// Hard borders.
    public var line: PixelColor
    /// The offset "pixel" shadow under boxes.
    public var shadow: PixelColor
    /// Speaker labels, hearts, the selected tab, the choice marker.
    public var accent: PixelColor
    /// Progress bar track and fill.
    public var track: PixelColor
    public var fill: PixelColor
    /// The artifact card header: gold with dark text in both themes.
    public var header: PixelColor
    public var onHeader: PixelColor
    /// The "You got" banner: a dark plate, light text, gold title and gold double border.
    public var banner: PixelColor
    public var onBanner: PixelColor
    public var bannerHighlight: PixelColor

    public static let light = PixelTheme(
        background: .cream,
        bar: PixelColor(0xEFE4D6),
        dialog: PixelColor(0xFFFFFF),
        card: PixelColor(0xFFFAF2),
        text: .ink,
        muted: PixelColor(0x6F5F63),
        line: .ink,
        shadow: .ink,
        accent: .deep,
        track: PixelColor(0xF0E2D6),
        fill: .rose,
        header: .gold,
        onHeader: .ink,
        banner: .ink,
        onBanner: .cream,
        bannerHighlight: .gold
    )

    public static let dark = PixelTheme(
        background: PixelColor(0x1D1719),
        bar: PixelColor(0x2A2124),
        dialog: PixelColor(0x2B2226),
        card: PixelColor(0x251D20),
        text: PixelColor(0xF2E8E1),
        muted: PixelColor(0xB8A7A2),
        line: PixelColor(0xE9DCCD),
        shadow: PixelColor(0x6B2A3E),
        accent: .rose,
        track: PixelColor(0x3A2F33),
        fill: .rose,
        header: .gold,
        onHeader: .ink,
        banner: PixelColor(0x120D10),
        onBanner: .cream,
        bannerHighlight: .gold
    )

    public init(_ scheme: ColorScheme) {
        self = scheme == .dark ? .dark : .light
    }

    public init(
        background: PixelColor, bar: PixelColor, dialog: PixelColor, card: PixelColor, text: PixelColor,
        muted: PixelColor, line: PixelColor, shadow: PixelColor, accent: PixelColor, track: PixelColor,
        fill: PixelColor, header: PixelColor, onHeader: PixelColor, banner: PixelColor, onBanner: PixelColor,
        bannerHighlight: PixelColor
    ) {
        self.background = background
        self.bar = bar
        self.dialog = dialog
        self.card = card
        self.text = text
        self.muted = muted
        self.line = line
        self.shadow = shadow
        self.accent = accent
        self.track = track
        self.fill = fill
        self.header = header
        self.onHeader = onHeader
        self.banner = banner
        self.onBanner = onBanner
        self.bannerHighlight = bannerHighlight
    }

    /// Every place a component puts text on a colour of this theme, by name. `ContrastTests` holds each one to
    /// WCAG AA for normal-size text (4.5:1): the pixel fonts are small, so no pair gets the large-text allowance.
    public var textPairs: [(name: String, text: PixelColor, on: PixelColor)] {
        var pairs: [(String, PixelColor, PixelColor)] = [
            ("text on background", text, background),
            ("text on bar", text, bar),
            ("text on dialog", text, dialog),
            ("text on card", text, card),
            ("muted on background", muted, background),
            ("muted on bar", muted, bar),
            ("muted on dialog", muted, dialog),
            ("muted on card", muted, card),
            ("accent on bar", accent, bar),
            ("accent on dialog", accent, dialog),
            ("accent on card", accent, card),
            ("accent on background", accent, background),
            ("header text on header", onHeader, header),
            ("banner text on banner", onBanner, banner),
            ("banner title on banner", bannerHighlight, banner),
        ]
        for kind in StateBadge.Kind.allCases {
            pairs.append(("\(kind) badge", kind.foreground, kind.background))
        }
        for tone in ProjectChip.Tone.allCases {
            let ink = tone.color(in: self)
            pairs.append(("\(tone) chip on card", ink, card))
            pairs.append(("\(tone) chip on dialog", ink, dialog))
            pairs.append(("\(tone) chip on bar", ink, bar))
            pairs.append(("\(tone) chip on background", ink, background))
        }
        return pairs
    }
}
