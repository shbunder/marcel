import Foundation
import MarcelKit
import XCTest

@testable import MarcelItems

/// The Items tab's grid (wireframe 4C, B-21): grouped by project, newest change first, the words on each tile and
/// the "updated" badge on living items.
@MainActor
final class ItemsLibraryTests: XCTestCase {
    func testItemsAreGroupedByProjectTheNewestChangeFirst() throws {
        let library = try FedCache.seeded().library()
        XCTAssertEqual(library.sections.map(\.name), ["Energy contracts", "Marcel dev", "Inbox"])
        XCTAssertEqual(library.sections[0].tiles.map(\.id),
                       [Fx.boardId, Fx.docId, Fx.htmlId, Fx.fileId, Fx.linkId], "newest change first")
        XCTAssertEqual(library.sections[1].tiles.map(\.id),
                       [Fx.dashboardId, Fx.branchId, "art_01J9ZT1", "art_01J9ZM2"])
        XCTAssertEqual(library.sections[2].tiles.map(\.title), ["Inbox — board"])
    }

    func testANewVersionMovesItsProjectAndItemToTheTop() throws {
        let fed = try FedCache.seeded()
        var dev = Fx.mockArtifact("art_01J9ZM2")
        dev["current_version"] = 2
        dev["updated_at"] = "2026-10-12T08:15:00Z"
        try fed.apply("artifact.updated", ["artifact": dev, "version": Fx.version("art_01J9ZM2", 2)])
        let library = try fed.library()
        XCTAssertEqual(library.sections.first?.name, "Marcel dev")
        XCTAssertEqual(library.sections.first?.tiles.first?.id, "art_01J9ZM2")
        XCTAssertEqual(library.sections.first?.tiles.first?.badge, "Updated")
    }

    func testEachTileSaysWhatTheItemIsAndWhenItChanged() throws {
        let tiles = Dictionary(uniqueKeysWithValues: try FedCache.seeded().library().sections.flatMap(\.tiles)
            .map { ($0.id, $0) })
        XCTAssertEqual(tiles[Fx.docId]?.detail, "Document · v3")
        XCTAssertEqual(tiles[Fx.docId]?.updated, "Updated 2 min ago")
        XCTAssertEqual(tiles[Fx.boardId]?.detail, "Board · v7")
        XCTAssertEqual(tiles[Fx.htmlId]?.detail, "Web page")
        XCTAssertEqual(tiles[Fx.fileId]?.detail, "PDF · 1.2 MB")
        XCTAssertEqual(tiles[Fx.fileId]?.updated, "Updated yesterday")
        XCTAssertEqual(tiles[Fx.linkId]?.detail, "Link · www.engie.be")
        XCTAssertEqual(tiles[Fx.linkId]?.updated, "Updated on 10 Oct")
        XCTAssertEqual(tiles["art_01J9ZT1"]?.detail, "Pull request #14 · checks passing")
        XCTAssertEqual(tiles[Fx.branchId]?.detail, "Branch · github.com")
        XCTAssertEqual(tiles[Fx.dashboardId]?.detail, "Dashboard · v4")
    }

    func testOnlyLivingItemsThatChangedInTheLastDayWearTheUpdatedBadge() throws {
        let tiles = Dictionary(uniqueKeysWithValues: try FedCache.seeded().library().sections.flatMap(\.tiles)
            .map { ($0.id, $0) })
        XCTAssertEqual(tiles[Fx.docId]?.badge, "Updated", "version 3, two minutes ago")
        XCTAssertEqual(tiles[Fx.boardId]?.badge, "Updated", "a board is living")
        XCTAssertEqual(tiles[Fx.dashboardId]?.badge, "Updated")
        XCTAssertNil(tiles[Fx.htmlId]?.badge, "a first version is not an update")
        XCTAssertNil(tiles["art_01J9ZT1"]?.badge, "the pull request is a first version from 4 Oct")
        XCTAssertNil(tiles["art_01J9ZM2"]?.badge, "a board that has not changed for a week")
    }

    func testTilesReadAsOneSentenceInFullWords() throws {
        let tiles = Dictionary(uniqueKeysWithValues: try FedCache.seeded().library().sections.flatMap(\.tiles)
            .map { ($0.id, $0) })
        XCTAssertEqual(tiles[Fx.docId]?.spoken,
                       "Energy offers compared. Document. Version 3. Updated 2 minutes ago. Recently updated.")
        XCTAssertEqual(tiles["art_01J9ZT1"]?.spoken,
                       "Fix flaky login test: wait on the session cookie. Pull request number 14. Checks passing. "
                       + "Updated on 4 October.")
        XCTAssertEqual(tiles[Fx.fileId]?.spoken, "Bills 2026. PDF file. 1.2 megabytes. Updated yesterday.")
    }

    func testAnItemOfAProjectTheCacheDoesNotHaveYetIsStillShown() {
        let library = ItemsLibrary(artifacts: [Fx.artifact(Fx.html)], projects: [], now: Fx.now, timeZone: Fx.utc)
        XCTAssertEqual(library.sections.map(\.name), ["Other items"])
    }

    func testAnEmptyCacheIsAnEmptyLibrary() throws {
        XCTAssertTrue(try FedCache().library().isEmpty)
    }

    func testTimesInWords() {
        func ago(_ seconds: TimeInterval) -> Ago { Ago(Fx.now.addingTimeInterval(-seconds), now: Fx.now, timeZone: Fx.utc) }
        XCTAssertEqual(ago(20).short, "just now")
        XCTAssertEqual(ago(60).spoken, "1 minute ago")
        XCTAssertEqual(ago(120).short, "2 min ago")
        XCTAssertEqual(ago(120).spoken, "2 minutes ago")
        XCTAssertEqual(ago(3 * 3600).short, "3 h ago")
        XCTAssertEqual(ago(3 * 3600).spoken, "3 hours ago")
        XCTAssertEqual(ago(30 * 3600).short, "yesterday")
        XCTAssertEqual(ago(5 * 86400).short, "on 7 Oct")
        XCTAssertEqual(ago(5 * 86400).spoken, "on 7 October")
    }

    func testSizesInWords() {
        XCTAssertEqual(ItemWords.size(512), "512 B")
        XCTAssertEqual(ItemWords.size(312_000), "312 KB")
        XCTAssertEqual(ItemWords.size(1_234_567), "1.2 MB")
        XCTAssertEqual(ItemWords.size(1_234_567, spoken: true), "1.2 megabytes")
    }
}
