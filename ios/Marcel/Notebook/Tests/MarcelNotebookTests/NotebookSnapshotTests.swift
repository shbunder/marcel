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
                NotebookContent(model: model, rollovers: model.rollovers, open: { _ in })
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
                                  rollovers: log, projectNames: { Fx.projectNames }, text: Fx.text)
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
        Snapshot.assert(NotebookStill(model: await notebook()), named: "Notebook", padding: 0)
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
