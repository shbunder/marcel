import Foundation
import MarcelChat
import MarcelKit
import SwiftUI
import UIKit
import XCTest

@testable import MarcelMap

/// The map and a room as wireframes 5A and 5B draw them, compared pixel for pixel with `__Snapshots__/*.png` (light,
/// dark and the largest text size). `ImageRenderer` draws nothing inside a `ScrollView`, so these render `MapStill`
/// and `RoomStill`: the same header, tiles, cards and composer, in a plain stack.
@MainActor
final class MapSnapshotTests: XCTestCase {
    /// Wireframe 5A: Home energy waits on you, Family, Marcel dev linked to Claude Code, Health, the inbox.
    static var wireframeMap: ProjectMap {
        get throws { try FedCache.seeded().map() }
    }

    /// Wireframe 5B: Home energy's room with its board, a quest waiting, one working, one done, two items, notes.
    static var wireframeRoom: ProjectRoom {
        get throws { try XCTUnwrap(try FedCache.seeded().room("prj_energy")) }
    }

    static let boardRows: BoardState = .rows([
        PreviewRow("Contract switched (−€486/yr)"), PreviewRow("Solar quote Friday"),
        PreviewRow("Heat pump: wait for subsidy news"),
    ])

    static let notes: NotesState = .text("""
        # Home energy
        - Supplier A from 1 November, €486 a year less.
        - Solar: three installers asked, two replied.
        - Heat pump only with the 2027 subsidy.
        """)

    func testTheMapLooksLikeWireframe5A() throws {
        Snapshot.assert(MapStill(map: try Self.wireframeMap), named: "Map", padding: 0)
    }

    func testAnEmptyMapWithAProblem() {
        Snapshot.assert(MapStill(map: ProjectMap(projects: [], now: Fx.now),
                                 problem: "Couldn't reach the hub. Check the connection and try again."),
                        named: "Map-empty", variants: [.light], padding: 0)
    }

    func testARoomLooksLikeWireframe5B() throws {
        Snapshot.assert(RoomStill(room: try Self.wireframeRoom, board: Self.boardRows, notes: Self.notes),
                        named: "Room", padding: 0)
    }

    func testARoomAfterAMessageWasSentAndAMoveWasRefused() throws {
        Snapshot.assert(
            RoomStill(room: try Self.wireframeRoom, board: .loading, notes: NotesState.empty,
                      draft: "And the heat pump?",
                      problem: "That project is archived, so the quest stayed where it was.",
                      sent: "Sent to Marcel about Home energy. The answer comes in Chat."),
            named: "Room-sent", variants: [.light, .dark], padding: 0)
    }

    /// A coding project with a tracker (B-46): "Open tracker" over its board.
    func testACodingRoomWithATracker() throws {
        let room = try XCTUnwrap(try FedCache.seeded().room("prj_dev"))
        Snapshot.assert(RoomStill(room: room, board: .rows([PreviewRow("PR #41 · CI green")]), notes: Self.briefNotes),
                        named: "Room-tracker", variants: [.light, .dark], padding: 0)
    }

    /// A notes file with the ADR-011 brief: the room previews only what quests wrote under `## Notes`.
    static let briefNotes: NotesState = .text("""
        # Marcel dev

        ## What it is for

        The home assistant the family talks to

        ## Where the work happens

        shbunder/marcel

        ## How to check the work

        `make check`

        ## What needs a go-ahead first

        (not known yet)

        ## Notes

        What later threads in this project need to know.
        - 2026-10-12 The iOS gate needs a private simulator (from Fix the board).
        - 2026-10-13 **Contracts** change only through the lead (from Add mentions).
        """)

    /// S-12.14: a quest in each of Claude Code Projects' groups, in their order.
    func testARoomWithAQuestInEachGroup() throws {
        Snapshot.assert(RoomStill(room: try RoomGroupTests.everyGroupRoom(), board: .rows([PreviewRow("PR #14 waits")]),
                                  notes: NotesState.empty),
                        named: "Room-groups", variants: [.light, .dark], padding: 0)
    }

    func testAnEmptyRoom() throws {
        let fed = try FedCache()
        try fed.apply("project.created", Fx.projectJSON("prj_garden", "Garden", goal: "A vegetable patch by May"))
        let room = try XCTUnwrap(try fed.room("prj_garden"))
        Snapshot.assert(RoomStill(room: room, board: .rows([PreviewRow("Nothing waits on you.")]),
                                  notes: NotesState.empty),
                        named: "Room-empty", variants: [.light], padding: 0)
    }
}

/// What VoiceOver reads on the map and in a room, from the accessibility tree SwiftUI builds (as PixelKit's audit
/// does, since XCUITest's audit cannot run on this Mac).
@MainActor
final class MapAccessibilityTests: XCTestCase {
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

