import SwiftUI

/// The app logo's puffy bow tiling (option 12) as the chat's ground: every bow's outline drawn faintly, a fixed few
/// filled in the brand colours. Static (drawn once, never animates), hidden from VoiceOver, and fully opaque: every
/// colour is mixed with the screen colour up front, so Reduce Transparency has nothing to change and text on top keeps
/// its contrast.
public struct BowTiling: View {
    /// How many bows are coloured: none (outlines only), about 1 in 14, 1 in 9 or 1 in 5.
    public enum Accents: Int, Sendable, CaseIterable {
        case none = 0, fewer = 14, some = 9, more = 5
    }

    /// How strong the colours are: faint behind text (the chat), more present where nothing sits on top (the start
    /// screen).
    public enum Strength: Sendable {
        case faint, present

        func outline(dark: Bool) -> Double {
            switch self {
            case .faint: dark ? 0.07 : 0.06
            case .present: dark ? 0.14 : 0.12
            }
        }

        func fill(dark: Bool) -> Double {
            switch self {
            case .faint: dark ? 0.30 : 0.24
            case .present: dark ? 0.62 : 0.55
            }
        }
    }

    var accents: Accents
    var strength: Strength
    /// Points across one bow (the logo's 270-unit box).
    var bowWidth: CGFloat
    /// The tiling's tilt, as in the logo.
    var degrees: Double

    @Environment(\.colorScheme) private var scheme

    public init(accents: Accents = .some, strength: Strength = .faint, bowWidth: CGFloat = 92, degrees: Double = 18) {
        self.accents = accents
        self.strength = strength
        self.bowWidth = bowWidth
        self.degrees = degrees
    }

    /// Behind the chat: outlines only, faint (the owner found coloured bows too busy behind text).
    public static let chat = BowTiling(accents: .none)
    /// The start screen: the logo's own scale (a bow is about half the icon's width), coloured bows more present.
    public static let start = BowTiling(accents: .more, strength: .present, bowWidth: 150)

    /// One bow in its 270 × 200 box: puffy loops pinched at the middle (the logo's path).
    public static let bow: Path = {
        var p = Path()
        p.move(to: CGPoint(x: 0, y: 0))
        p.addCurve(to: CGPoint(x: 135, y: 76), control1: CGPoint(x: 57.16, y: 3.73), control2: CGPoint(x: 77.84, y: 72.27))
        p.addCurve(to: CGPoint(x: 270, y: 0), control1: CGPoint(x: 192.16, y: 72.27), control2: CGPoint(x: 212.84, y: 3.73))
        p.addLine(to: CGPoint(x: 270, y: 200))
        p.addCurve(to: CGPoint(x: 135, y: 124), control1: CGPoint(x: 212.84, y: 196.27),
                   control2: CGPoint(x: 192.16, y: 127.73))
        p.addCurve(to: CGPoint(x: 0, y: 200), control1: CGPoint(x: 77.84, y: 127.73), control2: CGPoint(x: 57.16, y: 196.27))
        p.closeSubpath()
        return p
    }()

    /// The colours, already mixed with the ground: (ground, outline, the three fills).
    public struct Ink: Sendable {
        public var ground: PixelColor
        public var outline: PixelColor
        public var fills: [PixelColor]
    }

    public static func ink(_ scheme: ColorScheme, strength: Strength = .faint) -> Ink {
        let palette = ChatPalette(scheme)
        let dark = scheme == .dark
        let brand = [PixelColor(0xD87990), PixelColor(0x2FB39A), PixelColor(0xB56AA8)]  // rose, the bow teal, plum
        return Ink(ground: palette.screen,
                   outline: mix(dark ? PixelColor(0xF2E8E1) : PixelColor(0x2A1D24), into: palette.screen,
                                strength.outline(dark: dark)),
                   fills: brand.map { mix($0, into: palette.screen, strength.fill(dark: dark)) })
    }

    /// `top` over `ground` at `amount` (0…1), as one opaque colour.
    static func mix(_ top: PixelColor, into ground: PixelColor, _ amount: Double) -> PixelColor {
        func channel(_ a: UInt8, _ b: UInt8) -> UInt32 {
            UInt32((Double(b) * (1 - amount) + Double(a) * amount).rounded())
        }
        return PixelColor(channel(top.red, ground.red) << 16 | channel(top.green, ground.green) << 8
            | channel(top.blue, ground.blue))
    }

    /// Which bows are coloured, and with which colour: a fixed scatter from the bow's place in the tiling, the same on
    /// every launch and every phone.
    public static func fill(column: Int, row: Int, accents: Accents) -> Int? {
        var h = UInt32(bitPattern: Int32(truncatingIfNeeded: column &* 73_856_093 ^ row &* 19_349_663))
        h ^= h >> 13
        h = h &* 0x5BD1_E995
        h ^= h >> 15
        guard accents.rawValue > 0, h % UInt32(accents.rawValue) == 0 else { return nil }
        return Int((h >> 8) % 3)
    }

    public var body: some View {
        let ink = Self.ink(scheme, strength: strength)
        Canvas(opaque: true, rendersAsynchronously: false) { context, size in
            context.fill(Path(CGRect(origin: .zero, size: size)), with: .color(ink.ground.color))
            let scale = bowWidth / 270
            // Cover the screen after the tilt: the diagonal, in tiling units.
            let reach = Int((hypot(size.width, size.height) / (124 * scale)).rounded(.up)) / 2 + 2
            var c = context
            c.translateBy(x: size.width / 2, y: size.height / 2)
            c.rotate(by: .degrees(degrees))
            c.scaleBy(x: scale, y: scale)
            for row in -reach...reach {
                for column in -reach...reach {
                    let origin = CGPoint(x: 270 * CGFloat(column) + 135 * CGFloat(row) - 135,
                                         y: -124 * CGFloat(row) - 100)
                    let bow = Self.bow.offsetBy(dx: origin.x, dy: origin.y)
                    if let fill = Self.fill(column: column, row: row, accents: accents) {
                        c.fill(bow, with: .color(ink.fills[fill].color))
                    }
                    c.stroke(bow, with: .color(ink.outline.color),
                             style: StrokeStyle(lineWidth: 1.5 / scale, lineJoin: .round))
                }
            }
        }
        .clipped()
        .accessibilityHidden(true)
        .allowsHitTesting(false)
    }
}
