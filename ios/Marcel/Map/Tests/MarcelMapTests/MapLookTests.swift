import Foundation
import XCTest

@testable import MarcelMap

/// S-18.6: "Open tracker" only for a project with a tracker (B-46), and the room's notes preview shows what quests
/// wrote under `## Notes`, never the project brief's headings (S-20.1 follow-up).
@MainActor
final class MapLookTests: XCTestCase {
    func testOpenTrackerShowsOnlyWithATracker() throws {
        let fed = try FedCache.seeded()
        let tiles = Dictionary(uniqueKeysWithValues: try fed.map().tiles.map { ($0.id, $0) })
        XCTAssertEqual(tiles["prj_dev"]?.tracker, URL(string: "https://claude.ai/artifact/marcel-tracker"))
        for other in ["prj_energy", "prj_family", "prj_health", "prj_inbox"] {
            XCTAssertNil(tiles[other]?.tracker, "\(other) has no tracker")
        }
        XCTAssertEqual(try fed.room("prj_dev")?.tracker, URL(string: "https://claude.ai/artifact/marcel-tracker"))
        XCTAssertNil(try fed.room("prj_energy")?.tracker)
    }

    func testATrackerThatIsNotAWebAddressShowsNothing() throws {
        let fed = try FedCache()
        try fed.apply("project.created", Fx.projectJSON("prj_odd", "Odd", tracker: "javascript:alert(1)"))
        XCTAssertNil(try fed.map().tiles.first?.tracker)
        XCTAssertNil(try fed.room("prj_odd")?.tracker)
    }

    func testTheNotesPreviewShowsWhatIsUnderNotes() {
        let view = NotesView(state: MapSnapshotTests.briefNotes)
        let lines = view.lines
        XCTAssertEqual(lines.count, 2, "\(lines)")
        XCTAssertTrue(lines[0].contains("private simulator"))
        XCTAssertTrue(lines[1].contains("Contracts change only through the lead"))
        for heading in ["What it is for", "Where the work happens", "How to check the work", "(not known yet)",
                        NotesPreview.placeholder] {
            XCTAssertFalse(lines.contains { $0.contains(heading) }, "the preview shows \(heading)")
        }
    }

    func testABriefWithNoNotesYetSaysSo() {
        let brief = """
            # Garden
            ## What it is for
            A vegetable patch by May
            ## Notes
            What later threads in this project need to know.
            """
        XCTAssertEqual(NotesView(state: .text(brief)).lines,
                       ["No notes yet. Marcel writes down what every quest here should know."])
    }

    func testNotesStopAtTheNextHeadingOfTheSameLevel() {
        let notes = "## Notes\n- Keep this\n### Detail\n- And this\n## Later\n- Not this"
        XCTAssertEqual(NotesPreview.lines(notes), ["- Keep this", "### Detail", "- And this"])
    }

    func testAnOlderNotesFileIsShownFromTheTop() {
        XCTAssertEqual(NotesPreview.lines("# Home energy\n- Supplier A from 1 November."),
                       ["# Home energy", "- Supplier A from 1 November."])
    }
}
