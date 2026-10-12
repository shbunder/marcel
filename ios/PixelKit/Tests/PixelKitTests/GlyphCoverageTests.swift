import XCTest
@testable import PixelKit

/// The fonts must draw everything a Belgian family writes, in pixel style, without silently reaching for a system
/// font. Silkscreen shows text in capitals, so it is also checked on the capitals.
final class GlyphCoverageTests: XCTestCase {
    static let belgian = Array("€éèêëàâçîïôûùüœ«»’“”„—–…°")
    static let capitals = Array("ÉÈÊËÀÂÇÎÏÔÛÙÜŒ")
    static let symbols = Array("→←✓✔−ĳ")

    func testBothFontsCoverTheBelgianSetInPixelStyle() {
        for face in PixelGlyphFallback.Face.allCases {
            let set = Self.belgian + Self.symbols + (face == .silkscreen ? Self.capitals : [])
            for character in set {
                let route = PixelGlyphFallback.route(for: character, in: face)
                XCTAssertNotEqual(route, .system, "\(face) draws \"\(character)\" with a system font")
            }
        }
    }

    func testTheBelgianLettersComeFromTheFontsThemselves() {
        for face in PixelGlyphFallback.Face.allCases {
            let set = Self.belgian + (face == .silkscreen ? Self.capitals : [])
            for character in set {
                XCTAssertTrue(PixelGlyphFallback.has(character, in: face), "\(face) lacks \"\(character)\"")
            }
        }
    }

    func testTheKnownGapsUseTheirFallbacks() {
        for character in "→←✓✔" {
            for face in PixelGlyphFallback.Face.allCases {
                XCTAssertFalse(PixelGlyphFallback.has(character, in: face), "\(face) has \"\(character)\" now")
                XCTAssertEqual(PixelGlyphFallback.route(for: character, in: face), .bitmap(PixelBitmap(character)!))
            }
        }
        XCTAssertEqual(PixelGlyphFallback.route(for: "−", in: .silkscreen), .replacement("-", .silkscreen))
        XCTAssertEqual(PixelGlyphFallback.route(for: "ĳ", in: .silkscreen), .replacement("ij", .silkscreen))
        XCTAssertEqual(PixelGlyphFallback.route(for: "Ĳ", in: .silkscreen), .replacement("IJ", .silkscreen))
        XCTAssertEqual(PixelGlyphFallback.route(for: "−", in: .pixelify), .font(.pixelify))
    }

    func testEuroInBodyTextIsDrawnInSilkscreen() {
        XCTAssertTrue(PixelGlyphFallback.has("€", in: .pixelify), "Pixelify has a € (it just reads badly)")
        XCTAssertEqual(PixelGlyphFallback.route(for: "€", in: .pixelify), .font(.silkscreen))
        XCTAssertEqual(PixelGlyphFallback.route(for: "€", in: .silkscreen), .font(.silkscreen))
        XCTAssertEqual(PixelGlyphFallback.route(for: "4", in: .pixelify), .font(.pixelify))
    }

    func testEveryBitmapIsFivePixelsTallAndRectangular() {
        for character in PixelBitmap.characters {
            let rows = PixelBitmap(character)!.rows
            XCTAssertEqual(rows.count, 5, "\(character)")
            XCTAssertEqual(Set(rows.map(\.count)).count, 1, "\(character) has ragged rows")
        }
    }
}
