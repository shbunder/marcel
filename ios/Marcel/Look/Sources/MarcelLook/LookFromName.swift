import MarcelKit

/// "Pick from my name": the Pixel Zoo's `from_name` (shared/avatar/zoo/zoo/core.py), so the phone picks exactly the
/// look the hub would give a helper made without one. Same name, same look.
public enum LookFromName {
    /// The zoo's lists, in its order (`SPECIES`, `COLORS`, `PERSONALITIES`): the pick is an index into them.
    static let animals: [LookAnimal] = [.giraffe, .crocodile, .frog, .cat, .fox, .bear, .panda, .rabbit, .dog, .pig,
                                        .mouse]
    static let colors: [LookColor] = [.rose, .coral, .tangerine, .honey, .lime, .green, .teal, .sky, .blue, .lavender,
                                      .plum, .cocoa, .sand, .slate, .snow, .ink]
    static let personalities: [LookPersonality] = [.professional, .cheerful, .curious, .cozy, .bold, .cool, .sporty,
                                                   .shy, .grumpy]

    /// JavaScript's `trim()` set: what zoo.js (and so the zoo) strips around a name.
    static let spaces: Set<Unicode.Scalar> = Set((" \t\n\u{0B}\u{0C}\r\u{A0}\u{1680}\u{2000}\u{2001}\u{2002}\u{2003}\u{2004}"
        + "\u{2005}\u{2006}\u{2007}\u{2008}\u{2009}\u{200A}\u{2028}\u{2029}\u{202F}\u{205F}\u{3000}\u{FEFF}")
        .unicodeScalars)

    public static func pick(_ name: String) -> KnownLook {
        let h = hash(lowercased(trimmed(name)))
        let color: LookColor = (h >> 7) % 3 == 0 ? .natural : colors[Int((h >> 11) % UInt32(colors.count))]
        return KnownLook(animal: animals[Int(h % UInt32(animals.count))], color: color,
                         personality: personalities[Int((h >> 19) % UInt32(personalities.count))])
    }

    /// The name without JavaScript's `trim()` set around it, cut by code point as JavaScript does (a "\r\n" is two).
    static func trimmed(_ name: String) -> String.UnicodeScalarView.SubSequence {
        var scalars = name.unicodeScalars[...]
        while let first = scalars.first, spaces.contains(first) { scalars = scalars.dropFirst() }
        while let last = scalars.last, spaces.contains(last) { scalars = scalars.dropLast() }
        return scalars
    }

    /// Lower case as JavaScript and Python do it: like Swift's, except a capital sigma that ends a word is "ς".
    static func lowercased(_ scalars: String.UnicodeScalarView.SubSequence) -> String {
        let all = Array(scalars)
        var out = String.UnicodeScalarView()
        for (i, scalar) in all.enumerated() {
            let afterLetter = i > 0 && all[i - 1].properties.isCased
            let beforeLetter = i + 1 < all.count && all[i + 1].properties.isCased
            if scalar == "\u{03A3}", afterLetter, !beforeLetter {
                out.append("\u{03C2}")
            } else {
                out.append(contentsOf: String(scalar).lowercased().unicodeScalars)
            }
        }
        return String(out)
    }

    /// FNV-1a over UTF-16 code units, then a final mix: zoo.js's `hash`.
    static func hash(_ text: String) -> UInt32 {
        var h: UInt32 = 2_166_136_261
        for unit in text.utf16 {
            h ^= UInt32(unit)
            h = h &* 16_777_619
        }
        h ^= h >> 16
        h = h &* 0x85EB_CA6B
        h ^= h >> 13
        h = h &* 0xC2B2_AE35
        h ^= h >> 16
        return h
    }
}
