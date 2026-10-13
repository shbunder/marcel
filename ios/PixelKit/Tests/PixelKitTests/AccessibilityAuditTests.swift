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
/// - nothing is clipped: no element runs off the screen or over another (`textClipped`), also at the largest
///   Dynamic Type size;
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

    /// The audit runs in light, dark, and at the largest Dynamic Type size (Accessibility 5) in both themes.
    static let runs: [(name: String, scheme: ColorScheme, dynamicType: DynamicTypeSize)] = [
        ("light", .light, .large), ("dark", .dark, .large),
        ("light, largest text", .light, .accessibility5), ("dark, largest text", .dark, .accessibility5),
    ]

    func testTheGalleryPassesTheAudit() {
        for run in Self.runs {
            let found = elements(of: PixelGallery(), scheme: run.scheme, dynamicType: run.dynamicType)
            audit(found, run: run.name)
        }
    }

    private func audit(_ found: [Element], run: String) {
        XCTAssertGreaterThanOrEqual(found.count, 15, "\(run): the accessibility tree is missing elements")

        for element in found {
            if let problem = Self.plainWordsProblem(in: element.label) {
                XCTFail("\(run): \"\(element.label)\": \(problem)")
            }
            if element.traits.contains(.button) {
                XCTAssertGreaterThanOrEqual(element.frame.height, 44, "\(run): \"\(element.label)\" is too short to tap")
                XCTAssertGreaterThanOrEqual(element.frame.width, 44, "\(run): \"\(element.label)\" is too narrow to tap")
            }
        }
        if let problem = Self.clippingProblem(in: found, screenWidth: 390) {
            XCTFail("\(run): \(problem)")
        }

        let labels = found.map(\.label)
        for expected in ["Chat", "Quests", "Map", "Items",
                         "Supplier A is cheapest: €486 a year less with solar, no exit fee. Switch now?",
                         "Needs your OK", "Project: Family"] {
            XCTAssertTrue(labels.contains(expected), "\(run): no element reads \"\(expected)\"")
        }

        let tabs = found.filter { ["Chat", "Quests", "Map", "Items"].contains($0.label) }
        XCTAssertEqual(tabs.filter { $0.traits.contains(.selected) }.map(\.label), ["Chat"], run)
        XCTAssertTrue(tabs.allSatisfy { $0.traits.contains(.button) }, run)

        let headers = found.filter { $0.traits.contains(.header) }.map(\.label)
        XCTAssertTrue(headers.contains("Marcel. Morning · nothing running. 2 things need you"), run)
        XCTAssertTrue(headers.contains("Artifact: Energy comparison. Updated now"), run)

        let quest = found.first { $0.label.hasPrefix("Energy contracts.") }
        XCTAssertEqual(quest?.label, "Energy contracts. Working. Project: Home energy. step 1 of 4", run)
        XCTAssertEqual(quest?.value, "22 percent done", run)
    }

    /// Clipped or truncated text shows up in the tree as an element pushed past the screen edge or drawn over a
    /// neighbour: every single-line label in PixelKit is `fixedSize()` (it cannot truncate to "…"; it can only
    /// overflow) and every paragraph wraps with `fixedSize(vertical:)`. So: every element must lie inside the
    /// screen, and no two may overlap.
    static func clippingProblem(in elements: [Element], screenWidth: CGFloat) -> String? {
        for element in elements {
            let frame = element.frame
            if frame.minX < -0.5 || frame.maxX > screenWidth + 0.5 {
                return "\"\(element.label)\" runs off the screen (x \(Int(frame.minX))–\(Int(frame.maxX)))"
            }
            if frame.width < 1 || frame.height < 1 {
                return "\"\(element.label)\" has no room to draw"
            }
        }
        for (index, a) in elements.enumerated() {
            for b in elements[(index + 1)...] {
                let overlap = a.frame.intersection(b.frame)
                if !overlap.isNull, overlap.width > 1, overlap.height > 1 {
                    return "\"\(a.label)\" and \"\(b.label)\" are drawn over each other"
                }
            }
        }
        return nil
    }

    func testTheClippingCheckCatchesOverflowAndOverlap() {
        func element(_ label: String, _ frame: CGRect) -> Element {
            Element(label: label, value: "", traits: .staticText, frame: frame)
        }
        XCTAssertNil(Self.clippingProblem(in: [element("a", CGRect(x: 0, y: 0, width: 100, height: 20)),
                                               element("b", CGRect(x: 0, y: 20, width: 100, height: 20))],
                                          screenWidth: 390))
        XCTAssertNotNil(Self.clippingProblem(in: [element("wide", CGRect(x: 300, y: 0, width: 200, height: 20))],
                                             screenWidth: 390))
        XCTAssertNotNil(Self.clippingProblem(in: [element("a", CGRect(x: 0, y: 0, width: 100, height: 20)),
                                                  element("b", CGRect(x: 50, y: 10, width: 100, height: 20))],
                                             screenWidth: 390))
    }

    func testEveryComponentReadsAsPlainWords() {
        let labels = [
            QuestCard(title: "Dentist booking", state: .needsYou, project: "Family", detail: "pick a time")
                .accessibilityText,
            ArtifactCard(title: "Morning board", badge: "Updated now") { EmptyView() }.accessibilityText,
            ProjectChip("Home energy").accessibilityText,
            HeartsHeader(title: "Marcel", subtitle: "needs you", needsYou: 1).accessibilityText,
        ] + StateBadge.Kind.allCases.map { StateBadge($0).accessibilityText }
            + PixelTabBar.appTabs.map(\.title)
        for label in labels {
            XCTAssertNil(Self.plainWordsProblem(in: label), "\"\(label)\": \(Self.plainWordsProblem(in: label) ?? "")")
        }
    }

    func testBodyTextGrowsAtTheLargestTextSize() {
        // The gallery's "needs you" card (S-18.6): its body text is the system font, which scales without a cap.
        let says = "Supplier A is cheapest: €486 a year less with solar, no exit fee. Switch now?"
        let normal = elements(of: PixelGallery()).first { $0.label == says }
        let largest = elements(of: PixelGallery(), dynamicType: .accessibility5).first { $0.label == says }
        let normalHeight = try! XCTUnwrap(normal?.frame.height)
        let largestHeight = try! XCTUnwrap(largest?.frame.height)
        XCTAssertGreaterThan(largestHeight, normalHeight * 2.5, "the card's text does not scale with Dynamic Type")
    }
}
