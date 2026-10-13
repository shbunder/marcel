import XCTest
@testable import AvatarKit

/// S-19.3 (B-44): each agent's own sheet from the hub, kept on disk, the giraffe until then and offline.
@MainActor
final class AvatarSheetsTests: XCTestCase {
    /// A sheet the Pixel Zoo drew (`python -m zoo sheet --animal fox --color tangerine --personality cheerful`).
    static let fixtures = URL(fileURLWithPath: #filePath).deletingLastPathComponent().appending(path: "Fixtures")

    static func fox(etag: String? = "lk-fox-tangerine-cheerful") throws -> AvatarSheets.Download {
        AvatarSheets.Download(json: try Data(contentsOf: fixtures.appending(path: "fox-tangerine-cheerful.json")),
                              png: try Data(contentsOf: fixtures.appending(path: "fox-tangerine-cheerful.png")),
                              etag: etag)
    }

    /// What the fake hub was asked: agent id and If-None-Match.
    final class Asked: @unchecked Sendable {
        var calls: [(String, String?)] = []
    }

    private var directory: URL!

    override func setUp() async throws {
        directory = FileManager.default.temporaryDirectory.appending(path: "avatars-\(UUID().uuidString)")
    }

    override func tearDown() async throws {
        try? FileManager.default.removeItem(at: directory)
    }

    func testAZooSheetFromTheHubReadsAndPlaysEveryState() throws {
        let fox = try Self.fox()
        let sheet = try SpriteSheet(json: fox.json, png: fox.png)
        XCTAssertEqual(sheet.name, "fox")
        XCTAssertEqual(sheet.frameWidth, 32)
        for state in [AvatarState.idle, .thinking, .working(1), .working(2), .working(3), .needsYou, .done] {
            XCTAssertNotNil(sheet.loops[state.loopName], "the fox has a \(state.loopName) loop")
        }
        XCTAssertThrowsError(try SpriteSheet(json: fox.json, png: Data("not a png".utf8)))
    }

    func testAnAgentPlaysTheGiraffeUntilItsSheetArrivesThenItsOwn() async throws {
        let fox = try Self.fox()
        let sheets = AvatarSheets(directory: directory) { _, _ in fox }
        XCTAssertTrue(sheets.sheet(for: "agt_emma") === SpriteSheet.giraffe)
        XCTAssertTrue(sheets.sheet(for: nil) === SpriteSheet.giraffe)
        await sheets.refresh(agentId: "agt_emma")
        XCTAssertEqual(sheets.sheet(for: "agt_emma").name, "fox")
        XCTAssertTrue(sheets.sheet(for: "agt_marcel") === SpriteSheet.giraffe, "another agent is untouched")
    }

    func testOfflineStartWithNothingKeptShowsTheGiraffe() async {
        let sheets = AvatarSheets(directory: directory) { _, _ in throw URLError(.notConnectedToInternet) }
        await sheets.refresh(agentId: "agt_emma")
        XCTAssertTrue(sheets.sheet(for: "agt_emma") === SpriteSheet.giraffe)
    }

    func testTheSheetIsKeptForNextLaunchAndOfflineStillPlaysIt() async throws {
        let fox = try Self.fox()
        await AvatarSheets(directory: directory) { _, _ in fox }.refresh(agentId: "agt_emma")

        let offline = AvatarSheets(directory: directory) { _, _ in throw URLError(.notConnectedToInternet) }
        XCTAssertEqual(offline.sheet(for: "agt_emma").name, "fox", "read from disk at start, before any fetch")
        await offline.refresh(agentId: "agt_emma")
        XCTAssertEqual(offline.sheet(for: "agt_emma").name, "fox", "a failed fetch keeps what the phone has")
    }

    func testTheKnownETagIsSentAndASameETagFromAnEventFetchesNothing() async throws {
        let fox = try Self.fox()
        let asked = Asked()
        let sheets = AvatarSheets(directory: directory) { id, etag in
            asked.calls.append((id, etag))
            return etag == fox.etag ? nil : fox
        }
        await sheets.refresh(agentId: "agt_emma")
        XCTAssertNil(asked.calls.last?.1, "nothing kept yet: no If-None-Match")
        await sheets.refresh(agentId: "agt_emma")
        XCTAssertEqual(asked.calls.last?.1, "lk-fox-tangerine-cheerful", "sends the ETag it has; 304 keeps the sheet")
        XCTAssertEqual(sheets.sheet(for: "agt_emma").name, "fox")
        let before = asked.calls.count
        await sheets.refresh(agentId: "agt_emma", etag: "lk-fox-tangerine-cheerful")
        XCTAssertEqual(asked.calls.count, before, "agent.updated with the ETag the phone has: no fetch")
        await sheets.refresh(agentId: "agt_emma", etag: "lk-panda-teal-cozy")
        XCTAssertEqual(asked.calls.count, before + 1, "a new ETag fetches again")
    }

    func testAnAgentIdThatIsNotAFileNameIsNeverWritten() async throws {
        let fox = try Self.fox()
        let sheets = AvatarSheets(directory: directory) { _, _ in fox }
        await sheets.refresh(agentId: "a.b")
        await sheets.refresh(agentId: "agt 1")
        await sheets.refresh(agentId: "../escape")
        XCTAssertEqual(sheets.sheet(for: "../escape").name, "fox", "plays in memory")
        let folder = directory.path(percentEncoded: false)
        let written = (try? FileManager.default.contentsOfDirectory(atPath: folder)) ?? []
        XCTAssertTrue(written.isEmpty, "but nothing lands outside the folder")
        XCTAssertFalse(FileManager.default.fileExists(
            atPath: directory.deletingLastPathComponent().appending(path: "escape.json").path(percentEncoded: false)))
    }
}
