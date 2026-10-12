import Foundation
import MarcelKit
import XCTest

@testable import MarcelNotebook

/// The review of PR #58: unpairing forgets drafts and rollovers, stale-but-equal drafts go away, an offline draft says
/// what it is, and drafts are bounded.
@MainActor
final class ReviewFixTests: XCTestCase {
    final class Token: TokenStore, @unchecked Sendable {
        var value: String? = "mrc_test"
        func token() throws -> String? { value }
        func save(_ token: String) throws { value = token }
        func delete() throws { value = nil }
    }

    private func editor(_ hub: FakeHub, _ path: String = "MEMORY.md", drafts: DraftStore) -> NoteEditorModel {
        NoteEditorModel(path: MemoryPath(path)!, title: "About you", service: hub.service, drafts: drafts,
                        text: Fx.text, clock: { Fx.at(-300) })
    }

    // MARK: Unpairing

    /// The app's wiring (`RolloverLog.attach(to: connection)`): unpairing — by the person or because the hub refused
    /// the phone, both go through `HubConnection.unpair` — forgets the rollover log and every notebook draft.
    func testUnpairingForgetsDraftsAndRollovers() throws {
        let defaults = UserDefaults.scratch()
        let hubStore = UserDefaultsPairedHubStore(defaults: defaults)
        try hubStore.save(PairedHub(serverURL: URL(string: "http://hub.test/api")!, deviceId: "dev_phone",
                                    userId: "usr_owner"))
        let cache = MarcelCache(container: try MarcelCache.makeContainer(inMemory: true))
        let connection = HubConnection(tokenStore: Token(), hubStore: hubStore, cache: cache, transport: FakeHub(),
                                       connectorFactory: { _ in fatalError("the stream is not started here") })
        let drafts = DraftStore(defaults: defaults)
        drafts.keep(Draft(text: "x", base: nil, at: Fx.now), for: .aboutYou)
        drafts.keep(Draft(text: "y", base: nil, at: Fx.now), for: MemoryPath("memory/2026-10-04.md")!)
        let log = RolloverLog(defaults: defaults)
        log.attach(to: connection, drafts: drafts)
        try cache.apply(Fx.rollover(id: 1, at: Fx.now))
        XCTAssertEqual(log.rollovers.count, 1)

        connection.unpair()

        XCTAssertTrue(drafts.paths.isEmpty, "drafts belong to the hub that was forgotten")
        XCTAssertTrue(log.rollovers.isEmpty)
        XCTAssertTrue(RolloverLog(defaults: defaults).rollovers.isEmpty, "and they are gone from the phone too")
    }

    // MARK: A draft the hub already has

    func testADraftWithTheHubsWordsIsNotUnsavedChanges() async {
        let hub = FakeHub()
        let drafts = DraftStore(defaults: .scratch())
        // Started on an older version, but the hub now has exactly these words (saved from another phone).
        drafts.keep(Draft(text: Fx.energy, base: "0ld", at: Fx.at(-86_400)),
                    for: MemoryPath("memory/projects/energy.md")!)
        drafts.keep(Draft(text: "really unsaved", base: "0ld", at: Fx.at(-86_400)), for: .aboutYou)
        let model = NotebookModel(service: hub.service, drafts: drafts,
                                  rollovers: RolloverLog(defaults: .scratch("rollovers")),
                                  projectNames: { Fx.projectNames }, text: Fx.text)
        await model.load()
        XCTAssertEqual(drafts.paths, [.aboutYou])
        XCTAssertEqual(model.detail(model.projects[0]), "Updated today 16:40")
        XCTAssertEqual(model.detail(model.aboutYou), "Updated yesterday 18:40 · unsaved changes")
    }

    func testOpeningANoteWhoseDraftMatchesTheHubDropsTheDraft() async {
        let hub = FakeHub()
        let drafts = DraftStore(defaults: .scratch())
        drafts.keep(Draft(text: Fx.aboutYou, base: "0ld", at: Fx.at(-86_400)), for: .aboutYou)
        let model = editor(hub, drafts: drafts)
        await model.load()
        XCTAssertNil(drafts.draft(for: .aboutYou))
        XCTAssertNil(model.notice, "no \"unsaved changes are back\"")
        XCTAssertNil(model.conflict)
        XCTAssertEqual(model.mode, .preview)
    }

    // MARK: Offline with a draft

    func testOfflineWithADraftOfAnExistingNoteSaysItIsNotSavedYet() async {
        let hub = FakeHub()
        let defaults = UserDefaults.scratch()
        let first = editor(hub, drafts: DraftStore(defaults: defaults))
        await first.load()
        first.text = "typed on the train\n"
        hub.offline = true
        let model = editor(hub, drafts: DraftStore(defaults: defaults))
        await model.load()
        XCTAssertEqual(model.line, "Not saved yet: the hub can't be reached. Your draft from today 18:35.")
        hub.offline = false
        await model.save()
        XCTAssertEqual(model.line, "Updated today 18:40 in the app")
    }

    // MARK: Bounds

    func testADraftOverOneMegabyteIsNotKeptAndSaysWhy() async {
        let hub = FakeHub()
        let drafts = DraftStore(defaults: .scratch())
        let model = editor(hub, drafts: drafts)
        await model.load()
        model.text = "small change"
        XCTAssertEqual(drafts.draft(for: .aboutYou)?.text, "small change")
        model.text = String(repeating: "a", count: DraftStore.maxBytes + 1)
        XCTAssertEqual(drafts.draft(for: .aboutYou)?.text, "small change", "the last draft that fit stays")
        XCTAssertEqual(model.problem, "This note is over 1 MB: the phone can't keep it as a draft, and the hub "
                       + "won't save it. Make it shorter.")
    }

    func testAtMostFiftyDraftsTheOldestGoesWithAPlainMessage() async {
        let defaults = UserDefaults.scratch()
        let drafts = DraftStore(defaults: defaults)
        for index in 1...DraftStore.maxDrafts {
            XCTAssertEqual(drafts.keep(Draft(text: "n\(index)", base: nil, at: Fx.at(TimeInterval(index))),
                                       for: MemoryPath("memory/notes/n\(index).md")!), .kept)
        }
        XCTAssertEqual(drafts.paths.count, 50)
        // Changing a note that already has a draft drops nothing.
        XCTAssertEqual(drafts.keep(Draft(text: "again", base: nil, at: Fx.at(3600)), for: MemoryPath("memory/notes/n1.md")!),
                       .kept)

        let model = editor(FakeHub(), drafts: drafts)
        await model.load()
        model.text = "the fifty-first"
        XCTAssertEqual(drafts.paths.count, 50)
        XCTAssertNil(drafts.draft(for: MemoryPath("memory/notes/n2.md")!), "the oldest draft went")
        XCTAssertNotNil(drafts.draft(for: MemoryPath("memory/notes/n1.md")!), "n1 was changed later")
        XCTAssertEqual(drafts.draft(for: .aboutYou)?.text, "the fifty-first")
        XCTAssertEqual(model.problem, "The phone keeps at most 50 unsaved notes, so the oldest one "
                       + "(memory/notes/n2.md) was thrown away.")
    }
}
