import Foundation
import MarcelKit
import SwiftUI
import UIKit
import XCTest

@testable import MarcelItems

/// The Items tab and every kind of item in the viewer, as wireframes 4C and 4B draw them, compared pixel for pixel with
/// `__Snapshots__/*.png` (light, dark and the largest text size). `ImageRenderer` draws nothing inside a `ScrollView`,
/// a web view or QuickLook, so these render `ItemsStill` and `ViewerStill`: the same header, tiles, bodies and
/// actions in a plain stack, with a web page's words and a file's card where the screen shows WebKit and QuickLook.
@MainActor
final class ItemsSnapshotTests: XCTestCase {
    static var library: ItemsLibrary {
        get throws { try FedCache.seeded().library() }
    }

    /// The viewer for one item at its current version, with its bytes loaded.
    static func viewer(_ json: [String: Any], content: Data?, history: Int? = nil, asking: Bool = false,
                       draft: String = "", sent: String? = nil, problem: String? = nil) -> ViewerStill {
        let artifact = Fx.artifact(json)
        return ViewerStill(
            title: artifact.title,
            line: VersionLine(artifact, showing: nil, versions: [], now: Fx.now, timeZone: Fx.utc),
            body_: ItemBody.make(artifact, contentType: nil, content: content.map(ContentState.loaded)),
            historyCount: history, asking: asking, draft: draft, sent: sent, problem: problem)
    }

    /// Wireframe 4B: the mock hub's document at version 3, with the comparison a worker wrote.
    static var document: ViewerStill { viewer(Fx.mockArtifact(Fx.docId), content: Fx.comparison, history: 3) }

    func testTheItemsTabLooksLikeWireframe4C() throws {
        Snapshot.assert(ItemsStill(library: try Self.library), named: "Items", padding: 0)
    }

    func testAnEmptyItemsTabWithAProblem() {
        Snapshot.assert(ItemsStill(library: ItemsLibrary(artifacts: [], projects: [], now: Fx.now),
                                   problem: "The hub no longer accepts this phone. Pair it again."),
                        named: "Items-empty", variants: [.light], padding: 0)
    }

    func testAMarkdownDocumentLooksLikeWireframe4B() {
        Snapshot.assert(Self.document, named: "Viewer-doc", padding: 0)
    }

    func testTheMockHubsBoard() {
        Snapshot.assert(Self.viewer(Fx.mockArtifact(Fx.boardId), content: Fx.mockHub("board-energy.json"), history: 7),
                        named: "Viewer-board", variants: [.light, .dark], padding: 0)
    }

    func testADashboardWithEveryWidgetKind() {
        Snapshot.assert(Self.viewer(Fx.dashboard, content: Fx.contractDashboards[0], history: 4),
                        named: "Viewer-dashboard", padding: 0)
    }

    func testTheContractsBoardWithSectionsAndNotes() {
        var board = Fx.mockArtifact(Fx.boardId)
        board["title"] = "Energy contracts"
        Snapshot.assert(Self.viewer(board, content: Fx.contractDashboards[1], history: 7),
                        named: "Viewer-board-notes", variants: [.light, .dark], padding: 0)
    }

    func testAPullRequest() {
        Snapshot.assert(Self.viewer(Fx.mockArtifact(Fx.prId), content: nil), named: "Viewer-pr",
                        variants: [.light, .dark], padding: 0)
    }

    func testAnHTMLPage() {
        Snapshot.assert(Self.viewer(Fx.html, content: Fx.htmlContent), named: "Viewer-html", variants: [.light],
                        padding: 0)
    }

    func testAFile() {
        Snapshot.assert(Self.viewer(Fx.file, content: Fx.pdf), named: "Viewer-file", variants: [.light], padding: 0)
    }

    func testABranch() {
        Snapshot.assert(Self.viewer(Fx.branch, content: nil), named: "Viewer-branch", variants: [.light], padding: 0)
    }

