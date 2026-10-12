import Foundation
import MarcelKit
import SwiftData
import XCTest

@testable import MarcelItems

/// The story's UI test, run one layer down. XCUITest needs the iOS 26.5 platform this Mac's Xcode lacks (see
/// ios/README.md), so these drive what the viewer is built from: a real in-memory cache fed with the live stream's
/// event frames, read back the way the viewer's `@Query` reads it, and the viewer's model with a fake hub behind it.
@MainActor
final class ViewerLiveTests: XCTestCase {
    /// What the open viewer shows: its version line and its body, worked out the way `ArtifactViewer` does.
    private func screen(_ fed: FedCache, _ model: ItemViewerModel, _ id: String, showing version: Int? = nil)
        throws -> (line: VersionLine, body: ItemBody, artifact: Artifact)
    {
        let artifact = try XCTUnwrap(try fed.artifact(id))
        return (VersionLine(artifact, showing: version, versions: model.history(artifact), now: Fx.now,
                            timeZone: Fx.utc),
                model.body(artifact, version: version), artifact)
    }

    // UI test: an `artifact.updated` event refreshes an open viewer and bumps the version.
    func testAnArtifactUpdatedEventRefreshesAnOpenViewerAndBumpsTheVersion() async throws {
        let fed = try FedCache.seeded()
        let hub = FakeItemsService()
        let model = ItemViewerModel(service: hub, cache: fed.cache)

        // Open the mock hub's document at version 3.
        var open = try screen(fed, model, Fx.docId)
        await model.load(open.artifact)
        await model.loadVersions(open.artifact)
        open = try screen(fed, model, Fx.docId)
        XCTAssertEqual(open.line.text, "Updated 2 min ago by Energy contracts · v3")
        XCTAssertEqual(open.line.spoken, "Updated 2 minutes ago by Energy contracts, version 3")
        guard case .markdown(let v3) = open.body else { return XCTFail("not drawn: \(open.body)") }
        XCTAssertEqual(v3.last?.block, .paragraph("Version 3. The mock hub has no real content for this document."))

        // Version 4 arrives on the stream.
        hub.contents["\(Fx.docId)@4"] = Data("# Energy offers compared\n\nVersion 4. Switch requested.\n".utf8)
        var updated = Fx.mockArtifact(Fx.docId)
        updated["current_version"] = 4
        updated["updated_by"] = "Switch to Luminus"
        updated["updated_at"] = "2026-10-12T08:15:30Z"
        try fed.apply("artifact.updated", ["artifact": updated, "version": Fx.version(
            Fx.docId, 4, summary: "Switch requested.", author: "Switch to Luminus", at: "2026-10-12T08:15:30Z")])

        // The viewer reads the new version from the cache at once; its body loads the new bytes.
        open = try screen(fed, model, Fx.docId)
        XCTAssertEqual(open.line.text, "Updated just now by Switch to Luminus · v4")
        XCTAssertEqual(open.body, .loading, "the new version's bytes are fetched, not the old ones shown as new")
        await model.load(open.artifact)
        await model.loadVersions(open.artifact)
        open = try screen(fed, model, Fx.docId)
        guard case .markdown(let v4) = open.body else { return XCTFail("not drawn: \(open.body)") }
        XCTAssertEqual(v4.last?.block, .paragraph("Version 4. Switch requested."))
        XCTAssertEqual(hub.asked.filter { $0.hasPrefix("content") }, ["content \(Fx.docId) v3", "content \(Fx.docId) v4"])
        XCTAssertEqual(hub.asked.filter { $0.hasPrefix("versions") }, ["versions \(Fx.docId)", "versions \(Fx.docId)"],
                       "the history is fetched again for the new version")
    }

    func testABoardThatGetsANewVersionIsRedrawn() async throws {
        let fed = try FedCache.seeded()
        let hub = FakeItemsService()
        let model = ItemViewerModel(service: hub, cache: fed.cache)
        await model.load(try XCTUnwrap(try fed.artifact(Fx.boardId)))
        guard case .dashboard(let v7) = try screen(fed, model, Fx.boardId).body,
              case .section(let waiting) = v7.widgets[0] else { return XCTFail("board v7 not drawn") }
        XCTAssertTrue(waiting.widgets.isEmpty)

        hub.contents["\(Fx.boardId)@8"] = Data("""
            {"version": 1, "title": "Energy contracts", "widgets": [
              {"type": "section", "title": "Waiting on you", "widgets": [
                {"type": "list", "items": [{"text": "Send the switch request to Luminus"}]}]},
              {"type": "note", "text": "Luminus is cheapest by €412 a year.", "author": "Marcel"}]}
            """.utf8)
        var board = Fx.mockArtifact(Fx.boardId)
        board["current_version"] = 8
        try fed.apply("artifact.updated", ["artifact": board, "version": Fx.version(Fx.boardId, 8)])
        await model.load(try XCTUnwrap(try fed.artifact(Fx.boardId)))

        let open = try screen(fed, model, Fx.boardId)
        XCTAssertTrue(open.line.text.hasSuffix("· v8"))
        guard case .dashboard(let v8) = open.body, case .section(let now) = v8.widgets[0] else {
            return XCTFail("board v8 not drawn")
        }
        XCTAssertEqual(now.widgets.count, 1)
        XCTAssertEqual(v8.widgets.last, .note(.init(text: "Luminus is cheapest by €412 a year.", author: "Marcel")))
    }

