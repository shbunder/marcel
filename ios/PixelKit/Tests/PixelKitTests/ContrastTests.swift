import XCTest
@testable import PixelKit

/// WCAG AA, checked numerically on the tokens, in both themes.
final class ContrastTests: XCTestCase {
    func testTheContrastMathMatchesKnownValues() {
        XCTAssertEqual(PixelColor(0x000000).contrast(with: PixelColor(0xFFFFFF)), 21, accuracy: 0.01)
        XCTAssertEqual(PixelColor(0x777777).contrast(with: PixelColor(0xFFFFFF)), 4.48, accuracy: 0.01)
        // The wireframes' white-on-rose tag is why the working tag uses ink: it fails AA.
        XCTAssertLessThan(PixelColor(0xFFFFFF).contrast(with: .rose), 4.5)
    }

    func testEveryTextPairPassesAAInBothThemes() {
        for (themeName, theme) in [("light", PixelTheme.light), ("dark", PixelTheme.dark)] {
            let pairs = theme.textPairs
            XCTAssertGreaterThan(pairs.count, 30)
            for pair in pairs {
                let ratio = pair.text.contrast(with: pair.on)
                XCTAssertGreaterThanOrEqual(ratio, 4.5, "\(themeName): \(pair.name) is \(String(format: "%.2f", ratio)):1 "
                    + "(\(pair.text) on \(pair.on))")
            }
        }
    }

    /// Non-text contrast (WCAG 1.4.11, 3:1): box borders against what they sit on, and the progress fill's end.
    func testBordersAndTheProgressFillStandOut() {
        for (themeName, theme) in [("light", PixelTheme.light), ("dark", PixelTheme.dark)] {
            for (name, surface) in [("background", theme.background), ("bar", theme.bar), ("dialog", theme.dialog),
                                    ("card", theme.card)] {
                XCTAssertGreaterThanOrEqual(theme.line.contrast(with: surface), 3, "\(themeName): border on \(name)")
            }
            let fillVisible = theme.fill.contrast(with: theme.track) >= 3
                || (theme.line.contrast(with: theme.track) >= 3 && theme.line.contrast(with: theme.fill) >= 3)
            XCTAssertTrue(fillVisible, "\(themeName): where the progress fill ends is not visible enough")
        }
    }

    func testThemesFollowTheColorScheme() {
        XCTAssertEqual(PixelTheme(.light), .light)
        XCTAssertEqual(PixelTheme(.dark), .dark)
        XCTAssertNotEqual(PixelTheme.light.background, PixelTheme.dark.background)
        XCTAssertEqual(PixelTheme.light.background, .cream)
        XCTAssertEqual(PixelTheme.light.text, .ink)
    }

    func testThePaletteIsTheApprovedOne() {
        XCTAssertEqual(PixelColor.cream.description, "#f6efe4")
        XCTAssertEqual(PixelColor.ink.description, "#2a1d24")
        XCTAssertEqual(PixelColor.rose.description, "#d87990")
        XCTAssertEqual(PixelColor.deep.description, "#9c3e5a")
        XCTAssertEqual(PixelColor.teal.description, "#3f6b6a")
        XCTAssertEqual(PixelColor.gold.description, "#e2a43a")
    }
}
