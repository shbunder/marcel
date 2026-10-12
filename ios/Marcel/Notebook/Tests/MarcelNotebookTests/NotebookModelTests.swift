import Foundation
import MarcelItems
import MarcelKit
import SwiftUI
import UIKit
import XCTest

@testable import MarcelNotebook

/// The notebook's list against a fake hub: the sections, untrusted paths, the hub's answers in plain words.
@MainActor
final class NotebookModelTests: XCTestCase {
    private func loaded(_ hub: FakeHub = FakeHub(), drafts: DraftStore = DraftStore(defaults: .scratch(#function)),
                        names: [String: String] = Fx.projectNames) async -> NotebookModel {
        let model = NotebookModel(service: hub.service, drafts: drafts,
                                  rollovers: RolloverLog(defaults: .scratch("rollovers-\(#function)")),
                                  projectNames: { names }, text: Fx.text)
        await model.load()
        return model
    }

    func testSections() async {
        let model = await loaded()
        XCTAssertEqual(model.state, .loaded)
        XCTAssertEqual(model.aboutYouPreview, [
            "· Prefers mornings for appointments", "· Approves energy switches over €150",
            "· Family dinner is at 18:30",
        ])
        XCTAssertEqual(model.detail(model.aboutYou), "Updated yesterday 18:40")
        XCTAssertEqual(model.projects.map(\.title), ["Home energy", "Garden"], "projects with notes first, then the rest")
        XCTAssertEqual(model.detail(model.projects[1]), "No notes yet")
        XCTAssertEqual(model.projects[1].path.raw, "memory/projects/garden.md")
        XCTAssertEqual(model.dated.map(\.title), ["Sun, 4 Oct 2026", "Sat, 3 Oct 2026"], "newest first")
        XCTAssertTrue(model.others.isEmpty)
        XCTAssertNil(model.problem)
    }

    func testAProjectTheAppDoesNotKnowShowsItsSlug() async {
        let model = await loaded(names: [:])
        XCTAssertEqual(model.projects.map(\.title), ["energy"])
    }

    func testRowsWithADraftSaySo() async {
        let drafts = DraftStore(defaults: .scratch())
        drafts.keep(Draft(text: "x", base: nil, at: Fx.now), for: MemoryPath("memory/2026-10-04.md")!)
        let model = await loaded(drafts: drafts)
        XCTAssertEqual(model.detail(model.dated[0]), "Updated today 18:30 · unsaved changes")
        drafts.discard(MemoryPath("memory/2026-10-04.md")!)
        model.refreshDrafts()
        XCTAssertEqual(model.detail(model.dated[0]), "Updated today 18:30")
    }

    func testUntrustedPathsFromTheHubAreNeverOpened() async {
        let hub = FakeHub()
        hub.extraTreePaths = ["../etc/passwd.md", ".git/config.md", "/abs.md", "memory/.hidden.md", "a//b.md",
                              "notes.txt", "memory/%2e%2e/x.md", "memory/ünïcode.md", "memory/notes/todo.md"]
        let model = await loaded(hub)
        XCTAssertEqual(model.refused, 8)
        XCTAssertEqual(model.others.map(\.path.raw), ["memory/notes/todo.md"])
        XCTAssertFalse(hub.requests.contains { $0.contains("..") || $0.contains("%2e") }, "\(hub.requests)")
    }

    func testAHubWithoutTheNotebookSaysNotAvailableYet() async {
        for status in [404, 405, 501] {
            let hub = FakeHub()
            hub["GET /memory"] = (status, status == 404 ? #"{"detail":"Not Found"}"# : "")
            let model = await loaded(hub)
            XCTAssertEqual(model.state, .notAvailableYet, "HTTP \(status)")
        }
    }

    func testTheHubsAnswersInPlainWords() async {
        for (status, body, words) in [
            (401, Fx.error("unauthorized", "no"), "The hub no longer accepts this phone. Pair it again."),
            (503, Fx.error("memory_unavailable", "git is missing"),
             "Marcel's notebook can't be reached on the hub right now. Nothing was changed. (git is missing)"),
            (500, "", "The hub had a problem. Try again in a minute."),
        ] {
            let hub = FakeHub()
            hub["GET /memory"] = (status, body)
            let model = await loaded(hub)
            XCTAssertEqual(model.state, .failed(words), "HTTP \(status)")
        }
        let hub = FakeHub()
        hub.offline = true
        let model = await loaded(hub)
        XCTAssertEqual(model.state, .failed("Couldn't reach the hub. Check the connection and try again."))
    }
}

/// Paths from the hub are untrusted; on the wire a path is one percent-encoded segment.
@MainActor
final class MemoryPathTests: XCTestCase {
    func testOnlyTheContractsPathsAreAccepted() {
        for good in ["MEMORY.md", "memory/2026-10-04.md", "memory/projects/home-energy.md", "a_b.c-d.md"] {
            XCTAssertNotNil(MemoryPath(good), good)
        }
        for bad in ["", "..md", "../x.md", "memory/../MEMORY.md", "/MEMORY.md", ".git/HEAD.md", "memory/.x.md",
                    "a//b.md", "memory/", "MEMORY", "MEMORY.md.txt", "memory/a b.md", "memory/%2F.md", "x?.md",
                    "memory\\x.md", "memory/é.md", String(repeating: "a", count: 600) + ".md"] {
            XCTAssertNil(MemoryPath(bad), bad)
        }
    }

    func testSections() {
        XCTAssertEqual(MemoryPath("MEMORY.md")!.section, .aboutYou)
        XCTAssertEqual(MemoryPath("memory/projects/energy.md")!.section, .project(slug: "energy"))
        XCTAssertEqual(MemoryPath("memory/2026-10-04.md")!.section, .dated(MemoryPath.day("2026-10-04")!))
        XCTAssertEqual(MemoryPath("memory/2026-02-30.md")!.section, .other, "not a real date")
        XCTAssertEqual(MemoryPath("memory/notes/todo.md")!.section, .other)
        XCTAssertEqual(MemoryPath("memory/projects/a/b.md")!.section, .other)
    }

    func testTheWholePathTravelsAsOneSegment() async throws {
        let hub = FakeHub()
        let path = MemoryPath("memory/projects/energy.md")!
        XCTAssertEqual(path.encoded, "memory%2Fprojects%2Fenergy.md")
        _ = try await hub.service.file(path)
        XCTAssertEqual(hub.requests, ["GET /memory/files/memory%2Fprojects%2Fenergy.md"])
    }

    func testAnAnswerAboutAnotherFileIsRefused() async {
        let hub = FakeHub()
        hub["GET /memory/files/MEMORY.md"] = (200, Fx.json([
            "path": "memory/other.md", "content": "x", "etag": "abc", "updated_at": Fx.iso(Fx.now),
        ]))
        do {
            _ = try await hub.service.file(.aboutYou)
            XCTFail("expected a refusal")
        } catch {
            XCTAssertEqual(error as? NotebookError, .unexpected("a different note came back"))
        }
    }
}

/// The preview is the item viewer's Markdown (MarcelItems): it never opens a link by itself, only http(s) links get a
/// button, and HTML is shown as words, never run.
@MainActor
final class PreviewSafetyTests: XCTestCase {
    func testHTMLIsWordsAndOnlyWebLinksGetAButton() {
        let parts = ItemMarkdown.parse("""
            <script>alert(1)</script>

            [steal](javascript:alert(1)) [file](file:///etc/passwd) [call](tel:123) [site](https://example.com)

            <img src=x onerror=alert(1)>
            """)
        XCTAssertEqual(parts.flatMap(\.links).map(\.url.absoluteString), ["https://example.com"])
        let words = ItemMarkdown.plainText(parts)
        XCTAssertTrue(words.contains("steal"), words)
    }

    func testDrawingThePreviewOpensNothing() {
        var opened: [URL] = []
        let view = MarkdownView(parts: ItemMarkdown.parse("[site](https://example.com) and <https://example.org>"))
            .environment(\.openURL, OpenURLAction { url in
                opened.append(url)
                return .handled
            })
            .frame(width: 390)
        let renderer = ImageRenderer(content: view)
        XCTAssertNotNil(renderer.uiImage)
        XCTAssertTrue(opened.isEmpty, "a link opens only when its button is tapped")
    }
}

/// "What Marcel carries": `brain.rollover` frames from the live stream, kept on the phone.
@MainActor
final class RolloverLogTests: XCTestCase {
    func testRolloversFromTheStreamAreKeptNewestFirst() throws {
        let defaults = UserDefaults.scratch()
        let cache = MarcelCache(container: try MarcelCache.makeContainer(inMemory: true))
        let log = RolloverLog(defaults: defaults)
        log.attach(to: cache)
        try cache.apply(Fx.rollover(id: 10, at: Fx.at(-86_400 * 2), reason: "context_full", kept: "Your notes."))
        try cache.apply(Fx.rollover(id: 12, at: Fx.at(-3600 * 19 - 40 * 60)))
        try cache.apply(Fx.frame("agent.state", ["agent_id": "agt_marcel", "state": ["kind": "idle"]], id: 13,
                                 at: Fx.now))
        try cache.apply(Fx.rollover(id: 12, at: Fx.at(-3600 * 19 - 40 * 60)))  // replayed after a reconnect
        XCTAssertEqual(log.rollovers.map(\.id), [12, 10])
        XCTAssertEqual(Fx.text.rollover(log.rollovers[0]),
                       "Context rolled over yesterday 23:00 (the daily fresh start). Kept: open threads, decisions, "
                       + "today's plan. Your chat history is unchanged.")
        XCTAssertEqual(Fx.text.rollover(log.rollovers[1]),
                       "Context rolled over Fri 2 Oct, 18:40 (its working memory was full). Kept: Your notes. "
                       + "Your chat history is unchanged.")

        // Kept on the phone across launches; detached, it hears nothing more.
        XCTAssertEqual(RolloverLog(defaults: defaults).rollovers, log.rollovers)
        log.detach()
        try cache.apply(Fx.rollover(id: 20, at: Fx.now))
        XCTAssertEqual(log.rollovers.count, 2)
    }

    func testOnlyTheLastTenAreKept() {
        let log = RolloverLog(defaults: .scratch())
        for id in 1...15 { log.receive(Fx.rollover(id: id, at: Fx.at(TimeInterval(id * 60)))) }
        XCTAssertEqual(log.rollovers.map(\.id), Array((6...15).reversed()))
        log.clear()
        XCTAssertTrue(log.rollovers.isEmpty)
    }

    func testReasonsInPlainWords() {
        XCTAssertEqual(Fx.text.reason("crashed"), "the old session had stopped")
        XCTAssertEqual(Fx.text.reason("manual"), "you asked for it")
        XCTAssertEqual(Fx.text.reason("something_new"), "another reason")
    }
}

final class LineDiffTests: XCTestCase {
    func testLinesInOrderWithTheHubsFirst() {
        XCTAssertEqual(LineDiff.lines(theirs: "a\nb\nc\n", yours: "a\nB\nc\nd\n"),
                       [.same("a"), .theirs("b"), .yours("B"), .same("c"), .yours("d")])
        XCTAssertEqual(LineDiff.lines(theirs: "", yours: "new\n"), [.yours("new")])
        XCTAssertEqual(LineDiff.folded(theirs: "same\n", yours: "same\n"), [])
    }

    func testLongUnchangedRunsAreFolded() {
        let theirs = (1...20).map(String.init).joined(separator: "\n")
        let yours = theirs.replacingOccurrences(of: "\n10\n", with: "\nten\n")
        XCTAssertEqual(LineDiff.folded(theirs: theirs, yours: yours),
                       [.folded(8), .same("9"), .theirs("10"), .yours("ten"), .same("11"), .folded(9)])
        XCTAssertEqual(LineDiff.Line.folded(1).spoken, "1 line the same")
        XCTAssertEqual(LineDiff.Line.theirs("").spoken, "Only in the hub's version: empty line")
        XCTAssertEqual(LineDiff.Line.yours("x").spoken, "Only in yours: x")
    }
}