    // MARK: Versions

    func testTheHistoryListsEveryVersionAndAnOldOneOpensReadOnly() async throws {
        let fed = try FedCache.seeded()
        let hub = FakeItemsService()
        let model = ItemViewerModel(service: hub, cache: fed.cache)
        let doc = try XCTUnwrap(try fed.artifact(Fx.docId))
        await model.loadVersions(doc)

        let rows = model.history(doc).map { VersionRow($0, current: doc.currentVersion, now: Fx.now, timeZone: Fx.utc) }
        XCTAssertEqual(rows.map(\.version), [3, 2, 1])
        XCTAssertEqual(rows[0].summary, "Added the Engie offer; Luminus is still the cheapest.")
        XCTAssertEqual(rows[0].detail, "Energy contracts · 2 min ago")
        XCTAssertTrue(rows[0].isCurrent)
        XCTAssertEqual(rows[2].summary, "The first version.")
        XCTAssertEqual(rows[0].spoken, "Version 3, the current one. Added the Engie offer; Luminus is still the "
                       + "cheapest. By Energy contracts, 2 minutes ago")

        // Open version 2: asked for by number, and marked read-only.
        await model.load(doc, version: 2)
        let old = try screen(fed, model, Fx.docId, showing: 2)
        XCTAssertTrue(old.line.isOld)
        XCTAssertEqual(old.line.text, "v2 of 3 · read only · on 9 Oct")
        XCTAssertEqual(old.line.spoken, "Version 2 of 3, read only, made on 9 October")
        guard case .markdown(let parts) = old.body else { return XCTFail("v2 not drawn") }
        XCTAssertEqual(parts.last?.block, .paragraph("Version 2. The mock hub has no real content for this document."))
        XCTAssertEqual(hub.asked.last, "content \(Fx.docId) v2")
    }

    func testAnOldVersionOfAnotherTypeIsDrawnAsThatType() async throws {
        let fed = try FedCache.seeded()
        let hub = FakeItemsService()
        // Version 1 of the "Offer table" was Markdown; version 2 is HTML.
        var html = Fx.html
        html["current_version"] = 2
        try fed.apply("artifact.created", html)
        hub.versionsById[Fx.htmlId] = [
            ["artifact_id": Fx.htmlId, "version": 2, "content_type": "text/html", "created_at": "2026-10-12T06:00:00Z"],
            ["artifact_id": Fx.htmlId, "version": 1, "content_type": "text/markdown", "created_at": "2026-10-11T06:00:00Z"],
        ]
        hub.contents["\(Fx.htmlId)@1"] = Data("# Offers\n".utf8)
        let model = ItemViewerModel(service: hub, cache: fed.cache)
        let doc = try XCTUnwrap(try fed.artifact(Fx.htmlId))
        await model.loadVersions(doc)
        await model.load(doc, version: 1)
        XCTAssertEqual(model.body(doc, version: 1), .markdown([.init(block: .heading(level: 1, text: "Offers"), links: [])]))
    }

    func testPullRequestsAndLinksFetchNoBytes() async throws {
        let fed = try FedCache.seeded()
        let hub = FakeItemsService()
        let model = ItemViewerModel(service: hub, cache: fed.cache)
        await model.load(try XCTUnwrap(try fed.artifact(Fx.prId)))
        await model.load(try XCTUnwrap(try fed.artifact(Fx.linkId)))
        XCTAssertEqual(hub.asked, [])
    }

