import Foundation
import MarcelKit
import XCTest

@testable import MarcelNotebook

/// S-22.30 (B-49): unsaved edits and the rollovers this phone saw belong to one buddy. A draft of Marcel's `MEMORY.md`
/// never shows on Pip's (where "keep mine" would overwrite Pip's note with Marcel's words); drafts an older build kept
/// move to the buddy shown; one buddy's rollovers are not in another's notebook.
@MainActor
final class BuddyDraftTests: XCTestCase {
    func testAMarcelDraftNeverAppearsOnPipsNote() async {
        let defaults = UserDefaults.scratch()
        // Both stores open first, so no draft moving can hide a shared key.
        let marcel = DraftStore(defaults: defaults, buddy: "agt_marcel")
        let pip = DraftStore(defaults: defaults, buddy: "agt_pip")
        marcel.keep(Draft(text: "Marcel's unsaved words", base: FakeHub.blob(Fx.aboutYou), at: Fx.at(-60)),
                    for: .aboutYou)
        XCTAssertNil(pip.draft(for: .aboutYou), "Pip's MEMORY.md has no draft")
        XCTAssertEqual(pip.paths, [])
        XCTAssertNotNil(defaults.object(forKey: "notebook.draft.agt_marcel:MEMORY.md"), "kept under Marcel")

        // Each keeps its own draft of the same note: neither overwrites the other.
        pip.keep(Draft(text: "Pip's unsaved words", base: nil, at: Fx.at(-30)), for: .aboutYou)
        XCTAssertEqual(marcel.draft(for: .aboutYou)?.text, "Marcel's unsaved words")
        XCTAssertEqual(pip.draft(for: .aboutYou)?.text, "Pip's unsaved words")
        pip.discard(.aboutYou)
        XCTAssertEqual(marcel.draft(for: .aboutYou)?.text, "Marcel's unsaved words", "discarding Pip's leaves Marcel's")

        // Opened as the screen opens it: Pip's note shows Pip's words, with nothing unsaved to keep.
        let hub = FakeHub()
        let editor = NoteEditorModel(path: .aboutYou, title: "About you", service: hub.service, drafts: pip,
                                     text: Fx.text, clock: { Fx.at(-30) })
        await editor.load()
        XCTAssertEqual(editor.text, Fx.aboutYou)
        XCTAssertFalse(editor.canSave, "nothing of Marcel's to save over Pip's note")
    }

    func testFiftyMarcelDraftsDoNotPushOutPips() {
        let defaults = UserDefaults.scratch()
        let marcel = DraftStore(defaults: defaults, buddy: "agt_marcel", maxDrafts: 3)
        let pip = DraftStore(defaults: defaults, buddy: "agt_pip", maxDrafts: 3)
        pip.keep(Draft(text: "Pip's", base: nil, at: Fx.at(-1000)), for: .aboutYou)
        for n in 0..<5 {
            marcel.keep(Draft(text: "\(n)", base: nil, at: Fx.at(Double(n))), for: MemoryPath("memory/n\(n).md")!)
        }
        XCTAssertEqual(marcel.paths.count, 3, "Marcel's limit is Marcel's")
        XCTAssertEqual(pip.draft(for: .aboutYou)?.text, "Pip's", "Pip's oldest draft is not Marcel's to drop")
    }

    func testOldDraftsMoveToTheBuddyShownAndUnpairingForgetsEveryBuddys() throws {
        let defaults = UserDefaults.scratch()
        // A draft as an older build kept it: no buddy in the key.
        let old = Draft(text: "Typed before buddies", base: nil, at: Fx.at(-600))
        defaults.set(try JSONEncoder().encode(old), forKey: "notebook.draft.MEMORY.md")

        let marcel = DraftStore(defaults: defaults, buddy: "agt_marcel")
        XCTAssertEqual(marcel.draft(for: .aboutYou), old, "it moved to the buddy shown")
        XCTAssertNil(defaults.object(forKey: "notebook.draft.MEMORY.md"), "and left the old key")
        XCTAssertNil(DraftStore(defaults: defaults, buddy: "agt_pip").draft(for: .aboutYou), "not to anyone else")

        XCTAssertEqual(DraftStore(defaults: defaults, buddy: "agt_marcel").draft(for: .aboutYou), old,
                       "opening again moves nothing")
        DraftStore(defaults: defaults, buddy: "agt_pip")
            .keep(Draft(text: "Pip's", base: nil, at: Fx.at(-1)), for: .aboutYou)
        DraftStore(defaults: defaults).discardAll()
        XCTAssertNil(marcel.draft(for: .aboutYou))
        XCTAssertNil(DraftStore(defaults: defaults, buddy: "agt_pip").draft(for: .aboutYou))
    }