    func testABoardThatDoesNotMatchTheSchema() {
        Snapshot.assert(Self.viewer(Fx.mockArtifact(Fx.boardId),
                                    content: Data(#"{"version": 1, "title": "x", "widgets": [{"type": "map"}]}"#.utf8)),
                        named: "Viewer-broken", variants: [.light], padding: 0)
    }

    func testAnOldVersionReadOnlyWithTheHistory() throws {
        let artifact = Fx.artifact(Fx.mockArtifact(Fx.docId))
        let versions: [ArtifactVersion] = (Fx.json(Fx.mockHub("doc-versions.json")) as! [[String: Any]]).map {
            Fx.decode($0)
        }
        Snapshot.assert(
            ViewerStill(title: artifact.title,
                        line: VersionLine(artifact, showing: 2, versions: versions, now: Fx.now, timeZone: Fx.utc),
                        body_: ItemBody.make(artifact, contentType: nil, content: .loaded(Fx.mockHub("doc-v2.md"))),
                        history: versions.map { VersionRow($0, current: 3, now: Fx.now, timeZone: Fx.utc) },
                        shown: 2, historyCount: 3),
            named: "Viewer-old", variants: [.light, .dark], padding: 0)
    }

    func testAskingAboutAnItem() {
        Snapshot.assert(Self.viewer(Fx.mockArtifact(Fx.docId), content: Fx.mockHub("doc-v3.md"), history: 3,
                                    asking: true, draft: "Is Engie cheaper with solar?"),
                        named: "Viewer-ask", variants: [.light, .dark], padding: 0)
        Snapshot.assert(Self.viewer(Fx.mockArtifact(Fx.docId), content: Fx.mockHub("doc-v3.md"), history: 3,
                                    sent: "Sent to Marcel. His answer comes in Chat."),
                        named: "Viewer-sent", variants: [.light], padding: 0)
    }
}

/// What VoiceOver reads in the tab and the viewer, from the accessibility tree SwiftUI builds (as PixelKit's audit
/// does, since XCUITest's audit cannot run on this Mac).
@MainActor
final class ItemsAccessibilityTests: XCTestCase {
    override class func setUp() {
        super.setUp()
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

    private func elements<V: View>(of view: V, dynamicType: DynamicTypeSize = .large)
        -> [(label: String, traits: UIAccessibilityTraits, frame: CGRect)]
    {
        let host = UIHostingController(rootView: view.environment(\.dynamicTypeSize, dynamicType))
        let window = UIWindow(frame: CGRect(x: 0, y: 0, width: 390, height: 5000))
        window.rootViewController = host
        window.makeKeyAndVisible()
        windows.append(window)
        host.view.layoutIfNeeded()
        RunLoop.main.run(until: Date().addingTimeInterval(0.3))
        var found: [(String, UIAccessibilityTraits, CGRect)] = []
        func walk(_ object: NSObject, depth: Int) {
            guard depth < 20 else { return }
            if object.isAccessibilityElement {
                found.append((object.accessibilityLabel ?? "", object.accessibilityTraits, object.accessibilityFrame))
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

    /// Labels are words: no ▶ · ↗ → symbols, nothing in capitals but the names below.
    private func assertPlainWords(_ labels: [String], _ size: DynamicTypeSize, file: StaticString = #filePath,
                                  line: UInt = #line) {
        let names: Set<String> = ["NUC", "PDF", "GB", "MB", "JSON"]
        for label in labels {
            XCTAssertFalse(label.isEmpty, "\(size): an element has no label", file: file, line: line)
            XCTAssertNil(label.first { "▶▼♥◀▸▾·↗→✓".contains($0) }, "\(size): \"\(label)\" has a symbol",
                         file: file, line: line)
            let shouted = label.split { !$0.isLetter }
                .first { $0.count >= 3 && $0 == $0.uppercased() && $0 != $0.lowercased() && !names.contains(String($0)) }
            XCTAssertNil(shouted, "\(size): \"\(label)\" is in capitals", file: file, line: line)
        }
    }

    func testEveryTileReadsAsOneSentenceAndProjectsAreHeaders() throws {
        let library = try ItemsSnapshotTests.library
        for size in [DynamicTypeSize.large, .accessibility5] {
            let found = elements(of: ItemsStill(library: library), dynamicType: size)
            let labels = found.map(\.label)
            for tile in library.sections.flatMap(\.tiles) {
                XCTAssertTrue(labels.contains(tile.spoken), "\(size): no element reads \"\(tile.spoken)\"")
            }
            let headers = found.filter { $0.traits.contains(.header) }.map(\.label)
            XCTAssertEqual(headers, ["Items, by project", "Energy contracts", "Marcel dev", "Inbox"], "\(size)")
            assertPlainWords(labels, size)
            let tile = found.first { $0.label == library.sections[0].tiles[0].spoken }
            XCTAssertGreaterThanOrEqual(tile?.frame.height ?? 0, 44, "\(size): a tile is a comfortable target")
        }
    }

    func testTheViewerReadsInPlainWords() {
        for size in [DynamicTypeSize.large, .accessibility5] {
            let found = elements(of: ItemsSnapshotTests.document, dynamicType: size)
            let labels = found.map(\.label)
            XCTAssertTrue(labels.contains("Back"))
            XCTAssertTrue(labels.contains("Updated 2 minutes ago by Energy contracts, version 3"),
                          "\(size): the version line without the dot: \(labels)")
            XCTAssertTrue(labels.contains("Offer: Current, Per year: €1,496"), "\(size): a table row: \(labels)")
            XCTAssertTrue(labels.contains("Open 2027 subsidy in Safari"))
            XCTAssertTrue(labels.contains("Ask about this"))
            XCTAssertTrue(labels.contains("History, 3 versions"))
            assertPlainWords(labels, size)
            let headers = found.filter { $0.traits.contains(.header) }.map(\.label)
            XCTAssertTrue(headers.contains("Energy offers compared") && headers.contains("Open questions"),
                          "\(size): \(headers)")
            for button in found where button.traits.contains(.button) {
                XCTAssertGreaterThanOrEqual(button.frame.height, 44, "\(size): \(button.label) is too small")
            }
        }
    }

    func testDashboardsReadInPlainWords() {
        let found = elements(of: ItemsSnapshotTests.viewer(Fx.dashboard, content: Fx.contractDashboards[0]))
        let labels = found.map(\.label)
        XCTAssertTrue(labels.contains("Disk free, 412 GB, −12 GB this week"), "\(labels)")
        XCTAssertTrue(labels.contains("Volume: plex_media_cache, Size: 38.2 GB"))
        XCTAssertTrue(labels.contains("Bar chart, Growth per week, in GB. plex_media_cache: w37 30.1, w38 33.4, "
            + "w39 35.9, w40 38.2."))
        XCTAssertTrue(labels.contains("To do: Prune dangling images (6.3 GB)"))
        XCTAssertTrue(labels.contains("Plex cache grows ~2.5 GB a week."))
        assertPlainWords(labels, .large)

        var board = Fx.mockArtifact(Fx.boardId)
        board["title"] = "Energy contracts"
        let notes = elements(of: ItemsSnapshotTests.viewer(board, content: Fx.contractDashboards[1])).map(\.label)
        XCTAssertTrue(notes.contains("Done: Collect the offers"), "\(notes)")
        XCTAssertTrue(notes.contains { $0.hasPrefix("Note from Marcel: ") }, "\(notes)")
    }

    func testAPullRequestReadsAsOneSentence() {
        let labels = elements(of: ItemsSnapshotTests.viewer(Fx.mockArtifact(Fx.prId), content: nil)).map(\.label)
        XCTAssertTrue(labels.contains("Pull request number 14. Fix flaky login test: wait on the session cookie. Open. "
            + "In shbunder/marcel. Checks passing. Review requested"), "\(labels)")
        XCTAssertTrue(labels.contains("Open in GitHub"))
    }
}
