import SwiftUI

/// The chat's colours (ADR-007, wireframe Q1 v10, light and dark): a soft screen, your bubbles in your helper's colour
/// with dark text, Marcel's replies in ink on the screen, and one colour per special message. Frames use the
/// wireframe's colours; label text and filled buttons use a darker shade in light mode so they reach 4.5:1 on white.
public struct ChatPalette: Equatable, Sendable {
    /// The chat's ground.
    public var screen: PixelColor
    /// Text on the screen and in cards.
    public var ink: PixelColor
    public var muted: PixelColor
    /// Your bubble when the helper has no colour of its own (Marcel's rose).
    public var you: PixelColor
    /// Text on your bubble.
    public var youInk: PixelColor
    /// Inside a special message, and the time line's ground.
    public var card: PixelColor
    /// Thin lines: table rules, quote boxes, the item preview.
    public var line: PixelColor
    /// A quote box's or a code block's ground.
    public var well: PixelColor
    /// "Needs you" cards are the only tinted ones.
    public var needsTint: PixelColor
    /// Links in Marcel's replies.
    public var link: PixelColor

    public static let light = ChatPalette(
        screen: PixelColor(0xFFFDF9), ink: PixelColor(0x2A1D24), muted: PixelColor(0x7A6A6F),
        you: PixelColor(0xF08AA0), youInk: PixelColor(0x3A1F35), card: PixelColor(0xFFFFFF),
        line: PixelColor(0xE6DDE2), well: PixelColor(0xF5F1F3), needsTint: PixelColor(0xFFF8E6),
        link: PixelColor(0xB8405E))

    public static let dark = ChatPalette(
        screen: PixelColor(0x231C1E), ink: PixelColor(0xF2E8E1), muted: PixelColor(0xB8A7A2),
        you: PixelColor(0xE07A93), youInk: PixelColor(0x2A1020), card: PixelColor(0x2D2530),
        line: PixelColor(0x43384A), well: PixelColor(0x1F191F), needsTint: PixelColor(0x3A3020),
        link: PixelColor(0xFF8AA5))

    public init(_ scheme: ColorScheme) {
        self = scheme == .dark ? .dark : .light
    }

    init(screen: PixelColor, ink: PixelColor, muted: PixelColor, you: PixelColor, youInk: PixelColor, card: PixelColor,
         line: PixelColor, well: PixelColor, needsTint: PixelColor, link: PixelColor) {
        self.screen = screen
        self.ink = ink
        self.muted = muted
        self.you = you
        self.youInk = youInk
        self.card = card
        self.line = line
        self.well = well
        self.needsTint = needsTint
        self.link = link
    }

    var isDark: Bool { self == .dark }

    /// Your bubble in a helper's colour (its look's colour, as drawn in light mode). In dark mode it is a shade
    /// darker, like the wireframe's rose. Either way it is lightened until the dark text on it reaches 4.5:1, so a
    /// dark colour such as "ink" still gives a readable bubble.
    public func bubble(_ base: PixelColor?) -> PixelColor {
        // Marcel's rose is the wireframe's own bubble colour, in both themes.
        guard let base, base != ChatPalette.light.you else { return you }
        var fill = isDark ? base.mixed(with: PixelColor(0x000000), amount: 0.08) : base
        var step = 0
        while fill.contrast(with: youInk) < 4.5, step < 20 {
            fill = fill.mixed(with: PixelColor(0xFFFFFF), amount: 0.1)
            step += 1
        }
        return fill
    }

    /// The text pairs the chat draws, each held to 4.5:1 by MarcelChat's `ChatLookTests`.
    public var textPairs: [(name: String, text: PixelColor, on: PixelColor)] {
        var pairs: [(String, PixelColor, PixelColor)] = [
            ("ink on screen", ink, screen), ("muted on screen", muted, screen), ("ink on card", ink, card),
            ("muted on card", muted, card), ("ink on needs tint", ink, needsTint),
            ("muted on needs tint", muted, needsTint), ("your text on your bubble", youInk, you),
            ("link on screen", link, screen), ("ink in a quote", ink, well), ("muted in a quote", muted, well),
        ]
        for kind in ChatCardKind.allCases {
            let ground = kind == .needsYou ? needsTint : card
            pairs.append(("\(kind) label", kind.label(in: self), ground))
            pairs.append(("\(kind) button text", kind.buttonText(in: self), kind.label(in: self)))
        }
        return pairs
    }
}

extension PixelColor {
    /// This colour moved `amount` (0…1) of the way towards `other`.
    public func mixed(with other: PixelColor, amount: Double) -> PixelColor {
        func mix(_ a: UInt8, _ b: UInt8) -> UInt32 { UInt32((Double(a) + (Double(b) - Double(a)) * amount).rounded()) }
        return PixelColor((mix(red, other.red) << 16) | (mix(green, other.green) << 8) | mix(blue, other.blue))
    }
}

/// The four special messages, the only boxes in the chat (ADR-007): a coloured frame on all four sides and a short
/// pixel-font label.
public enum ChatCardKind: String, CaseIterable, Sendable {
    /// A quest began (teal).
    case started
    /// A quest waits for a yes or no (gold, the only tinted card).
    case needsYou
    /// A quest finished, or an item arrived (rose).
    case done
    /// A quest stopped with a problem (red).
    case failed

    /// The frame, as in the wireframe.
    public func frame(in palette: ChatPalette) -> PixelColor {
        switch (self, palette.isDark) {
        case (.started, false): PixelColor(0x2F9C8E)
        case (.needsYou, false): PixelColor(0xD99A1C)
        case (.done, false): PixelColor(0xE0607E)
        case (.failed, false): PixelColor(0xCF4B4B)
        case (.started, true): PixelColor(0x5CC8B8)
        case (.needsYou, true): PixelColor(0xF2C14E)
        case (.done, true): PixelColor(0xFF8AA5)
        case (.failed, true): PixelColor(0xF07A7A)
        }
    }

    /// The label and the filled button: the frame colour, darker in light mode so small text stays readable.
    public func label(in palette: ChatPalette) -> PixelColor {
        guard !palette.isDark else { return frame(in: palette) }
        return switch self {
        case .started: PixelColor(0x1F6F65)
        case .needsYou: PixelColor(0x8A5A0B)
        case .done: PixelColor(0xB8405E)
        case .failed: PixelColor(0xA83232)
        }
    }

    /// Text on a filled button.
    public func buttonText(in palette: ChatPalette) -> PixelColor {
        palette.isDark ? palette.card : PixelColor(0xFFFFFF)
    }

    /// The card's ground: only "needs you" is tinted.
    public func ground(in palette: ChatPalette) -> PixelColor {
        self == .needsYou ? palette.needsTint : palette.card
    }
}
