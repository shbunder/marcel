import MarcelKit
import XCTest

@testable import MarcelChat

/// Sending: the composer is never locked, a message shows at once, and one that does not get through can be sent
/// again without doubling it.
@MainActor
final class ComposerTests: XCTestCase {
    private func users(_ items: [FeedItem]) -> [UserLine] {
        items.compactMap { if case .user(let line) = $0 { line } else { nil } }
    }

    func testAMessageShowsAtOnceAndGivesWayToTheHubsCopy() async throws {
        let fed = try FedCache()
        let hub = FakeService()
        hub.holdSends = true
        let model = ChatModel(service: hub, conversationId: Fx.conversation)
        model.draft = "  Compare energy contracts  "

        let sending = Task { await model.send() }
        while hub.sent.isEmpty { await Task.yield() }
        XCTAssertEqual(model.draft, "", "the composer is cleared for the next message straight away")
        XCTAssertEqual(users(ChatFeed.items(try fed.input(), local: model.local)).map(\.status), [.sending])
        hub.release()
        await sending.value

        let clientId = try XCTUnwrap(hub.sent.first?.clientId)
        XCTAssertEqual(hub.sent.first?.text, "Compare energy contracts")
        XCTAssertEqual(users(ChatFeed.items(try fed.input(), local: model.local)).map(\.status), [.sent],
                       "stored by the hub, even before the stream brings it")

        // The stream brings the hub's copy: one bubble, from the cache.
        try fed.send("message.created", Fx.message("msg_1", at: 1, role: "user", text: "Compare energy contracts",
                                                   extra: ["client_id": clientId]))
        model.prune(try fed.input())
        XCTAssertEqual(users(ChatFeed.items(try fed.input(), local: model.local)).map(\.id), ["msg_1"])
        XCTAssertTrue(model.local.pending.isEmpty)
    }

    func testYouCanSendWhileThreadsWorkAndAQuestionWaits() async throws {
        let hub = FakeService()
        let model = ChatModel(service: hub, conversationId: Fx.conversation)
        model.draft = "What's the weather Sunday?"
        await model.send()
        model.draft = "And Monday?"
        await model.send()
        XCTAssertEqual(hub.sent.map(\.text), ["What's the weather Sunday?", "And Monday?"])

        let waiting: [FeedItem] = [.approval(ApprovalLine(id: "a", approvalId: "apr", taskId: "t", question: "?",
                                                          detail: nil, status: .open, showsDetail: false))]
        XCTAssertEqual(Composer.placeholder(questionWaiting: ChatFeed.hasOpenQuestion(waiting)), "Or say something…")
        XCTAssertEqual(Composer.placeholder(questionWaiting: ChatFeed.hasOpenQuestion([])), "Talk to Marcel…")
    }

    /// Regression: with a full window (50 rows) a sent message's hub copy does not change the row count. The
    /// screen must still notice it arrived, or the "on its way" copy stays and comes back as a ghost row later.
    func testASentMessageIsClearedWhenItArrivesInAFullWindow() async throws {
        let fed = try FedCache()
        for n in 1...ChatModel.pageSize {
            try fed.send("message.created", Fx.message("msg_old_\(n)", at: n, text: "Old \(n)"))
        }
        let hub = FakeService()
        let model = ChatModel(service: hub, conversationId: Fx.conversation)
        model.draft = "Is the bin out?"
        await model.send()
        let before = try fed.input()
        XCTAssertEqual(before.messages.count, ChatModel.pageSize)

        let clientId = try XCTUnwrap(hub.sent.first?.clientId)
        try fed.send("message.created", Fx.message("msg_mine", at: 100, role: "user", text: "Is the bin out?",
                                                   extra: ["client_id": clientId]))
        let after = try fed.input()
        XCTAssertEqual(after.messages.count, before.messages.count, "the window is still full")
        XCTAssertNotEqual(LiveFeed.arrivalKey(after), LiveFeed.arrivalKey(before),
                          "the screen's trigger fires although the count did not change")
        model.prune(after)
        XCTAssertTrue(model.local.pending.isEmpty)

        // Later, when it has scrolled out of the window, it does not come back as a ghost.
        for n in 1...ChatModel.pageSize {
            try fed.send("message.created", Fx.message("msg_new_\(n)", at: 200 + n, text: "New \(n)"))
        }
        let later = ChatFeed.items(try fed.input(), local: model.local)
        XCTAssertFalse(later.contains { if case .user(let u) = $0 { u.text == "Is the bin out?" } else { false } })
    }

    func testAReplayedEventAddsNoRow() async throws {
        let fed = try FedCache()
        let hub = FakeService()
        let model = ChatModel(service: hub, conversationId: Fx.conversation)
        model.draft = "Hello"
        await model.send()
        let clientId = try XCTUnwrap(hub.sent.first?.clientId)
        let copy = Fx.message("msg_1", at: 1, role: "user", text: "Hello", extra: ["client_id": clientId])
        try fed.send("message.created", copy)
        try fed.send("message.created", Fx.message("msg_2", at: 2, text: "Hi!"))
        // The hub replays after a reconnect: the same messages arrive again.
        try fed.send("message.created", copy)
        try fed.send("message.created", Fx.message("msg_2", at: 2, text: "Hi!"))
        model.prune(try fed.input())
        XCTAssertEqual(ChatFeed.items(try fed.input(), local: model.local).map(\.id), ["msg_1", "msg_2"])
    }

    func testAnEmptyMessageIsNotSent() async {
        let hub = FakeService()
        let model = ChatModel(service: hub, conversationId: Fx.conversation)
        model.draft = "  \n "
        await model.send()
        XCTAssertTrue(hub.sent.isEmpty)
        XCTAssertTrue(model.local.pending.isEmpty)
    }

    func testAMessageThatDidNotGetThroughIsKeptAndRetriedWithTheSameId() async throws {
        let fed = try FedCache()
        let hub = FakeService(failing: true)
        let model = ChatModel(service: hub, conversationId: Fx.conversation)
        model.draft = "Book the dentist"
        await model.send()

        let line = try XCTUnwrap(users(ChatFeed.items(try fed.input(), local: model.local)).first)
        XCTAssertEqual(line.status, .failed)
        XCTAssertEqual(line.accessibilityText, "Not sent: Book the dentist. Double-tap to try again.")
        XCTAssertEqual(model.problem, "Not sent: can't reach the hub. Tap the message to try again.")

        hub.failing = false
        await model.retry(clientId: try XCTUnwrap(line.clientId))
        XCTAssertEqual(hub.sent.count, 2)
        XCTAssertEqual(hub.sent[0].clientId, hub.sent[1].clientId, "a retry reuses the client id: no duplicate")
        XCTAssertNil(model.problem)
        XCTAssertEqual(users(ChatFeed.items(try fed.input(), local: model.local)).map(\.status), [.sent])

        await model.retry(clientId: hub.sent[0].clientId)
        XCTAssertEqual(hub.sent.count, 2, "only a failed message is sent again")
    }

    func testWithoutAConversationTheMessageWaitsToBeRetried() async {
        let hub = FakeService()
        let model = ChatModel(service: hub)
        model.draft = "Hello"
        await model.send()
        XCTAssertTrue(hub.sent.isEmpty)
        XCTAssertEqual(model.local.pending.first?.failed, true)
        XCTAssertEqual(model.problem, "Not sent: the conversation has not loaded yet. Tap the message to try again.")
    }
}
