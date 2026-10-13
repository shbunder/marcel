import CoreText
import SwiftUI

#if canImport(UIKit)
import UIKit
#endif

/// Text in a pixel font that never falls back to a system font. Use it instead of `Text(…).pixelText(role)`.
///
/// Character by character it picks:
/// 1. **Silkscreen for €** in body text: Pixelify Sans has a €, but at body size it reads as "6" or "8".
/// 2. **The role's own font** when it has the character.
/// 3. **A pixel bitmap** (→ ← ✓ ✔, which neither font has), drawn on the font's pixel grid in the text colour.
/// 4. **A plain stand-in** the font has (Silkscreen: − becomes -, ĳ becomes ij).
/// 5. **The other pixel font**, when it has the character.
/// Only a character none of these cover reaches the system font. VoiceOver reads the original string.
public struct PixelText: View {
    public var string: String
    public var role: PixelTextRole
    public var capitals: Bool

    @Environment(\.dynamicTypeSize) private var dynamicType

    /// - Parameter capitals: labels are drawn in capitals unless this is false. Pass sentence-case text.
    public init(_ string: String, role: PixelTextRole, capitals: Bool = true) {
        self.string = string
        self.role = role
        self.capitals = capitals
    }

    public var body: some View {
        let size = role.size(for: dynamicType)
        Self.text(string, role: role, capitals: capitals, dynamicType: dynamicType)
            .lineSpacing(size * (role.isLabel ? 0.2 : 0.15))
            .accessibilityLabel(Text(verbatim: string))
    }

    /// The styled `Text` alone, for composing several pieces into one paragraph (e.g. a label and a value in one line).
    static func text(_ string: String, role: PixelTextRole, capitals: Bool = true, dynamicType: DynamicTypeSize) -> Text {
        let shown = role.isLabel && capitals ? string.uppercased() : string
        return PixelGlyphFallback.text(shown, face: role.isLabel ? .silkscreen : .pixelify,
                                       size: role.size(for: dynamicType))
    }
}

/// How each character is drawn; see `PixelText`.
public enum PixelGlyphFallback {
    public enum Face: String, Sendable, CaseIterable {
        case silkscreen, pixelify

        public var postScriptName: String {
            self == .silkscreen ? PixelFonts.labelFace : PixelFonts.bodyFace
        }

        var other: Face { self == .silkscreen ? .pixelify : .silkscreen }
    }

    public enum Route: Equatable, Sendable {
        case font(Face)
        case bitmap(PixelBitmap)
        case replacement(String, Face)
        /// Nothing pixel-shaped covers it: the system font draws it.
        case system
    }

    /// Characters a face draws in the other face even though it has them (Pixelify's € reads as a digit).
    static let preferOther: [Face: Set<Character>] = [.pixelify: ["€"]]

    /// Plain stand-ins, per face, for characters the face lacks.
    static let replacements: [Face: [Character: String]] = [
        .silkscreen: ["−": "-", "ĳ": "ij", "Ĳ": "IJ"],
        .pixelify: [:],
    ]

    public static func route(for character: Character, in face: Face) -> Route {
        if preferOther[face]?.contains(character) == true, has(character, in: face.other) {
            return .font(face.other)
        }
        if has(character, in: face) { return .font(face) }
        if let bitmap = PixelBitmap(character) { return .bitmap(bitmap) }
        if let replacement = replacements[face]?[character],
           replacement.allSatisfy({ has($0, in: face) }) {
            return .replacement(replacement, face)
        }
        if has(character, in: face.other) { return .font(face.other) }
        return .system
    }

    /// Whether the bundled font has a glyph for every scalar of `character`.
    public static func has(_ character: Character, in face: Face) -> Bool {
        if character.isWhitespace || character.isNewline { return true }
        guard let set = characterSets[face] else { return false }
        return character.unicodeScalars.allSatisfy { CFCharacterSetIsLongCharacterMember(set, $0.value) }
    }

    nonisolated(unsafe) private static let characterSets: [Face: CFCharacterSet] = {
        PixelFonts.register()
        var sets: [Face: CFCharacterSet] = [:]
        for face in Face.allCases {
            let font = CTFontCreateWithName(face.postScriptName as CFString, 12, nil)
            // CoreText answers with a stand-in font when the name is unknown; only trust the real one.
            guard CTFontCopyPostScriptName(font) as String == face.postScriptName else { continue }
            sets[face] = CTFontCopyCharacterSet(font)
        }
        return sets
    }()