    func testEachBuddysRolloversStayInItsOwnNotebook() throws {
        let defaults = UserDefaults.scratch()
        let cache = MarcelCache(container: try MarcelCache.makeContainer(inMemory: true))
        let log = RolloverLog(defaults: defaults)
        log.attach(to: cache)
        try cache.apply(Fx.rollover(id: 10, at: Fx.at(-3600)))
        try cache.apply(Fx.rollover(id: 11, at: Fx.at(-60), reason: "context_full", agent: "agt_pip"))
        XCTAssertEqual(log.rollovers(for: "agt_marcel").map(\.id), ["event-10"])
        XCTAssertEqual(log.rollovers(for: "agt_pip").map(\.id), ["event-11"])

        // Ten per buddy: Marcel's eleventh drops his oldest, not Pip's.
        for n in 0..<10 { try cache.apply(Fx.rollover(id: 20 + n, at: Fx.at(Double(n)))) }
        XCTAssertEqual(log.rollovers(for: "agt_marcel").count, RolloverLog.keep)
        XCTAssertFalse(log.rollovers(for: "agt_marcel").contains { $0.id == "event-10" })
        XCTAssertEqual(log.rollovers(for: "agt_pip").map(\.id), ["event-11"])
    }

    func testRolloversAnOlderBuildKeptMoveToTheBuddyTheNotebookOpensOn() throws {
        let defaults = UserDefaults.scratch()
        let old = [Rollover(id: "event-5", at: Fx.at(-86_400), reason: "daily", kept: "notes")]
        defaults.set(try JSONEncoder().encode(old), forKey: "notebook.rollovers")
        let log = RolloverLog(defaults: defaults)
        _ = NotebookModel(service: FakeHub().service, buddy: "agt_pip", drafts: DraftStore(defaults: defaults),
                          rollovers: log)
        XCTAssertEqual(log.rollovers(for: "agt_pip").map(\.id), ["event-5"])
        XCTAssertEqual(log.rollovers(for: "agt_marcel"), [])
        XCTAssertEqual(RolloverLog(defaults: defaults).rollovers(for: "agt_pip").map(\.id), ["event-5"], "kept so")
    }

    func testMarcelsNotebookCarriesOnlyMarcelsRollovers() async throws {
        let defaults = UserDefaults.scratch()
        let cache = MarcelCache(container: try MarcelCache.makeContainer(inMemory: true))
        let log = RolloverLog(defaults: defaults)
        log.attach(to: cache)
        try cache.apply(Fx.rollover(id: 10, at: Fx.at(-3600)))
        try cache.apply(Fx.rollover(id: 11, at: Fx.at(-60), reason: "context_full", agent: "agt_pip"))
        let model = NotebookModel(service: FakeHub().service, buddy: "agt_marcel",
                                  drafts: DraftStore(defaults: defaults, buddy: "agt_marcel"), rollovers: log)
        await model.load()
        XCTAssertEqual(model.carried.map(\.id), ["event-10"], "Pip's fresh start is not in Marcel's notebook")
    }

    func testAnOpenNotebookStaysOnItsBuddyThroughASwitch() async throws {
        // The service follows the buddy shown; a notebook pins it when it opens, so a switch meanwhile (another
        // phone, a push) cannot make its reads and saves another buddy's while its drafts stay this one's.
        let hub = FakeHub()
        let name = "BuddyDraftTests-pin-\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: name)!
        addTeardownBlock { defaults.removePersistentDomain(forName: name) }
        let shown = CurrentBuddy(defaults: defaults)
        shown.show("agt_marcel")
        let service = LiveNotebookService(client: hub.client, buddy: shown)
        let model = NotebookModel(service: service, buddy: "agt_marcel", drafts: DraftStore(defaults: .scratch()),
                                  rollovers: RolloverLog(defaults: .scratch()))
        shown.show("agt_pip")
        await model.load()
        XCTAssertEqual(hub.memoryBuddies, ["agt_marcel", "agt_marcel"], "the tree and About you: still Marcel's")
    }
}
