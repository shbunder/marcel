import Foundation
import MarcelKit
import XCTest

@testable import MarcelNotebook

/// S-22.29 (app API 0.8.1, B-49): the Notebook is the buddy shown. Every memory request names it as `agent_id`, a
/// switch moves the next one to the new buddy, and a buddy that is gone (or archived, when saving) shows the "no buddy"
/// state.
@MainActor
final class NotebookBuddyTests: XCTestCase {
    private func setUp(_ hub: FakeHub) -> (LiveNotebookService, CurrentBuddy) {
        var me = Fx.me
        var pip = (me["agents"] as! [[String: Any]])[0]
        pip["id"] = "agt_pip"
        pip["name"] = "Pip"
        pip["main_conversation_id"] = "cnv_main_pip"
        me["agents"] = (me["agents"] as! [[String: Any]]) + [pip]
        hub["GET /me"] = (200, Fx.json(me))
        let name = "NotebookBuddyTests-\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: name)!
        addTeardownBlock { defaults.removePersistentDomain(forName: name) }
        let buddy = CurrentBuddy(defaults: defaults)
        buddy.show("agt_marcel")
        return (LiveNotebookService(client: hub.client, buddy: buddy), buddy)
    }

    func testEveryMemoryRequestNamesTheBuddyShownAndASwitchMovesTheNextOne() async throws {
        let hub = FakeHub()
        let (service, buddy) = setUp(hub)
        _ = try await service.listing()
        let path = try XCTUnwrap(MemoryPath("MEMORY.md"))
        let file = try await service.file(path)
        _ = try await service.save(path, content: file.content + "- Likes tea.\n", ifMatch: file.etag)
        XCTAssertEqual(hub.memoryBuddies, ["agt_marcel", "agt_marcel", "agt_marcel"], "the tree, the read and the save")

        buddy.show("agt_pip")
        _ = try await service.listing()
        XCTAssertEqual(hub.memoryBuddies.last, "agt_pip", "after a switch, the next tree is Pip's")
    }

    func testABuddyThatIsGoneOrArchivedShowsTheNoBuddyState() async throws {
        let hub = FakeHub()
        let (service, _) = setUp(hub)
        let gone = (404, Fx.error("agent_not_found", "No such buddy."))
        hub["GET /memory"] = gone
        do {
            _ = try await service.listing()
            XCTFail("listed")
        } catch let error as NotebookError {
            XCTAssertEqual(error, .noAgent)
            XCTAssertEqual(error.errorDescription,
                           "This buddy is gone or archived, so its notebook can't change. Pull down to refresh.")
        }

        // A read for a buddy that is gone.
        hub["GET /memory/files/MEMORY.md"] = gone
        do {
            _ = try await service.file(try XCTUnwrap(MemoryPath("MEMORY.md")))
            XCTFail("read")
        } catch let error as NotebookError {
            XCTAssertEqual(error, .noAgent)
        }
        hub["GET /memory/files/MEMORY.md"] = nil

        // Archived: still readable, but saving is refused.
        hub["PUT /memory/files/MEMORY.md"] = gone
        let path = try XCTUnwrap(MemoryPath("MEMORY.md"))
        let file = try await service.file(path)
        do {
            _ = try await service.save(path, content: "x", ifMatch: file.etag)
            XCTFail("saved")
        } catch let error as NotebookError {
            XCTAssertEqual(error, .noAgent)
        }

        // A note that is gone is still "that note is gone", not "no buddy".
        do {
            _ = try await service.file(try XCTUnwrap(MemoryPath("memory/nope.md")))
            XCTFail("read")
        } catch let error as NotebookError {
            XCTAssertEqual(error, .notFound)
        }
    }

    func testBeforeThePhoneKnowsABuddyItNamesNone() async throws {
        // A first launch offline: no buddy yet, so no `agent_id`; the hub uses this phone's buddy.
        let hub = FakeHub()
        let name = "NotebookBuddyTests-none-\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: name)!
        addTeardownBlock { defaults.removePersistentDomain(forName: name) }
        let service = LiveNotebookService(client: hub.client, buddy: CurrentBuddy(defaults: defaults))
        _ = try await service.listing()
        XCTAssertEqual(hub.memoryBuddies, [nil])
        XCTAssertFalse(hub.requests.contains("GET /me"), "no extra request to find out")
    }
}
