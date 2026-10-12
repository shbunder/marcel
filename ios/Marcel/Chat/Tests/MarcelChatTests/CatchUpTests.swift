import MarcelItems
import MarcelKit
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
        XCTAssertTrue(ChatHeader(state: header, collapsed: false).accessibilityText.hasPrefix("Marcella. "))

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
}
