import SwiftUI
import UIKit
import XCTest
@testable import PixelKit

/// An accessibility audit of the component gallery, run without XCUITest.
///
/// The story asks for XCUITest's `performAccessibilityAudit()`. That needs a UI-test target and an
/// `xcodebuild test` destination, which this Mac's Xcode cannot provide (the iOS 26.5 platform is not installed; see
/// ios/README.md). Until it is, this test does the same checks on the same gallery, in a real window, by reading the
/// accessibility tree SwiftUI builds for VoiceOver:
/// - every element has a label (`sufficientElementDescription`), written as plain words: no ▶ ▼ ♥, no ALL CAPS;
/// - every button is at least 44 × 44 pt (`hitRegion`);
/// - the tabs say which one is selected, headers are marked as headers (`trait`);
/// - body text grows at the largest Dynamic Type size (`dynamicType`);
/// - contrast is checked numerically in `ContrastTests` (`contrast`).
@MainActor
final class AccessibilityAuditTests: XCTestCase {
    struct Element {
        var label: String
        var value: String
        var traits: UIAccessibilityTraits
        var frame: CGRect
    }

    override class func setUp() {
        super.setUp()
        // Outside an app, UIKit builds no accessibility tree unless accessibility is switched on for the process
        // (what VoiceOver or XCUITest do in a real run).
        let handle = dlopen("/usr/lib/libAccessibility.dylib", RTLD_NOW)
        typealias Enable = @convention(c) (Bool) -> Void
        if let symbol = dlsym(handle, "_AXSApplicationAccessibilitySetEnabled") {
            unsafeBitCast(symbol, to: Enable.self)(true)
        }
    }

    private var windows: [UIWindow] = []

    override func tearDown() {
        windows.forEach { $0.isHidden = true }
        windows = []
        super.tearDown()
    }

    /// Shows `view` in a window and returns its accessibility elements in reading order.
    func elements<V: View>(of view: V, scheme: ColorScheme = .light, dynamicType: DynamicTypeSize = .large)
        -> [Element]
    {
        let host = UIHostingController(rootView: view
            .environment(\.colorScheme, scheme)
            .environment(\.dynamicTypeSize, dynamicType)
            .environment(\.pixelReduceMotionOverride, true))
        let window = UIWindow(frame: CGRect(x: 0, y: 0, width: 390, height: 4000))
        window.rootViewController = host
        window.makeKeyAndVisible()
        windows.append(window)
        host.view.layoutIfNeeded()
        RunLoop.main.run(until: Date().addingTimeInterval(0.3))

        var found: [Element] = []
        func walk(_ object: NSObject, depth: Int) {
            guard depth < 20 else { return }
            if object.isAccessibilityElement {
                found.append(Element(label: object.accessibilityLabel ?? "", value: object.accessibilityValue ?? "",
                                     traits: object.accessibilityTraits, frame: object.accessibilityFrame))
            }
            if let children = object.accessibilityElements as? [NSObject], !children.isEmpty {
                children.forEach { walk($0, depth: depth + 1) }
            } else if object.accessibilityElementCount() != NSNotFound, object.accessibilityElementCount() > 0 {
                for index in 0..<object.accessibilityElementCount() {
                    if let child = object.accessibilityElement(at: index) as? NSObject { walk(child, depth: depth + 1) }
                }
            } else if let view = object as? UIView {
                view.subviews.forEach { walk($0, depth: depth + 1) }
            }
        }
        walk(host.view, depth: 0)
        return found
    }

    /// Plain words: no decorative symbols, and no word of three or more letters shouted in capitals
    /// ("OK" and "PR" are fine).
    static func plainWordsProblem(in label: String) -> String? {
        if label.trimmingCharacters(in: .whitespaces).isEmpty { return "empty label" }
        if let symbol = label.first(where: { "▶▼♥◀▸▾".contains($0) }) { return "contains \"\(symbol)\"" }
        let words = label.split { !$0.isLetter }
        if let shouted = words.first(where: { $0.count >= 3 && $0 == $0.uppercased() && $0 != $0.lowercased() }) {
            return "\"\(shouted)\" is in capitals"
        }
        return nil
    }

