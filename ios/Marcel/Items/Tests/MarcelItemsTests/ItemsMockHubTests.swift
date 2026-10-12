import Foundation
import MarcelKit
import SwiftData
import XCTest

@testable import MarcelItems

/// The tab and the viewer against the real mock hub, through the hub's REST client and the cache the screens use.
/// Skipped unless `MARCEL_MOCK_HUB` holds the hub's server URL: start it with `MOCK_HUB_PORT=7488 make mock-hub`,
/// then `MOCK_HUB=http://127.0.0.1:7488/api make -C ios items-integration`. Not part of the gate.
@MainActor
final class ItemsMockHubTests: XCTestCase {
    final class FixedToken: TokenStore, @unchecked Sendable {
        func token() throws -> String? { "items-integration-test" }
        func save(_ token: String) throws {}
        func delete() throws {}
    }

    func testTheTabEveryKindTheMockHasVersionsAndAQuestion() async throws {
        guard let hub = ProcessInfo.processInfo.environment["MARCEL_MOCK_HUB"], let serverURL = URL(string: hub) else {
            throw XCTSkip("Set MARCEL_MOCK_HUB (make -C ios items-integration) to run against the mock hub.")
        }
        let cache = MarcelCache(container: try MarcelCache.makeContainer(inMemory: true))
        let service = LiveItemsService(client: HubClient.make(serverURL: serverURL, tokenStore: FixedToken()))

        // The tab: every item, by project.
        let tab = ItemsModel(service: service, cache: cache)
        await tab.refresh()
        XCTAssertNil(tab.problem)
        let input = try ItemsInput.read(from: cache.container.mainContext)
        let library = ItemsLibrary(artifacts: input.artifacts, projects: input.projects, now: Date())
        XCTAssertFalse(library.isEmpty)

        // Each item the mock has opens as its kind.
        let viewer = ItemViewerModel(service: service, cache: cache)
        for artifact in input.artifacts {
            await viewer.load(artifact)
            switch (artifact.kind, viewer.body(artifact)) {
            case (.doc, .markdown), (.board, .dashboard), (.dashboard, .dashboard), (.pr, .pr), (.link, .link),
                 (.branch, .link), (.file, .file):
                break
            case (let kind, let body):
                XCTFail("\(artifact.id) (\(kind)) became \(body)")
            }
        }

        // The document's history, and version 1 read-only.
        let doc = try XCTUnwrap(input.artifacts.first { $0.kind == .doc && $0.currentVersion > 1 })
        await viewer.loadVersions(doc)
        XCTAssertEqual(viewer.history(doc).map(\.version), Array((1...doc.currentVersion).reversed()))
        await viewer.load(doc, version: 1)
        guard case .markdown(let parts) = viewer.body(doc, version: 1) else { return XCTFail("version 1 not drawn") }
        XCTAssertTrue(ItemMarkdown.plainText(parts).contains("Version 1."))

        // Ask about it: stored in the main conversation with the project's tag.
        viewer.draft = "Which offer is cheapest?"
        await viewer.ask(about: doc)
        XCTAssertNil(viewer.problem)
        let messages = try cache.container.mainContext.fetch(FetchDescriptor<CachedMessage>())
        let sent = try messages.map { try ItemsJSON.decoder().decode(Message.self, from: $0.payload) }
        XCTAssertEqual(sent.first?.projectId, doc.projectId)
        XCTAssertTrue(sent.first?.text?.hasPrefix("Which offer is cheapest?") == true)
    }
}