    func testContentThatCannotBeLoadedSaysSoAndIsAskedForAgain() async throws {
        let fed = try FedCache.seeded()
        let hub = FakeItemsService()
        hub.contentError = .offline
        let model = ItemViewerModel(service: hub, cache: fed.cache)
        let doc = try XCTUnwrap(try fed.artifact(Fx.docId))
        await model.load(doc)
        XCTAssertEqual(model.body(doc), .failed("Couldn't load this item. It shows once the hub is back."))
        hub.contentError = nil
        await model.load(doc)
        guard case .markdown = model.body(doc) else { return XCTFail("not loaded on the second try") }

        hub.contentError = .gone
        await model.load(doc, version: 1)
        XCTAssertEqual(model.body(doc, version: 1), .failed("The hub no longer has this version."))
    }

    func testAnItemTheCacheDoesNotHaveIsFetched() async throws {
        let fed = try FedCache()
        let hub = FakeItemsService()
        let model = ItemViewerModel(service: hub, cache: fed.cache)
        await model.fetch(id: Fx.fileId)
        XCTAssertEqual(try fed.artifact(Fx.fileId)?.title, "Bills 2026")
        await model.fetch(id: "art_nope")
        XCTAssertEqual(model.problem, "The hub no longer has this item.")
    }

    // MARK: Ask about this

    func testAskAboutThisPostsToTheMainConversationWithTheItemAndItsProject() async throws {
        let fed = try FedCache.seeded()
        let hub = FakeItemsService()
        let model = ItemViewerModel(service: hub, cache: fed.cache)
        let doc = try XCTUnwrap(try fed.artifact(Fx.docId))
        model.asking = true
        model.draft = "  Is Engie cheaper with solar?  "

        await model.ask(about: doc)

        XCTAssertNil(model.problem)
        XCTAssertEqual(hub.sent.first?.conversationId, Fx.conversation, "to the main conversation")
        XCTAssertEqual(hub.sent.first?.projectId, Fx.energy, "tagged with the item's project")
        XCTAssertEqual(hub.sent.first?.text, "Is Engie cheaper with solar?\n\n(About the item “Energy offers "
                       + "compared”, version 3, art_01JA0B1.)")
        XCTAssertEqual(model.draft, "")
        XCTAssertFalse(model.asking)
        XCTAssertEqual(model.sent, "Sent to Marcel. His answer comes in Chat.")

        // The main chat shows it at once (it is in the cache the Chat tab reads).
        let messages = try fed.cache.container.mainContext.fetch(FetchDescriptor<CachedMessage>())
        XCTAssertEqual(messages.map(\.conversationId), [Fx.conversation])
    }

    func testAQuestionAboutAnOldVersionSaysWhichVersion() {
        let doc = Fx.artifact(Fx.mockArtifact(Fx.docId))
        XCTAssertTrue(ItemViewerModel.question("Why?", about: doc, version: 2).contains("version 2,"))
    }

    func testAQuestionThatDidNotGetThroughStaysInTheBox() async throws {
        let fed = try FedCache.seeded()
        let hub = FakeItemsService()
        hub.sendError = .offline
        let model = ItemViewerModel(service: hub, cache: fed.cache, conversationId: Fx.conversation)
        model.asking = true
        model.draft = "Is Engie cheaper?"
        await model.ask(about: try XCTUnwrap(try fed.artifact(Fx.docId)))
        XCTAssertEqual(model.problem, "Not sent. Couldn't reach the hub. Check the connection and try again.")
        XCTAssertEqual(model.draft, "Is Engie cheaper?", "one tap to try again")
        XCTAssertTrue(model.asking)
        XCTAssertNil(model.sent)
        XCTAssertFalse(hub.asked.contains("me"), "a known conversation is not asked for again")
        XCTAssertFalse(model.sending)
    }

    func testAnEmptyQuestionIsNotSentAndWithoutAHubItSaysSo() async throws {
        let fed = try FedCache.seeded()
        let hub = FakeItemsService()
        let model = ItemViewerModel(service: hub, cache: fed.cache)
        let doc = try XCTUnwrap(try fed.artifact(Fx.docId))
        model.draft = "   "
        await model.ask(about: doc)
        XCTAssertEqual(hub.asked, [])

        let unpaired = ItemViewerModel(service: nil, cache: fed.cache)
        unpaired.draft = "Hello"
        await unpaired.ask(about: doc)
        XCTAssertEqual(unpaired.problem, "This phone isn't paired with a hub yet.")
        await unpaired.load(doc)
        XCTAssertEqual(unpaired.body(doc), .failed("This phone isn't paired with a hub yet."))
    }

    // MARK: The tab

    func testTheTabFetchesEveryItemIntoTheCache() async throws {
        let fed = try FedCache()
        let hub = FakeItemsService()
        let model = ItemsModel(service: hub, cache: fed.cache)
        await model.refresh()
        XCTAssertNil(model.problem)
        XCTAssertEqual(try fed.input.artifacts.count, Fx.allArtifacts.count)
    }
}
