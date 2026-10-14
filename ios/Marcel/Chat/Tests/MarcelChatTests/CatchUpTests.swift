import Foundation
import HTTPTypes
import MarcelItems
import MarcelKit
import OpenAPIRuntime
import XCTest

@testable import MarcelChat

/// S-12.11: the chat's side of app API 0.5 and the lead's review follow-ups — "Ask about this" arriving with an item
/// attached, "+ New" on the map never losing what you typed, and the header following the agent's name.
@MainActor
final class CatchUpTests: XCTestCase {
    private let offers = ItemAttachment(artifactId: "art_offers", title: "Energy offers compared",
                                        projectId: "prj_energy")

    private func users(_ items: [FeedItem]) -> [UserLine] {
        items.compactMap { if case .user(let line) = $0 { line } else { nil } }
    }

    /// "Ask about this": the item rides on the next message as `artifact_id` (tagged with its project), shows under
    /// the bubble while it is on its way and once the hub's copy arrives, and is used once.
    func testAskAboutThisSendsTheItemAndShowsItInTheSentBubble() async throws {
        let fed = try FedCache()
        try fed.send("artifact.created", Fx.artifact("art_offers", title: "Energy offers compared"))
        let hub = FakeService()
        hub.holdSends = true
        let model = ChatModel(service: hub, conversationId: Fx.conversation)
        model.draft = "Half typed"
        model.attach(offers)
        XCTAssertEqual(model.draft, "Half typed", "attaching keeps what you typed")
        model.draft = "Is Engie cheaper with solar?"

        let sending = Task { await model.send() }
        while hub.sent.isEmpty { await Task.yield() }
        XCTAssertNil(model.attachment, "the item goes with this message only")
        XCTAssertEqual(users(ChatFeed.items(try fed.input(), local: model.local)).map(\.attachment),
                       ["Energy offers compared"], "on its way, with the item")
        hub.release()
        await sending.value
        XCTAssertEqual(hub.sent.first?.attachment, offers)

        let clientId = try XCTUnwrap(hub.sent.first?.clientId)
        try fed.send("message.created", Fx.message("msg_1", at: 1, role: "user", text: "Is Engie cheaper with solar?",
                                                   extra: ["client_id": clientId, "artifact_id": "art_offers"]))
        model.prune(try fed.input())
        let line = try XCTUnwrap(users(ChatFeed.items(try fed.input(), local: model.local)).first)
        XCTAssertEqual(line.id, "msg_1", "the hub's copy")
        XCTAssertEqual(line.attachment, "Energy offers compared")
        XCTAssertEqual(line.accessibilityText,
                       "You said, with the item Energy offers compared: Is Engie cheaper with solar?")

        // An item the phone does not have yet still shows that something was attached.
        try fed.send("message.created", Fx.message("msg_2", at: 2, role: "user", text: "And this?",
                                                   extra: ["artifact_id": "art_unknown"]))
        XCTAssertEqual(users(ChatFeed.items(try fed.input(), local: model.local)).last?.attachment, "An item")

        // The next message goes without it.
        hub.holdSends = false
        model.draft = "Thanks"
        await model.send()
        XCTAssertNil(hub.sent.last?.attachment)
    }

    func testAMessageWithAnItemThatDidNotGetThroughKeepsItForTheRetry() async throws {
        let hub = FakeService(failing: true)
        let model = ChatModel(service: hub, conversationId: Fx.conversation)
        model.attach(offers)
        model.draft = "Why?"
        await model.send()
        let clientId = try XCTUnwrap(model.local.pending.first?.clientId)
        hub.failing = false
        await model.retry(clientId: clientId)
        XCTAssertEqual(hub.sent.map(\.attachment), [offers, offers])
    }

    /// Lead follow-up: "+ New" on the map puts its request in the composer without overwriting a draft.
    func testANewProjectRequestNeverOverwritesADraft() {
        let model = ChatModel(service: FakeService())
        model.propose("Start a new project: ")
        XCTAssertEqual(model.draft, "Start a new project: ", "an empty composer takes it")

        model.draft = "  Remind me about the dentist  "
        model.propose("Start a new project: ")
        XCTAssertEqual(model.draft, "Remind me about the dentist\n\nStart a new project: ", "your words stay first")
        model.propose("Start a new project: ")
        XCTAssertEqual(model.draft, "Remind me about the dentist\n\nStart a new project: ", "never added twice")
    }

    /// Lead follow-up: the header says the agent's name, from `GET /me`, and follows a rename in Settings.
    func testTheHeaderUsesTheAgentsName() async {
        let hub = FakeService()
        hub.agentName = "Marcella"
        let model = ChatModel(service: hub)
        XCTAssertEqual(ChatHeaderState(tasks: [], name: model.agentName).name, "Marcel", "before the hub says")
        await model.start()
        XCTAssertEqual(model.agentName, "Marcella")
        let header = ChatHeaderState(tasks: [], name: model.agentName)
        XCTAssertEqual(header.name, "Marcella")
        XCTAssertTrue(header.accessibilityText.hasPrefix("Marcella, free. "), header.accessibilityText)

        // Renamed in Settings: the app passes the new name on (`SettingsModel.nameChanged`).
        model.agentName = "Gigi"
        XCTAssertEqual(ChatHeaderState(tasks: [], name: model.agentName).name, "Gigi")
    }

