import SwiftUI

/// The small pixel symbols of the look, drawn as bitmaps on the pixel grid (the bundled fonts have no ▶, ▼ or ♥,
/// and a fallback system glyph would break the style). Always decorative: hidden from VoiceOver.
public struct PixelGlyph: View {
    public enum Shape: Sendable, CaseIterable {
        /// ▶ — the choice marker.
        case pointer
        /// ▼ — "more text", bottom right of a dialogue box.
        case more
        /// ♥
        case heart

        var rows: [String] {
            switch self {
            case .pointer: ["X..", "XX.", "XXX", "XX.", "X.."]
            case .more: ["XXXXX", ".XXX.", "..X.."]
            case .heart: ["XX.XX", "XXXXX", ".XXX.", "..X.."]
            }
        }
    }

    public var shape: Shape
    public var color: PixelColor
    public var pixel: CGFloat

    public init(_ shape: Shape, color: PixelColor, pixel: CGFloat = PixelMetrics.grid) {
        self.shape = shape
        self.color = color
        self.pixel = pixel
    }

    public var body: some View {
        let rows = shape.rows
        let width = CGFloat(rows[0].count) * pixel
        let height = CGFloat(rows.count) * pixel
        Canvas { context, _ in
            for (y, row) in rows.enumerated() {
                for (x, cell) in row.enumerated() where cell == "X" {
                    let rect = CGRect(x: CGFloat(x) * pixel, y: CGFloat(y) * pixel, width: pixel, height: pixel)
                    context.fill(Path(rect), with: .color(color.color))
                }
            }
        }
        .frame(width: width, height: height)
        .accessibilityHidden(true)
    }
}

extension PixelGlyph {
    /// The glyph pixel for a Dynamic Type setting: 2 pt by default, growing in whole points with large text.
    static func pixel(for dynamicType: DynamicTypeSize) -> CGFloat {
        (PixelMetrics.grid * min(PixelTextRole.scale(for: dynamicType), 1.5)).rounded()
    }
}
