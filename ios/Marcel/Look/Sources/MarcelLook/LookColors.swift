import MarcelKit
import PixelKit

/// The Pixel Zoo's colours (shared/avatar/zoo, `COLORS` and each animal's natural colour), for your bubbles: a
/// helper's look colour, or its animal's own colour when the look says "natural". Marcel the rose giraffe gives rose.
public enum LookColors {
    public static func bubble(_ look: KnownLook) -> PixelColor {
        look.color == .natural ? natural(look.animal) : named(look.color)
    }

    static func natural(_ animal: LookAnimal) -> PixelColor {
        switch animal {
        case .giraffe: PixelColor(0xF08AA0)
        case .crocodile: PixelColor(0x4FAE5A)
        case .frog: PixelColor(0x8FD14F)
        case .cat: PixelColor(0xF2A65A)
        case .fox: PixelColor(0xF07A35)
        case .bear: PixelColor(0xB5794C)
        case .panda: PixelColor(0xF1EDF2)
        case .rabbit: PixelColor(0xECE4F2)
        case .dog: PixelColor(0xE0B07A)
        case .pig: PixelColor(0xF9A9BD)
        case .mouse: PixelColor(0xB3AEC2)
        }
    }

    static func named(_ color: LookColor) -> PixelColor {
        switch color {
        case .natural, .rose: PixelColor(0xF08AA0)
        case .coral: PixelColor(0xFF8A6E)
        case .tangerine: PixelColor(0xF5953F)
        case .honey: PixelColor(0xF2C14E)
        case .lime: PixelColor(0xA8D65A)
        case .green: PixelColor(0x5FB85A)
        case .teal: PixelColor(0x3FB8A8)
        case .sky: PixelColor(0x6CC3F0)
        case .blue: PixelColor(0x5B8DEF)
        case .lavender: PixelColor(0xAB93E8)
        case .plum: PixelColor(0xB56AA8)
        case .cocoa: PixelColor(0xA87650)
        case .sand: PixelColor(0xE0C49A)
        case .slate: PixelColor(0x8F97A8)
        case .snow: PixelColor(0xEEEAF2)
        case .ink: PixelColor(0x5A5A78)
        }
    }
}
