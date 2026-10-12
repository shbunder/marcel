import Foundation
import XCTest

@testable import MarcelNotebook

/// The story's UI test — edit, save, conflict — one layer down: XCUITest cannot run on this Mac's Xcode (no iOS 26.5
/// platform, see ios/README.md), so the editor's model is driven as the screen drives it, against a fake hub that keeps
/// files the way the real one does (etags, `If-Match`, 409 with the current version), through the generated client.
@MainActor
final class NoteEditorTests: XCTestCase {
    private func editor(_ hub: FakeHub, _ path: String = "MEMORY.md", drafts: DraftStore) -> NoteEditorModel {
        NoteEditorModel(path: MemoryPath(path)!, title: "About you", service: hub.service, drafts: drafts,
                        text: Fx.text, clock: { Fx.at(-300) })
    }

    // MARK: Edit and save

    func testEditAndSaveSendsTheEtagItReadAndClearsTheDraft() async {
        let hub = FakeHub()
        let drafts = DraftStore(defaults: .scratch())
        let model = editor(hub, drafts: drafts)
        await model.load()
        XCTAssertEqual(model.state, .ready)
        XCTAssertEqual(model.mode, .preview, "an existing note opens on its preview")
        XCTAssertEqual(model.text, Fx.aboutYou)
        XCTAssertEqual(model.line, "Updated yesterday 18:40 by Marcel")
        XCTAssertFalse(model.canSave, "nothing changed yet")

        let etag = FakeHub.blob(Fx.aboutYou)
        model.mode = .edit
        model.text += "\n- Allergic to cats"
        XCTAssertTrue(model.canSave)
        XCTAssertEqual(drafts.draft(for: .aboutYou)?.text, model.text, "every change is kept as a draft")
        XCTAssertEqual(drafts.draft(for: .aboutYou)?.base, etag)

        await model.save()
        XCTAssertEqual(hub.ifMatches, [etag], "the save names the version it was based on")
        XCTAssertEqual(hub.file("MEMORY.md")?.content, Fx.aboutYou + "\n- Allergic to cats")
        XCTAssertEqual(model.notice, "Saved.")
        XCTAssertNil(model.problem)
        XCTAssertNil(drafts.draft(for: .aboutYou), "a saved note has no draft")
        XCTAssertFalse(model.canSave)
        XCTAssertEqual(model.base, FakeHub.blob(model.text), "the next save is based on what was just saved")
    }

    func testANewNoteIsCreatedWithoutIfMatch() async {
        let hub = FakeHub()
        let drafts = DraftStore(defaults: .scratch())
        let model = editor(hub, "memory/projects/garden.md", drafts: drafts)
        await model.load()
        XCTAssertEqual(model.state, .ready, "a 404 on open is a new note, not a failure")
        XCTAssertEqual(model.mode, .edit)
        XCTAssertEqual(model.line, "New note: saving creates it")
        model.text = "- Plant tulips in October\n"
        await model.save()
        XCTAssertEqual(hub.ifMatches, [nil])
        XCTAssertEqual(hub.file("memory/projects/garden.md")?.content, "- Plant tulips in October\n")
        XCTAssertTrue(hub.requests.contains("PUT /memory/files/memory%2Fprojects%2Fgarden.md"), "\(hub.requests)")
    }

    // MARK: Conflict

    func testAChangeMadeElsewhereIsNeverOverwrittenAndShowsTheDiff() async {
        let hub = FakeHub()
        let drafts = DraftStore(defaults: .scratch())
        let model = editor(hub, drafts: drafts)
        await model.load()
        model.text = Fx.aboutYou.replacingOccurrences(of: "18:30", with: "19:00")
        // Meanwhile Marcel writes the file.
        let theirs = Fx.aboutYou + "\n- Dentist: Tue 9:10"
        hub.write("MEMORY.md", theirs)

        await model.save()
        XCTAssertEqual(hub.file("MEMORY.md")?.content, theirs, "the hub's version is untouched")
        XCTAssertEqual(model.conflict?.content, theirs)
        XCTAssertFalse(model.canSave, "Save is off until a version is chosen")
        XCTAssertNil(model.problem)
        XCTAssertEqual(model.words.changedElsewhere(model.conflict!),
                       "Marcel changed this note today 18:39, after you started editing. Your changes are not saved "
                       + "yet, and nothing was overwritten.")
        XCTAssertEqual(model.diff, [
            .folded(3),
            .same("- Approves energy switches over €150"),
            .theirs("- Family dinner is at 18:30"),
            .theirs("- Dentist: Tue 9:10"),
            .yours("- Family dinner is at 19:00"),
        ])
        XCTAssertEqual(drafts.draft(for: .aboutYou)?.text, model.text, "your text is still kept")

        // Reload: the hub's version, and the draft is gone.
        model.reloadTheirs()
        XCTAssertNil(model.conflict)
        XCTAssertEqual(model.text, theirs)
        XCTAssertNil(drafts.draft(for: .aboutYou))
        XCTAssertFalse(model.canSave)
        XCTAssertEqual(hub.ifMatches.count, 1, "reloading sends nothing")
    }