    /// `pr.note` (app API 0.5) on the chat's PR card; nothing when the last refresh worked.
    func testAPullRequestCardSaysWhyItMayBeOutOfDate() {
        var json = Fx.artifact("art_pr", title: "Fix flaky login", kind: "pr")
        XCTAssertFalse(ArtifactPreview.summaryRows(Fx.decode(json)).map(\.label).contains { $0.hasPrefix("May be") })
        json["pr"] = ["number": 14, "state": "open", "note": "GitHub is having trouble."]
        XCTAssertEqual(ArtifactPreview.summaryRows(Fx.decode(json)).last?.label,
                       "May be out of date: GitHub is having trouble.")
    }

    /// Review fix: the agent can be renamed, so no user-facing words in Chat, Items or Map hard-code "Marcel". They
    /// read the `agentName` environment value (or a model's `agentName`). Allowed: the one fallback, and "Marcel" as
    /// the product ("This changes Marcel itself" on a pull request that changes the app's own code).
    func testNoUserFacingWordsHardCodeTheAgentsName() throws {
        let packages = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent()
            .deletingLastPathComponent().deletingLastPathComponent()
        let allowed: Set<String> = [#""Marcel""#, #""This changes Marcel itself""#,
                                    #""This changes Marcel itself. It is merged only when you approve it.""#]
        let literal = try NSRegularExpression(pattern: #""(?:[^"\\]|\\.)*""#)
        var found: [String] = []
        for package in ["Chat", "Items", "Map"] {
            let sources = packages.appendingPathComponent(package).appendingPathComponent("Sources")
            let files = try XCTUnwrap(FileManager.default.enumerator(at: sources, includingPropertiesForKeys: nil))
            for case let file as URL in files where file.pathExtension == "swift" {
                for line in try String(contentsOf: file, encoding: .utf8).split(separator: "\n") {
                    let code = String(line)
                    if code.trimmingCharacters(in: .whitespaces).hasPrefix("//") { continue }
                    let range = NSRange(code.startIndex..., in: code)
                    for match in literal.matches(in: code, range: range) {
                        let text = String(code[Range(match.range, in: code)!])
                        if text.contains("Marcel"), !allowed.contains(text) {
                            found.append("\(package)/\(file.lastPathComponent): \(text)")
                        }
                    }
                }
            }
            XCTAssertTrue(FileManager.default.fileExists(atPath: sources.path), "\(package) sources were read")
        }
        XCTAssertEqual(found, [], "user-facing words that hard-code the agent's name")
    }

    /// Review fix: an attached item the hub no longer has (404 `artifact_not_found`) would fail every retry the same
    /// way. The bubble says so and a tap sends the message without the item.
    func testAMessageWhoseItemIsGoneIsSentWithoutItOnRetry() async throws {
        let fed = try FedCache()
        let hub = FakeService()
        hub.sendError = ChatError.itemGone
        let model = ChatModel(service: hub, conversationId: Fx.conversation)
        model.attach(offers)
        model.draft = "Why?"
        await model.send()
        XCTAssertEqual(model.problem, ChatError.itemGone.message)
        let line = try XCTUnwrap(users(ChatFeed.items(try fed.input(), local: model.local)).first)
        XCTAssertEqual(line.status, .failed)
        XCTAssertTrue(line.itemGone)
        XCTAssertTrue(line.accessibilityText.hasSuffix(
            "The item is gone from the hub. Double-tap to send it without the item."))

        hub.sendError = nil
        await model.retry(clientId: try XCTUnwrap(line.clientId))
        XCTAssertEqual(hub.sent.map(\.attachment), [offers, nil], "the retry goes without the item")
        XCTAssertNil(model.problem)
        let sent = try XCTUnwrap(users(ChatFeed.items(try fed.input(), local: model.local)).first)
        XCTAssertEqual(sent.status, .sent)
        XCTAssertNil(sent.attachment)
    }

    /// Review fix: the hub's 404 says which thing is gone, by its code: the attached item, or the conversation.
    func testASend404SaysWhichThingIsGone() async throws {
        for (code, words, itemGone) in [
            ("artifact_not_found",
             "Not sent: the item you attached isn't on the hub any more. Tap the message to send it without the item.",
             true),
            ("conversation_not_found", "Not sent: the hub does not know this conversation.", false),
        ] {
            let hub = StubHub(status: 404, body: #"{"code":"\#(code)","message":"No."}"#)
            let service = LiveChatService(client: HubClient.make(serverURL: URL(string: "http://hub.test/api")!,
                                                                 transport: hub))
            do {
                _ = try await service.send(text: "Why?", clientId: "c1", conversationId: Fx.conversation,
                                           attachment: offers)
                XCTFail("\(code): sent")
            } catch let error as ChatError {
                XCTAssertEqual(error.message, words, code)
                XCTAssertEqual(error.itemGone, itemGone, code)
            }
        }
    }
}

/// A hub that answers every request the same way, through the real generated client.
final class StubHub: ClientTransport, @unchecked Sendable {
    let status: Int
    let body: String

    init(status: Int, body: String) {
        self.status = status
        self.body = body
    }

    func send(_ request: HTTPRequest, body: HTTPBody?, baseURL: URL, operationID: String) async throws -> (
        HTTPResponse, HTTPBody?
    ) {
        var fields = HTTPFields()
        fields[.contentType] = "application/json"
        return (HTTPResponse(status: .init(code: status), headerFields: fields), HTTPBody(self.body))
    }
}
