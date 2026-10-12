import SwiftUI

/// One opaque sRGB colour from the palette. Kept as numbers (not just a `Color`) so the contrast of every text and
/// background pair can be checked in tests.
public struct PixelColor: Hashable, Sendable, CustomStringConvertible {
    public let red: UInt8
    public let green: UInt8
    public let blue: UInt8

    public init(_ hex: UInt32) {
        red = UInt8((hex >> 16) & 0xFF)
        green = UInt8((hex >> 8) & 0xFF)
        blue = UInt8(hex & 0xFF)
    }

    public var color: Color {
        Color(.sRGB, red: Double(red) / 255, green: Double(green) / 255, blue: Double(blue) / 255, opacity: 1)
    }

    public var description: String { String(format: "#%02x%02x%02x", red, green, blue) }

    /// WCAG 2.x relative luminance.
    public var luminance: Double {
        func channel(_ value: UInt8) -> Double {
            let c = Double(value) / 255
            return c <= 0.04045 ? c / 12.92 : pow((c + 0.055) / 1.055, 2.4)
        }
        return 0.2126 * channel(red) + 0.7152 * channel(green) + 0.0722 * channel(blue)
    }

    /// WCAG 2.x contrast ratio between two colours (1 to 21).
    public func contrast(with other: PixelColor) -> Double {
        let a = luminance, b = other.luminance
        return (max(a, b) + 0.05) / (min(a, b) + 0.05)
    }
}

extension PixelColor {
    // The six colours the look is built on (wireframes, scratch/spikes/SP5-avatar/wireframes/).
    public static let cream = PixelColor(0xF6EFE4)
    public static let ink = PixelColor(0x2A1D24)
    public static let rose = PixelColor(0xD87990)
    public static let deep = PixelColor(0x9C3E5A)
    public static let teal = PixelColor(0x3F6B6A)
    public static let gold = PixelColor(0xE2A43A)
}