    func testSaveMineOverItIsAKnowingOverwriteOfTheVersionShown() async {
        let hub = FakeHub()
        let drafts = DraftStore(defaults: .scratch())
        let model = editor(hub, drafts: drafts)
        await model.load()
        let mine = Fx.aboutYou + "\n- Mine"
        model.text = mine
        hub.write("MEMORY.md", "theirs\n")
        await model.save()
        XCTAssertNotNil(model.conflict)

        await model.saveMineOverTheirs()
        XCTAssertEqual(hub.ifMatches, [FakeHub.blob(Fx.aboutYou), FakeHub.blob("theirs\n")],
                       "the overwrite names the version that was shown, never none")
        XCTAssertEqual(hub.file("MEMORY.md")?.content, mine)
        XCTAssertNil(model.conflict)
        XCTAssertNil(drafts.draft(for: .aboutYou))
    }

    func testAThirdChangeDuringTheChoiceIsCaughtToo() async {
        let hub = FakeHub()
        let model = editor(hub, drafts: DraftStore(defaults: .scratch()))
        await model.load()
        model.text = "mine\n"
        hub.write("MEMORY.md", "second\n")
        await model.save()
        hub.write("MEMORY.md", "third\n")
        await model.saveMineOverTheirs()
        XCTAssertEqual(hub.file("MEMORY.md")?.content, "third\n")
        XCTAssertEqual(model.conflict?.content, "third\n")
    }

    // MARK: Drafts

    func testUnsavedEditsSurviveLeavingTheScreen() async {
        let hub = FakeHub()
        let defaults = UserDefaults.scratch()
        let first = editor(hub, drafts: DraftStore(defaults: defaults))
        await first.load()
        first.text = Fx.aboutYou + "\n- Half-typed"
        // The screen goes away; a new one opens the same note.
        let second = editor(hub, drafts: DraftStore(defaults: defaults))
        await second.load()
        XCTAssertEqual(second.text, Fx.aboutYou + "\n- Half-typed")
        XCTAssertEqual(second.mode, .edit)
        XCTAssertEqual(second.notice, "Your unsaved changes from today 18:35 are back.")
        XCTAssertNil(second.conflict)
        XCTAssertTrue(second.canSave)
        await second.save()
        XCTAssertEqual(hub.ifMatches, [FakeHub.blob(Fx.aboutYou)])
    }

