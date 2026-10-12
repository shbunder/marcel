import SwiftUI

/// Sizes on the 2-pt pixel grid. Everything that is drawn (borders, shadows, padding, glyph pixels) is a whole
/// number of grid steps, so edges stay hard.
public enum PixelMetrics {
    /// One pixel of the look.
    public static let grid: CGFloat = 2

    /// `n` grid steps.
    public static func steps(_ n: Int) -> CGFloat { CGFloat(n) * grid }

    /// Borders: thin for cards, chips and bars; thick for dialogue boxes, artifact cards and the screen strips.
    public static let thinBorder: CGFloat = 2
    public static let thickBorder: CGFloat = 3

    /// The offset of the hard shadow, down and to the right.
    public static let smallShadow: CGFloat = 2
    public static let shadow: CGFloat = 4

    /// Padding inside boxes.
    public static let padding: CGFloat = 10
    public static let tightPadding: CGFloat = 6

    /// Space between stacked items.
    public static let gap: CGFloat = 8

    /// Apple's smallest comfortable tap target; choices and tabs are at least this tall.
    public static let minTapTarget: CGFloat = 44
}

/// A hard-edged box: a fill, a border drawn inside the edge and a solid shadow offset down and right.
public struct PixelBox: ViewModifier {
    public var fill: PixelColor
    public var line: PixelColor
    public var shadow: PixelColor?
    public var border: CGFloat
    public var offset: CGFloat

    public init(fill: PixelColor, line: PixelColor, shadow: PixelColor?, border: CGFloat, offset: CGFloat) {
        self.fill = fill
        self.line = line
        self.shadow = shadow
        self.border = border
        self.offset = offset
    }

    public func body(content: Content) -> some View {
        content
            .background(Rectangle().fill(fill.color))
            .overlay(Rectangle().strokeBorder(line.color, lineWidth: border))
            .background(alignment: .topLeading) {
                if let shadow {
                    Rectangle().fill(shadow.color).offset(x: offset, y: offset)
                }
            }
            // The shadow is drawn outside the box; reserve room for it so neighbours don't overlap it.
            .padding(.trailing, shadow == nil ? 0 : offset)
            .padding(.bottom, shadow == nil ? 0 : offset)
    }
}

extension View {
    /// Draws the view as a pixel box. See `PixelBox`.
    public func pixelBox(
        fill: PixelColor, line: PixelColor, shadow: PixelColor? = nil,
        border: CGFloat = PixelMetrics.thickBorder, offset: CGFloat = PixelMetrics.shadow
    ) -> some View {
        modifier(PixelBox(fill: fill, line: line, shadow: shadow, border: border, offset: offset))
    }
}
