import SwiftUI
import UIKit
import XCTest
@testable import PixelKit

/// Fonts, Dynamic Type, the pixel grid, Reduce Motion and keyboard movement in the choice prompt.
final class TypeAndMotionTests: XCTestCase {
    func testBothBundledFontsLoad() {
        XCTAssertTrue(PixelKit.registerFonts())
        XCTAssertNotNil(UIFont(name: PixelFonts.labelFace, size: 12), "Silkscreen is not registered")
        XCTAssertNotNil(UIFont(name: PixelFonts.bodyFace, size: 17), "Pixelify Sans is not registered")
    }

    func testTheOFLLicencesShipWithTheFonts() throws {
        let folder = try XCTUnwrap(Bundle.module.url(forResource: "Fonts", withExtension: nil))
        for name in ["Silkscreen-OFL.txt", "PixelifySans-OFL.txt"] {
            let text = try String(contentsOf: folder.appendingPathComponent(name), encoding: .utf8)
            XCTAssertTrue(text.contains("SIL Open Font License"), name)
        }
    }

    func testBodyTextScalesWithDynamicTypeWithoutACap() {
        XCTAssertEqual(PixelTextRole.body.size(for: .large), 17)
        XCTAssertEqual(PixelTextRole.body.size(for: .accessibility5), 53, accuracy: 0.01)
        XCTAssertGreaterThan(PixelTextRole.detail.size(for: .accessibility5), PixelTextRole.detail.size(for: .xxxLarge))
        XCTAssertNil(PixelTextRole.body.maximumSize)
    }

    func testPixelLabelsGrowThenStopAtTheirCap() {
        for role in PixelTextRole.allCases where role.isLabel {
            let cap = try! XCTUnwrap(role.maximumSize)
            XCTAssertGreaterThan(role.size(for: .xxLarge), role.size(for: .large), "\(role) does not grow")
            XCTAssertEqual(role.size(for: .accessibility5), cap, "\(role) is not capped")
            XCTAssertLessThan(cap, PixelTextRole.body.size(for: .accessibility5))
        }
        // Default sizes keep the wireframes' order: titles over labels over small labels.
        XCTAssertGreaterThan(PixelTextRole.title.baseSize, PixelTextRole.label.baseSize)
        XCTAssertGreaterThan(PixelTextRole.label.baseSize, PixelTextRole.smallLabel.baseSize)
    }

    func testDrawnSizesSitOnTheTwoPointGrid() {
        for value in [PixelMetrics.thinBorder, PixelMetrics.smallShadow, PixelMetrics.shadow, PixelMetrics.padding,
                      PixelMetrics.tightPadding, PixelMetrics.gap] {
            XCTAssertEqual(value.truncatingRemainder(dividingBy: PixelMetrics.grid), 0, "\(value) is off the grid")
        }
        XCTAssertTrue((2...3).contains(PixelMetrics.thinBorder) && (2...3).contains(PixelMetrics.thickBorder))
    }

    // MARK: Reduce Motion

    func testTheMoreMarkerBlinksWithoutReduceMotion() {
        XCTAssertTrue(MoreMarker.animates(reduceMotion: false))
        let start = Date(timeIntervalSinceReferenceDate: 0)
        XCTAssertTrue(MoreMarker.isVisible(at: start, reduceMotion: false))
        XCTAssertFalse(MoreMarker.isVisible(at: start.addingTimeInterval(0.75), reduceMotion: false))
    }

    func testReduceMotionStopsTheBlinkAndKeepsTheMarkerVisible() {
        XCTAssertFalse(MoreMarker.animates(reduceMotion: true))
        let start = Date(timeIntervalSinceReferenceDate: 0)
        for tenth in 0..<20 {
            XCTAssertTrue(MoreMarker.isVisible(at: start.addingTimeInterval(Double(tenth) / 10), reduceMotion: true))
        }
    }

    /// The system's Reduce Motion setting (not only the tests' override) reaches the ▼. The blink's clock is pinned
    /// to its hidden half and only the system setting changes: with Reduce Motion the ▼ is drawn, without it it is
    /// not (so the check can fail).
    @MainActor
    func testTheSystemReduceMotionSettingStopsTheBlink() throws {
        let hiddenHalf = Date(timeIntervalSinceReferenceDate: 0.75)
        XCTAssertFalse(MoreMarker.isVisible(at: hiddenHalf, reduceMotion: false))

        func markerPixels(systemReduceMotion: Bool) throws -> Int {
            var environment = EnvironmentValues()
            environment._accessibilityReduceMotion = systemReduceMotion
            XCTAssertEqual(environment.accessibilityReduceMotion, systemReduceMotion, "the public setting reads this")

            let renderer = ImageRenderer(content: DialogBox(speaker: "Marcel", text: "More.", showsMore: true)
                .environment(\._accessibilityReduceMotion, systemReduceMotion)
                .environment(\.pixelBlinkDate, hiddenHalf)
                .environment(\.pixelRendersOffscreen, true)
                .environment(\.colorScheme, .light)
                .frame(width: 200))
            renderer.scale = 1
            let image = try XCTUnwrap(renderer.cgImage)
            // Count dark pixels in a strip at the box's right side, where the ▼ sits. The box's top and bottom
            // borders cross the whole strip, so rows that are dark all the way across are skipped.
            let bytes = Snapshot.rgba(image)
            var count = 0
            for y in 0..<image.height {
                var row = 0
                for x in (image.width - 30)..<(image.width - 10) {
                    let i = (y * image.width + x) * 4
                    if bytes[i] < 140, bytes[i + 1] < 140, bytes[i + 2] < 140 { row += 1 }
                }
                if row < 15 { count += row }
            }
            return count
        }
        XCTAssertGreaterThan(try markerPixels(systemReduceMotion: true), 4, "Reduce Motion: the ▼ should stay drawn")
        XCTAssertEqual(try markerPixels(systemReduceMotion: false), 0, "without Reduce Motion the ▼ should blink off")
    }

    // MARK: Choice prompt keyboard

    func testArrowKeysMoveTheMarkerAndStopAtTheEnds() {
        let choices: [ChoicePrompt.Choice] = [.init("Yes"), .init("Not now"), .init("Details")]
        XCTAssertEqual(ChoicePrompt.move("Yes", by: 1, in: choices), "Not now")
        XCTAssertEqual(ChoicePrompt.move("Details", by: 1, in: choices), "Details")
        XCTAssertEqual(ChoicePrompt.move("Not now", by: -1, in: choices), "Yes")
        XCTAssertEqual(ChoicePrompt.move("Yes", by: -1, in: choices), "Yes")
        XCTAssertEqual(ChoicePrompt.move(nil, by: 1, in: choices), "Not now")
        XCTAssertNil(ChoicePrompt.move(nil, by: 1, in: []))
    }

    func testTheMarkerStartsOnTheFirstChoiceOrTheOneGiven() {
        let choices: [ChoicePrompt.Choice] = [.init("Yes"), .init("No")]
        XCTAssertEqual(ChoicePrompt(choices).markedChoice, "Yes")
        XCTAssertEqual(ChoicePrompt(choices, marked: "No").markedChoice, "No")
    }
}