    /// Builds the text: runs of characters with the same route share one segment.
    static func text(_ string: String, face: Face, size: CGFloat) -> Text {
        var segments: [Text] = []
        var run = ""
        var runRoute: Route?

        func flush() {
            guard let route = runRoute, !run.isEmpty else { return }
            segments.append(segment(run, route: route, size: size))
            run = ""
        }

        for character in string {
            let route = route(for: character, in: face)
            if case .bitmap = route {
                flush()
                runRoute = nil
                segments.append(segment(String(character), route: route, size: size))
                continue
            }
            if route != runRoute { flush() }
            runRoute = route
            run.append(character)
        }
        flush()

        guard var result = segments.first else { return Text(verbatim: "") }
        for next in segments.dropFirst() {
            result = Text("\(result)\(next)")
        }
        return result
    }

    /// The same routes as an `AttributedString`, for text that must stay one attributed run (a tappable link).
    /// A pixel bitmap cannot live in an attributed string, so those characters (→ ← ✓ ✔) keep the face's font.
    static func attributed(_ string: String, face: Face, size: CGFloat) -> AttributedString {
        var result = AttributedString()
        for character in string {
            var piece: AttributedString
            switch route(for: character, in: face) {
            case .font(let routed):
                piece = AttributedString(String(character))
                piece.font = .custom(routed.postScriptName, fixedSize: size)
            case .replacement(let replacement, let routed):
                piece = AttributedString(replacement)
                piece.font = .custom(routed.postScriptName, fixedSize: size)
            case .bitmap:
                piece = AttributedString(String(character))
                piece.font = .custom(face.postScriptName, fixedSize: size)
            case .system:
                piece = AttributedString(String(character))
                piece.font = .system(size: size)
            }
            result += piece
        }
        return result
    }

    private static func segment(_ run: String, route: Route, size: CGFloat) -> Text {
        switch route {
        case .font(let face):
            return Text(verbatim: run).font(.custom(face.postScriptName, fixedSize: size))
        case .replacement(let replacement, let face):
            return Text(verbatim: replacement).font(.custom(face.postScriptName, fixedSize: size))
        case .bitmap(let bitmap):
            return bitmap.text(size: size)
        case .system:
            return Text(verbatim: run).font(.system(size: size))
        }
    }
}

/// The symbols neither pixel font has, drawn five font-pixels tall like Silkscreen's capitals.
public struct PixelBitmap: Equatable, Sendable {
    public let rows: [String]

    static let shapes: [Character: [String]] = [
        "→": ["....X..", ".....X.", "XXXXXXX", ".....X.", "....X.."],
        "←": ["..X....", ".X.....", "XXXXXXX", ".X.....", "..X...."],
        "✓": ["......X", ".....X.", "X...X..", ".X.X...", "..X...."],
        "✔": [".....XX", "....XX.", "XX.XX..", ".XXX...", "..X...."],
    ]

    public static var characters: Set<Character> { Set(shapes.keys) }

    public init?(_ character: Character) {
        guard let rows = Self.shapes[character] else { return nil }
        self.rows = rows
    }

    /// The bitmap as a template image inside a `Text`, so it takes the text colour and flows with the words.
    /// One font pixel is an eighth of the point size (Silkscreen's grid), with a pixel of space on each side.
    func text(size: CGFloat) -> Text {
        #if canImport(UIKit)
        let pixel = max(1, (size / 8).rounded())
        let columns = (rows.first?.count ?? 0) + 2
        let format = UIGraphicsImageRendererFormat()
        format.scale = 3
        let canvas = CGSize(width: CGFloat(columns) * pixel, height: CGFloat(rows.count) * pixel)
        let image = UIGraphicsImageRenderer(size: canvas, format: format).image { context in
            UIColor.black.setFill()
            for (y, row) in rows.enumerated() {
                for (x, cell) in row.enumerated() where cell == "X" {
                    context.fill(CGRect(x: CGFloat(x + 1) * pixel, y: CGFloat(y) * pixel, width: pixel, height: pixel))
                }
            }
        }.withRenderingMode(.alwaysTemplate)
        return Text(Image(uiImage: image).renderingMode(.template).interpolation(.none))
        #else
        return Text(verbatim: "")
        #endif
    }
}