    func testThePlainWordsCheckCatchesWhatItShould() {
        XCTAssertNil(Self.plainWordsProblem(in: "Needs your OK"))
        XCTAssertNotNil(Self.plainWordsProblem(in: "▶ Yes"))
        XCTAssertNotNil(Self.plainWordsProblem(in: "NEEDS YOU"))
        XCTAssertNotNil(Self.plainWordsProblem(in: " "))
    }

    func testTheGalleryPassesTheAuditInBothThemes() {
        for scheme in [ColorScheme.light, .dark] {
            let found = elements(of: PixelGallery(), scheme: scheme)
            XCTAssertGreaterThanOrEqual(found.count, 15, "\(scheme): the accessibility tree is missing elements")

            for element in found {
                if let problem = Self.plainWordsProblem(in: element.label) {
                    XCTFail("\(scheme): \"\(element.label)\": \(problem)")
                }
                if element.traits.contains(.button) {
                    XCTAssertGreaterThanOrEqual(element.frame.height, 44, "\(scheme): \"\(element.label)\" is too short to tap")
                    XCTAssertGreaterThanOrEqual(element.frame.width, 44, "\(scheme): \"\(element.label)\" is too narrow to tap")
                }
            }

            let labels = found.map(\.label)
            for expected in ["Yes", "Not now", "Details", "Chat", "Quests", "Map", "Items",
                             "Marcel says: Supplier A is cheapest: €486 a year less with solar, no exit fee. Switch now?",
                             "You got: Energy comparison! Saved to Home energy.", "Needs your OK", "Project: Family"] {
                XCTAssertTrue(labels.contains(expected), "\(scheme): no element reads \"\(expected)\"")
            }

            let choices = found.filter { ["Yes", "Not now", "Details"].contains($0.label) }
            XCTAssertTrue(choices.allSatisfy { $0.traits.contains(.button) }, "answers must be buttons")

            let tabs = found.filter { ["Chat", "Quests", "Map", "Items"].contains($0.label) }
            XCTAssertEqual(tabs.filter { $0.traits.contains(.selected) }.map(\.label), ["Chat"])
            XCTAssertTrue(tabs.allSatisfy { $0.traits.contains(.button) })

            let headers = found.filter { $0.traits.contains(.header) }.map(\.label)
            XCTAssertTrue(headers.contains("Marcel. Morning · nothing running. 2 things need you"))
            XCTAssertTrue(headers.contains("Artifact: Energy comparison. Updated now"))

            let quest = found.first { $0.label.hasPrefix("Energy contracts.") }
            XCTAssertEqual(quest?.label, "Energy contracts. Working. Project: Home energy. step 1 of 4")
            XCTAssertEqual(quest?.value, "22 percent done")
        }
    }

    func testEveryComponentReadsAsPlainWords() {
        let labels = [
            DialogBox(speaker: "Marcel", text: "Two things wait for you.", showsMore: true).accessibilityText,
            QuestCard(title: "Dentist booking", state: .needsYou, project: "Family", detail: "pick a time")
                .accessibilityText,
            ArtifactCard(title: "Morning board", badge: "Updated now") { EmptyView() }.accessibilityText,
            ItemGetBanner(item: "Energy comparison", savedTo: "Home energy").accessibilityText,
            ProjectChip("Home energy").accessibilityText,
            HeartsHeader(title: "Marcel", subtitle: "needs you", needsYou: 1).accessibilityText,
        ] + StateBadge.Kind.allCases.map { StateBadge($0).accessibilityText }
            + PixelTabBar.appTabs.map(\.title)
        for label in labels {
            XCTAssertNil(Self.plainWordsProblem(in: label), "\"\(label)\": \(Self.plainWordsProblem(in: label) ?? "")")
        }
    }

    func testBodyTextGrowsAtTheLargestTextSize() {
        let says = "Marcel says: Supplier A is cheapest: €486 a year less with solar, no exit fee. Switch now?"
        let normal = elements(of: PixelGallery()).first { $0.label == says }
        let largest = elements(of: PixelGallery(), dynamicType: .accessibility5).first { $0.label == says }
        let normalHeight = try! XCTUnwrap(normal?.frame.height)
        let largestHeight = try! XCTUnwrap(largest?.frame.height)
        XCTAssertGreaterThan(largestHeight, normalHeight * 2.5, "the dialogue text does not scale with Dynamic Type")
    }
}