    private func assertPlainWords(_ labels: [String], _ size: DynamicTypeSize, file: StaticString = #filePath,
                                  line: UInt = #line) {
        for label in labels {
            XCTAssertFalse(label.isEmpty, "\(size): an element has no label", file: file, line: line)
            XCTAssertNil(label.first { "▶▼♥◀▸▾·↗→".contains($0) }, "\(size): \"\(label)\" has a symbol",
                         file: file, line: line)
            // "NUC" is the home server's name, said as one word; everything else is in sentence case.
            let shouted = label.split { !$0.isLetter }
                .first { $0.count >= 3 && $0 == $0.uppercased() && $0 != $0.lowercased() && $0 != "NUC" }
            XCTAssertNil(shouted, "\(size): \"\(label)\" is in capitals", file: file, line: line)
        }
    }

    func testEveryTileReadsAsOneSentenceInPlainWords() throws {
        let map = try MapSnapshotTests.wireframeMap
        for size in [DynamicTypeSize.large, .accessibility5] {
            let found = elements(of: MapStill(map: map), dynamicType: size)
            let labels = found.map(\.label)
            for tile in map.tiles {
                XCTAssertTrue(labels.contains(tile.spoken), "\(size): no element reads \"\(tile.spoken)\"")
            }
            XCTAssertTrue(labels.contains("New project"))
            XCTAssertTrue(found.contains { $0.label == "Map of your projects" && $0.traits.contains(.header) })
            assertPlainWords(labels, size)
            let tile = found.first { $0.label == map.tiles[0].spoken }
            XCTAssertGreaterThanOrEqual(tile?.frame.height ?? 0, 44, "\(size): a tile is a comfortable target")
        }
    }

    /// S-18.6: a tile opens its room on a tap, not as a Button (it holds "Open tracker"), so VoiceOver must still find
    /// the tile's summary as a button that opens the room, and the tracker as a button of its own.
    func testATileIsAButtonForVoiceOverAndTheTrackerIsAnother() throws {
        let map = try MapSnapshotTests.wireframeMap
        let dev = try XCTUnwrap(map.tiles.first { $0.id == "prj_dev" })
        let found = elements(of: TileView(tile: dev, open: {}).frame(width: 180))
        let summary = found.first { $0.label == dev.spoken }
        XCTAssertTrue(summary?.traits.contains(.button) ?? false, "the tile's summary is not a button")
        XCTAssertTrue(found.contains { $0.label == "Open tracker" && $0.traits.contains(.button) })
    }

    /// S-12.14: the five group headings, in order, as VoiceOver headings, each with its count.
    func testTheRoomsGroupHeadingsAreHeadingsInOrder() throws {
        let found = elements(of: RoomStill(room: try RoomGroupTests.everyGroupRoom(), board: .loading,
                                           notes: NotesState.empty))
        let headers = found.filter { $0.traits.contains(.header) }.map(\.label)
        let groups = headers.filter { $0.hasSuffix(", 1 quest") }
        XCTAssertEqual(groups, ["Needs you, 1 quest", "Working, 1 quest", "Ready for review, 1 quest", "Done, 1 quest",
                                "Ended, 1 quest"], "\(headers)")
    }

    func testTheRoomReadsInPlainWords() throws {
        let room = try MapSnapshotTests.wireframeRoom
        for size in [DynamicTypeSize.large, .accessibility5] {
            let found = elements(of: RoomStill(room: room, board: MapSnapshotTests.boardRows,
                                               notes: MapSnapshotTests.notes), dynamicType: size)
            let labels = found.map(\.label)
            XCTAssertTrue(labels.contains("Back to the map"))
            XCTAssertTrue(labels.contains("Home energy. Lower the bill by 20% this year"))
            XCTAssertTrue(labels.contains("Project board. Pinned, version 7. Contract switched (−€486/yr). "
                + "Solar quote Friday. Heat pump: wait for subsidy news"), "\(size): \(labels)")
            XCTAssertTrue(labels.contains("Document: Comparison"))
            XCTAssertTrue(labels.contains { $0.hasPrefix("Notes: Home energy") })
            for row in room.quests.sections.flatMap(\.rows) {
                XCTAssertTrue(labels.contains(RoomQuest(row: row, moving: false).spoken), "\(size): \(row.title)")
            }
            assertPlainWords(labels, size)
            let headers = found.filter { $0.traits.contains(.header) }.map(\.label)
            XCTAssertTrue(headers.contains("Needs you, 1 quest") && headers.contains("Done, 1 quest") && headers.contains("Items") && headers.contains("Notes"),
                          "\(size): section titles are headers: \(headers)")
        }
    }
}
