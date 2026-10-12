import Foundation
import PixelKit
import SwiftUI
import XCTest

@testable import MarcelNotebook

/// The notebook and a note, drawn as the live screens draw them but without the scroll view (which `ImageRenderer`
/// leaves blank) and with the editor's text as plain text, compared pixel for pixel with `__Snapshots__/*.png`.
@MainActor
final class NotebookSnapshotTests: XCTestCase {
    static let both: [Snapshot.Variant] = [.light, .dark]

    struct NotebookStill: View {
        var model: NotebookModel

        var body: some View {
            VStack(spacing: 0) {
                NotebookHeader(title: "Marcel's notebook", back: {})
                NotebookContent(model: model, open: { _ in })
                    .padding(PixelMetrics.padding)
            }
            .environment(\.notebookStill, true)
        }
    }

    struct EditorStill: View {
        var model: NoteEditorModel

        var body: some View {
            VStack(spacing: 0) {
                NotebookHeader(title: model.title, line: model.line, back: {})
                NoteEditorContent(model: model, actions: NoteEditorActions())
                    .padding(PixelMetrics.padding)
            }
            .environment(\.notebookStill, true)
        }
    }

    private func notebook(_ hub: FakeHub = FakeHub(), rollovers: Bool = true) async -> NotebookModel {
        let log = RolloverLog(defaults: .scratch("snapshot-rollovers-\(rollovers)"))
        if rollovers { log.receive(Fx.rollover(id: 12, at: Fx.at(-3600 * 19 - 40 * 60))) }
        let model = NotebookModel(service: hub.service, drafts: DraftStore(defaults: .scratch("snapshot-list")),
                                  rollovers: log, projectNames: { Fx.projectNames }, text: Fx.text,
                                  clock: { Fx.now })
        await model.load()
        return model
    }

    private func editor(_ hub: FakeHub = FakeHub(), path: String, title: String) async -> NoteEditorModel {
        let model = NoteEditorModel(path: MemoryPath(path)!, title: title, service: hub.service,
                                    drafts: DraftStore(defaults: .scratch("snapshot-\(path)")), text: Fx.text,
                                    clock: { Fx.at(-300) })
        await model.load()
        return model
    }

    /// Wireframe 6B: about you, project notes (one without notes yet), dated notes, and a rollover. Also at the
    /// largest text size.
    func testNotebook() async {
        // S-12.12: the hub's list (one of them also seen on this phone, shown once) and "Fresh start".
        let hub = FakeHub()
        hub[Fx.rolloversRoute] = (200, Fx.json([
            Fx.hubRollover("rol_2", at: Fx.at(-3600 * 19 - 40 * 60)),
            Fx.hubRollover("rol_1", at: Fx.at(-86_400 * 3), reason: "context_full", kept: "Your notes."),
        ]))
        let model = await notebook(hub)
        XCTAssertEqual(model.carried.count, 2)
        Snapshot.assert(NotebookStill(model: model), named: "Notebook", padding: 0)
    }

    /// S-12.12: a fresh start asked for, on its way (the button is off until it shows).
    func testFreshStartOnItsWay() async {
        let hub = FakeHub()
        hub[Fx.rollOverRoute] = (202, "")
        let model = await notebook(hub)
        await model.startFresh()
        XCTAssertFalse(model.canStartFresh)
        Snapshot.assert(NotebookStill(model: model), named: "Notebook-fresh-start", variants: Self.both, padding: 0)
    }

    /// S-12.12: a hub that cannot start one yet (before S-06.7) says so in plain words.
    func testFreshStartNotAvailableYet() async {
        let model = await notebook()
        await model.startFresh()
        Snapshot.assert(NotebookStill(model: model), named: "Notebook-fresh-start-not-yet", variants: Self.both,
                        padding: 0)
    }

    /// A hub that has not built the memory routes, and no rollover seen yet.
    func testNotebookOnAHubThatIsNotFinished() async {
        let hub = FakeHub()
        hub["GET /memory"] = (404, #"{"detail":"Not Found"}"#)
        Snapshot.assert(NotebookStill(model: await notebook(hub, rollovers: false)), named: "Notebook-not-yet",
                        variants: Self.both, padding: 0)
    }

    /// A project's notes, previewed.
    func testPreview() async {
        let model = await editor(path: "memory/projects/energy.md", title: "Home energy")
        Snapshot.assert(EditorStill(model: model), named: "Note-preview", variants: Self.both, padding: 0)
    }

    /// Editing, with unsaved changes.
    func testEditing() async {
        let model = await editor(path: "memory/projects/energy.md", title: "Home energy")
        model.mode = .edit
        model.text += "\n- Installer B replied: €7,900"
        Snapshot.assert(EditorStill(model: model), named: "Note-editing", variants: Self.both, padding: 0)
    }

    /// "Changed elsewhere": Marcel wrote the note while it was being edited.
    func testChangedElsewhere() async {
        let hub = FakeHub()
        let model = await editor(hub, path: "MEMORY.md", title: "About you")
        model.mode = .edit
        model.text = Fx.aboutYou.replacingOccurrences(of: "18:30", with: "19:00")
        hub.write("MEMORY.md", Fx.aboutYou + "\n- Dentist: Tue 9:10")
        await model.save()
        XCTAssertNotNil(model.conflict)
        Snapshot.assert(EditorStill(model: model), named: "Note-conflict", variants: Self.both, padding: 0)
    }
}