    func testAFailedSaveKeepsTheTextAndSaysWhy() async {
        let hub = FakeHub()
        let defaults = UserDefaults.scratch()
        let model = editor(hub, drafts: DraftStore(defaults: defaults))
        await model.load()
        model.text = "offline edit\n"
        hub.offline = true
        await model.save()
        XCTAssertEqual(model.problem, "Couldn't reach the hub. Check the connection and try again.")
        XCTAssertEqual(model.text, "offline edit\n")
        XCTAssertTrue(model.canSave, "it can be tried again")

        // Still offline when the note is opened again: the draft is shown, and a later save still checks the version.
        let reopened = editor(hub, drafts: DraftStore(defaults: defaults))
        await reopened.load()
        XCTAssertEqual(reopened.state, .ready)
        XCTAssertEqual(reopened.text, "offline edit\n")
        XCTAssertEqual(reopened.problem, "Couldn't reach the hub. Check the connection and try again.")
        hub.offline = false
        await reopened.save()
        XCTAssertEqual(hub.ifMatches.last, FakeHub.blob(Fx.aboutYou))
        XCTAssertEqual(hub.file("MEMORY.md")?.content, "offline edit\n")

        for (status, body, words) in [
            (503, Fx.error("memory_unavailable", "git failed"),
             "Marcel's notebook can't be reached on the hub right now. Nothing was changed. (git failed)"),
            (500, Fx.error("internal_error", "boom"), "The hub had a problem. Try again in a minute."),
            (401, Fx.error("unauthorized", "no"), "The hub no longer accepts this phone. Pair it again."),
            (400, Fx.error("memory_file_too_big", "too big"),
             "This note is over 1 MB, too big to open or save on the phone."),
        ] {
            let hub = FakeHub()
            hub["PUT /memory/files/MEMORY.md"] = (status, body)
            let defaults = UserDefaults.scratch("failed-\(status)")
            let model = editor(hub, drafts: DraftStore(defaults: defaults))
            await model.load()
            model.text = "kept\n"
            await model.save()
            XCTAssertEqual(model.problem, words, "HTTP \(status)")
            XCTAssertEqual(DraftStore(defaults: defaults).draft(for: .aboutYou)?.text, "kept\n", "HTTP \(status)")
        }
    }

    func testADraftStartedOnAnOlderVersionShowsTheConflictOnOpenAndKeepsShowingIt() async {
        let hub = FakeHub()
        let defaults = UserDefaults.scratch()
        let first = editor(hub, drafts: DraftStore(defaults: defaults))
        await first.load()
        first.text = "my draft\n"
        hub.write("MEMORY.md", "Marcel's newer notes\n")

        let second = editor(hub, drafts: DraftStore(defaults: defaults))
        await second.load()
        XCTAssertEqual(second.text, "my draft\n")
        XCTAssertEqual(second.conflict?.content, "Marcel's newer notes\n")
        XCTAssertFalse(second.canSave)
        second.text += "more\n"  // typing on while the choice is open

        // Leave and come back: the conflict is still there (the draft keeps its own base).
        let third = editor(hub, drafts: DraftStore(defaults: defaults))
        await third.load()
        XCTAssertNotNil(third.conflict)
        XCTAssertEqual(third.text, "my draft\nmore\n")
        XCTAssertTrue(hub.ifMatches.isEmpty, "nothing was sent")
    }

    func testANoteRemovedElsewhereIsCreatedAgainOnTheNextSave() async {
        let hub = FakeHub()
        let model = editor(hub, drafts: DraftStore(defaults: .scratch()))
        await model.load()
        model.text = "still wanted\n"
        hub.remove("MEMORY.md")
        await model.save()
        XCTAssertEqual(model.problem,
                       "This note was removed since you opened it. Your text is still here: save again to create it.")
        XCTAssertNil(model.base)
        await model.save()
        XCTAssertEqual(hub.ifMatches, [FakeHub.blob(Fx.aboutYou), nil])
        XCTAssertEqual(hub.file("MEMORY.md")?.content, "still wanted\n")
    }

    func testThrowingAwayChangesBringsBackTheSavedText() async {
        let hub = FakeHub()
        let drafts = DraftStore(defaults: .scratch())
        let model = editor(hub, drafts: drafts)
        await model.load()
        model.text = "oops"
        model.discardChanges()
        XCTAssertEqual(model.text, Fx.aboutYou)
        XCTAssertNil(drafts.draft(for: .aboutYou))
        XCTAssertFalse(model.canSave)
    }

    func testAFileThatCannotBeReadSaysWhy() async {
        for (status, body, words) in [
            (404, #"{"detail":"Not Found"}"#, "Not available yet: the hub can't show Marcel's notebook yet."),
            (401, Fx.error("unauthorized", "no"), "The hub no longer accepts this phone. Pair it again."),
            (502, "", "The hub had a problem. Try again in a minute."),
        ] {
            let hub = FakeHub()
            hub["GET /memory/files/MEMORY.md"] = (status, body)
            let model = editor(hub, drafts: DraftStore(defaults: .scratch("read-\(status)")))
            await model.load()
            XCTAssertEqual(model.state, .failed(words), "HTTP \(status)")
        }
    }
}
