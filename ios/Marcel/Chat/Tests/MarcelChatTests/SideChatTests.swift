import Foundation
import HTTPTypes
import MarcelKit
import OpenAPIRuntime
import SwiftData
import XCTest

@testable import MarcelChat

/// The side chat (S-12.4, B-08, wireframe 2B), driven through the screen's models with a fake hub and a real
/// in-memory cache fed with event frames. XCUITest cannot run on this Mac, so `testOpenReplySendToMain` is the
/// story's "UI test: open, reply, send to main", one layer down.
@MainActor
final class SideChatTests: XCTestCase {
    private let side = "cnv_side_m1"

    private func sideMessage(_ id: String, at second: Int, role: String = "agent", text: String,
                             extra: [String: Any] = [:]) -> [String: Any] {
        Fx.message(id, at: second, role: role, text: text, extra: ["conversation_id": side].merging(extra) { _, b in b })
    }

    /// How many messages the cache holds per side thread: what the live screen counts for "n replies".
    private func counts(_ fed: FedCache) throws -> [String: Int] {
        let main = Fx.conversation
        let rows = try fed.cache.container.mainContext.fetch(
            FetchDescriptor<CachedMessage>(predicate: #Predicate { $0.conversationId != main }))
        return Dictionary(grouping: rows, by: \.conversationId).mapValues(\.count)
    }

    private func threadInput(_ fed: FedCache, _ conversationId: String) throws -> FeedInput {
        try FeedInput.read(from: fed.cache.container.mainContext, conversationId: conversationId, limit: 200)
    }

    private func parent(_ fed: FedCache, _ id: String, name: String = "Marcel") throws -> SideChatParent {
        let input = try fed.input()
        let item = try XCTUnwrap(ChatFeed.items(input).first { $0.id == id })
        return try XCTUnwrap(SideChatParent.of(item, message: input.messages.first { $0.id == id }, agentName: name))
    }

    // MARK: The story's flow

    func testOpenReplySendToMain() async throws {
        let fed = try FedCache()
        try fed.send("message.created", Fx.message("m1", at: 1, text: "18° and dry. Good for the garden lunch option."))
        let hub = FakeService()
        let chat = ChatModel(service: hub, conversationId: Fx.conversation)
        XCTAssertTrue(chat.canReplyInThread)

        // Long-press ▸ Reply in thread: the sheet opens on that message.
        chat.openSideChat(try parent(fed, "m1"))
        let model = try XCTUnwrap(chat.sideChat)
        XCTAssertEqual(model.parent.speaker, "Marcel")
        XCTAssertEqual(model.parent.text, "18° and dry. Good for the garden lunch option.")
        model.cache = fed.cache
        await model.open()
        XCTAssertEqual(hub.side.opened, ["m1"])
        XCTAssertEqual(model.conversationId, side)
        XCTAssertNil(model.problem)
        var look = SideChatLook.of(model, task: nil, messages: [])
        XCTAssertEqual(look.detail, "Sees the main chat")
        XCTAssertEqual(look.placeholder, "Reply in side chat…")
        XCTAssertFalse(look.canSendToMain, "nothing to send before an answer")

        // Reply: it shows at once, goes to the thread, and the stream brings it back.
        model.draft = "And if it rains after three?"
        await model.send()
        XCTAssertEqual(hub.side.replies.map(\.text), ["And if it rains after three?"])
        XCTAssertEqual(hub.side.replies.map(\.conversationId), [side])
        XCTAssertEqual(model.draft, "")
        let clientId = try XCTUnwrap(hub.side.replies.first?.clientId)
        var items = SideChatFeed.items(try threadInput(fed, side), model: model, approvals: ChatLocalState())
        guard case .user(let sent) = items.last else { return XCTFail("the reply is not shown") }
        XCTAssertEqual(sent.status, .sent)

        try fed.send("message.created", sideMessage("s1", at: 10, role: "user", text: "And if it rains after three?",
                                                    extra: ["client_id": clientId]))
        try fed.send("message.created", sideMessage("s2", at: 11, text:
            "Then the covered terrace: same plan, ten metres closer to the kitchen."))
        let thread = try threadInput(fed, side)
        model.prune(thread.messages)
        XCTAssertTrue(model.pending.isEmpty)
        items = SideChatFeed.items(thread, model: model, approvals: ChatLocalState())
        XCTAssertEqual(items.map(\.id), ["s1", "s2"], "one copy of the reply, then the answer")

        // The main chat does not see the thread, but its message says "2 replies", kept live.
        XCTAssertEqual(try fed.items().map(\.id), ["m1"])
        let summary = chat.sideChats.summaries(for: try fed.input().messages, counts: try counts(fed))["m1"]
        XCTAssertEqual(summary?.label, "2 replies")
        XCTAssertEqual(summary?.accessibilityLabel, "Side chat, 2 replies")

        // Send result to main chat: asks first, showing exactly what will be sent.
        look = SideChatLook.of(model, task: nil, messages: thread.messages)
        XCTAssertTrue(look.canSendToMain)
        model.proposeSendToMain(thread.messages)
        let proposal = try XCTUnwrap(model.proposal)
        XCTAssertEqual(proposal.messageId, "s2")
        XCTAssertEqual(SideChatFeed.confirmation(proposal),
                       "The main chat will show this as a quote:\n\n“Then the covered terrace: same plan, ten metres closer to the kitchen.”")
        XCTAssertTrue(hub.side.sentToMain.isEmpty, "nothing is sent before you confirm")
        await model.confirmSendToMain().value
        XCTAssertEqual(hub.side.sentToMain.map(\.conversationId), [side])
        XCTAssertEqual(hub.side.sentToMain.map(\.messageId), ["s2"])
        XCTAssertNil(model.proposal)
        XCTAssertTrue(SideChatLook.of(model, task: nil, messages: thread.messages).sentToMain)

        // The quote arrives in the main chat over the stream.
        try fed.send("message.created", Fx.message("m2", at: 20, role: "user", kind: "quote", text:
            "Then the covered terrace: same plan, ten metres closer to the kitchen.",
            extra: ["quoted_message_id": "s2"]))
        XCTAssertEqual(try fed.items().map(\.id), ["m1", "m2"])
    }

    func testCancellingSendToMainSendsNothing() async throws {
        let fed = try FedCache()
        try fed.send("message.created", Fx.message("m1", at: 1, text: "Hi"))
        try fed.send("message.created", sideMessage("s1", at: 2, text: "An answer"))
        let hub = FakeService()
        let model = SideChatModel(parent: try parent(fed, "m1"), service: hub, chats: SideChats())
        await model.open()
        model.proposeSendToMain(try threadInput(fed, side).messages)
        XCTAssertNotNil(model.proposal)
        model.cancelSendToMain()
        await model.confirmSendToMain().value
        XCTAssertTrue(hub.side.sentToMain.isEmpty)
        XCTAssertEqual(model.problem, "Nothing was sent: there was no answer to send. Try again.")

        // A thread with only your words has nothing to send.
        model.proposeSendToMain([Fx.decode(sideMessage("s3", at: 3, role: "user", text: "Mine"))])
        XCTAssertNil(model.proposal)
        XCTAssertEqual(model.problem, "Nothing to send yet: no answer in this side chat.")
    }

    // MARK: Review fixes

    /// Review fix 1: the dialog closes (which cancels the proposal) before the send's task runs; the confirmed
    /// proposal is still sent.
    func testConfirmingInTheDialogSendsEvenAfterItCloses() async throws {
        let fed = try FedCache()
        try fed.send("message.created", Fx.message("m1", at: 1, text: "Hi"))
        try fed.send("message.created", sideMessage("s1", at: 2, text: "An answer"))
        let hub = FakeService()
        let model = SideChatModel(parent: try parent(fed, "m1"), service: hub, chats: SideChats())
        await model.open()
        model.proposeSendToMain(try threadInput(fed, side).messages)
        let send = model.confirmSendToMain()  // the dialog's "Send to main chat" button
        model.cancelSendToMain()              // the dialog's binding, as it closes
        await send.value
        XCTAssertEqual(hub.side.sentToMain.map(\.messageId), ["s1"])
        XCTAssertEqual(model.sentToMain, "s1")
        XCTAssertNil(model.problem)
    }

    /// Review fix 1: a send that cannot happen says why instead of returning quietly.
    func testSendToMainNeverFailsQuietly() async {
        let hub = FakeService()
        let model = SideChatModel(parent: SideChatParent(id: "m1", speaker: "You", text: "Hi"), service: hub,
                                  chats: SideChats())
        await model.sendToMain(SendToMainProposal(messageId: "s1", preview: "An answer"))
        XCTAssertEqual(model.problem, "Not sent: the side chat has not opened yet. Try again in a moment.")
        await model.sendToMain(nil)
        XCTAssertEqual(model.problem, "Nothing was sent: there was no answer to send. Try again.")
        XCTAssertTrue(hub.side.sentToMain.isEmpty)
    }

    /// Review fix 2: a reply sent while the thread is still opening waits for that open, then goes.
    func testAReplyWhileTheThreadOpensIsSent() async throws {
        let hub = FakeService()
        hub.side.holdOpens = true
        let model = SideChatModel(parent: SideChatParent(id: "m1", speaker: "You", text: "Hi"), service: hub,
                                  chats: SideChats())
        let opening = Task { await model.open() }
        while hub.side.heldOpens.isEmpty { await Task.yield() }
        model.draft = "Quick question"
        let sending = Task { await model.send() }
        for _ in 0..<20 { await Task.yield() }
        XCTAssertTrue(hub.side.replies.isEmpty, "waits for the open")
        hub.side.holdOpens = false
        hub.side.releaseOpens()
        await opening.value
        await sending.value
        XCTAssertEqual(hub.side.opened, ["m1"], "one open, shared")
        XCTAssertEqual(hub.side.replies.map(\.text), ["Quick question"])
        XCTAssertEqual(hub.side.replies.map(\.conversationId), [side])
        XCTAssertEqual(model.pending.first?.failed, false)
        XCTAssertNil(model.problem)
    }

    /// Review fix 3: a second tap on "n replies" (or reopening) keeps the same side chat, so its open has run and
    /// a reply on its way is still there.
    func testTappingTwiceKeepsOneSideChat() async throws {
        let hub = FakeService()
        let chat = ChatModel(service: hub, conversationId: Fx.conversation)
        let parent = SideChatParent(id: "m1", speaker: "Marcel", text: "Hi")
        chat.openSideChat(parent)
        let first = try XCTUnwrap(chat.sideChat)
        await first.open()
        hub.side.replyError = .offline
        first.draft = "Kept"
        await first.send()
        chat.openSideChat(parent)
        XCTAssertTrue(chat.sideChat === first, "the second tap shows the same side chat")
        chat.sideChat = nil  // the sheet closes
        chat.openSideChat(parent)
        XCTAssertTrue(chat.sideChat === first, "reopening too")
        XCTAssertEqual(first.conversationId, side)
        XCTAssertEqual(first.pending.map(\.text), ["Kept"])
        chat.openSideChat(SideChatParent(id: "m2", speaker: "You", text: "Other"))
        XCTAssertFalse(chat.sideChat === first, "another message has its own")
    }

    /// Review fix 4: "n replies" counts only the threads on screen, once, and again when the stream adds a message to
    /// one of them; a redraw reads the kept counts.
    func testReplyCountsAreKeptNotRecountedOnRedraw() throws {
        let fed = try FedCache()
        try fed.send("message.created", Fx.message("m1", at: 1, text: "Hi", extra: [
            "thread": ["conversation_id": side, "reply_count": 0],
        ]))
        try fed.send("message.created", sideMessage("s1", at: 2, text: "One"))
        try fed.send("message.created", Fx.message("o1", at: 3, text: "Elsewhere",
                                                   extra: ["conversation_id": "cnv_side_other"]))
        let chats = SideChats()
        let context = fed.cache.container.mainContext
        let messages = try fed.input().messages
        XCTAssertEqual(chats.threadIds(of: messages), [side])
        XCTAssertNil(chats.summaries(for: messages)["m1"], "nothing counted yet")
        chats.recount(chats.threadIds(of: messages), in: context)
        XCTAssertEqual(chats.counts, [side: 1], "only the threads on screen")
        XCTAssertEqual(chats.summaries(for: messages)["m1"]?.label, "1 reply")

        // A new reply over the stream: the cache stores it, then the frame recounts that thread.
        let frame = Fx.frame("message.created", sideMessage("s2", at: 4, text: "Two"), id: 90)
        try fed.cache.apply(frame)
        XCTAssertEqual(chats.summaries(for: messages)["m1"]?.label, "1 reply", "a redraw does not recount")
        chats.receive(frame, context: context)
        XCTAssertEqual(chats.summaries(for: messages)["m1"]?.label, "2 replies")
    }

    // MARK: "n replies"

    func testRepliesFollowTheStreamAndOtherDevices() throws {
        let fed = try FedCache()
        // The hub said on the message: 3 replies the phone has not seen.
        try fed.send("message.created", Fx.message("m1", at: 1, text: "Hi", extra: [
            "thread": ["conversation_id": side, "reply_count": 3],
        ]))
        try fed.send("message.created", Fx.message("m2", at: 2, text: "Later"))
        let chats = SideChats()
        var summaries = chats.summaries(for: try fed.input().messages, counts: try counts(fed))
        XCTAssertEqual(summaries["m1"]?.label, "3 replies")
        XCTAssertNil(summaries["m2"], "no thread, no line")

        // A thread opened on another device: `conversation.created`, then a reply over the stream.
        chats.receive(Fx.frame("conversation.created", [
            "id": "cnv_side_m2", "agent_id": "agt_marcel", "kind": "side", "parent_message_id": "m2",
            "created_at": Fx.at(3),
        ], id: 50))
        summaries = chats.summaries(for: try fed.input().messages, counts: try counts(fed))
        XCTAssertNil(summaries["m2"], "an empty thread says nothing yet")
        try fed.send("message.created", Fx.message("s9", at: 4, text: "On it", extra: ["conversation_id": "cnv_side_m2"]))
        summaries = chats.summaries(for: try fed.input().messages, counts: try counts(fed))
        XCTAssertEqual(summaries["m2"]?.label, "1 reply")

        // More replies than the hub last said: the cache's count wins.
        for n in 0..<4 {
            try fed.send("message.created", sideMessage("x\(n)", at: 10 + n, text: "Reply \(n)"))
        }
        summaries = chats.summaries(for: try fed.input().messages, counts: try counts(fed))
        XCTAssertEqual(summaries["m1"]?.label, "4 replies")
    }

    // MARK: Task threads

    func testATaskThreadGoesToTheTask() async throws {
        let fed = try FedCache()
        try fed.send("task.created", Fx.task("t1", title: "Energy contracts", state: "done"))
        try fed.send("message.created", Fx.milestone("m1", at: 1, task: "t1", title: "Energy contracts", event: "done",
                                                     summary: "Supplier A is cheapest."))
        let hub = FakeService()
        hub.side.taskIds["m1"] = "t1"
        let model = SideChatModel(parent: try parent(fed, "m1"), service: hub, chats: SideChats())
        XCTAssertEqual(model.taskId, "t1", "known from the milestone before the hub answers")
        await model.open()
        let task = try XCTUnwrap(try fed.input().tasks["t1"])
        let look = SideChatLook.of(model, task: task, messages: [])
        XCTAssertEqual(look.detail, "Replies go to Energy contracts")
        XCTAssertEqual(look.placeholder, "Ask a follow-up…", "a done task takes follow-ups")
        XCTAssertEqual(look.taskId, "t1", "Open quest shows")
        XCTAssertFalse(look.stopped)

        model.draft = "Also compare with a heat pump"
        await model.send()
        XCTAssertEqual(hub.side.replies.map(\.text), ["Also compare with a heat pump"])
        XCTAssertNil(model.problem)
    }

    func testAStoppedTaskSaysSoInPlainWords() async throws {
        let fed = try FedCache()
        try fed.send("task.created", Fx.task("t1", title: "Energy contracts", state: "working"))
        try fed.send("message.created", Fx.milestone("m1", at: 1, task: "t1", title: "Energy contracts",
                                                     event: "started"))
        let hub = FakeService()
        hub.side.taskIds["m1"] = "t1"
        hub.side.replyError = .taskFinished
        let chat = ChatModel(service: hub, conversationId: Fx.conversation)
        chat.openSideChat(try parent(fed, "m1"))
        let model = try XCTUnwrap(chat.sideChat)
        await model.open()
        model.draft = "Use 4 kWp"
        await model.send()
        XCTAssertTrue(model.taskFinished)
        XCTAssertEqual(model.problem, "This task has stopped; start a new one?")
        var look = SideChatLook.of(model, task: try fed.input().tasks["t1"], messages: [])
        XCTAssertTrue(look.stopped, "the notice shows")
        XCTAssertNil(look.problem, "said once, in the notice")
        let line = try XCTUnwrap(model.pending.first)
        XCTAssertTrue(line.failed)

        // "Start a new one": your words go to the main chat's composer, where the agent can start one.
        model.draft = "and the roof is small"
        let words = model.startOver()
        XCTAssertEqual(words, "Use 4 kWp\nand the roof is small")
        XCTAssertTrue(model.pending.isEmpty)
        XCTAssertEqual(model.draft, "")
        chat.propose(words)
        XCTAssertEqual(chat.draft, words)

        // A task the cache already knows is stopped shows the notice before you try.
        try fed.send("task.updated", Fx.task("t1", title: "Energy contracts", state: "stopped", at: 5))
        let fresh = SideChatModel(parent: try parent(fed, "m1"), service: hub, chats: SideChats())
        look = SideChatLook.of(fresh, task: try fed.input().tasks["t1"], messages: [])
        XCTAssertTrue(look.stopped)
    }

    // MARK: Drafts

    func testDraftsArePerThread() throws {
        let hub = FakeService()
        let chat = ChatModel(service: hub, conversationId: Fx.conversation)
        let a = SideChatParent(id: "m1", speaker: "Marcel", text: "One")
        let b = SideChatParent(id: "m2", speaker: "You", text: "Two")
        chat.openSideChat(a)
        chat.sideChat?.draft = "Half a thought"
        chat.sideChat = nil
        chat.openSideChat(b)
        XCTAssertEqual(chat.sideChat?.draft, "", "another thread has its own draft")
        chat.sideChat?.draft = "Something else"
        chat.openSideChat(a)
        XCTAssertEqual(chat.sideChat?.draft, "Half a thought", "reopening keeps what you typed")
        XCTAssertEqual(chat.draft, "", "the main chat's draft is untouched")
    }

    func testAKnownThreadShowsFromTheCacheOffline() async throws {
        let fed = try FedCache()
        try fed.send("message.created", Fx.message("m1", at: 1, text: "Hi", extra: [
            "thread": ["conversation_id": side, "reply_count": 1],
        ]))
        try fed.send("message.created", sideMessage("s1", at: 2, text: "Saved answer"))
        let hub = FakeService()
        hub.side.openError = .offline
        let model = SideChatModel(parent: try parent(fed, "m1"), service: hub, chats: SideChats())
        XCTAssertEqual(model.conversationId, side, "known before the hub answers")
        await model.open()
        XCTAssertEqual(model.problem, "Can't reach the hub right now. Check the connection and try again.")
        let items = SideChatFeed.items(try threadInput(fed, side), model: model, approvals: ChatLocalState())
        XCTAssertEqual(items.map(\.id), ["s1"])

        // A reply offline stays, marked not sent; a retry reuses its client id.
        hub.side.replyError = .offline
        model.draft = "Thanks"
        await model.send()
        XCTAssertEqual(model.pending.first?.failed, true)
        hub.side.openError = nil
        hub.side.replyError = nil
        let clientId = try XCTUnwrap(model.pending.first?.clientId)
        await model.retry(clientId: clientId)
        XCTAssertEqual(model.pending.first?.failed, false)
        XCTAssertEqual(hub.side.replies.map(\.clientId).suffix(1), [clientId])
    }

    func testAGoneMessageSaysSo() async throws {
        let hub = FakeService()
        hub.side.openError = SideChatError.missing("message_not_found")
        let model = SideChatModel(parent: SideChatParent(id: "m1", speaker: "You", text: "Hi"), service: hub,
                                  chats: SideChats())
        await model.open()
        XCTAssertTrue(model.gone)
        XCTAssertEqual(model.problem, "That message is gone from the hub, so it has no side chat.")
        model.draft = "Hello?"
        await model.send()
        XCTAssertTrue(hub.side.replies.isEmpty, "nothing is posted to a thread that is gone")
    }

    // MARK: The real client's answers, in plain words

    func testTheHubsAnswersInPlainWords() async throws {
        let cases: [(Int, String, String)] = [
            (401, #"{"code":"unauthorized","message":"No."}"#, "The hub no longer accepts this phone. Pair it again."),
            (500, #"{"code":"internal_error","message":"Boom."}"#, "The hub had a problem. Try again in a moment."),
            (503, "Service Unavailable", "The hub had a problem. Try again in a moment."),
            (404, #"{"code":"message_not_found","message":"No."}"#,
             "That message is gone from the hub, so it has no side chat."),
            (404, #"{"code":"not_found","message":"No."}"#, "This hub can't do side chats yet. Update the hub."),
            (404, #"{"detail":"Not Found"}"#, "This hub can't do side chats yet. Update the hub."),
        ]
        for (status, body, words) in cases {
            let service = LiveChatService(client: HubClient.make(
                serverURL: URL(string: "http://hub.test/api")!, transport: StubHub(status: status, body: body)))
            do {
                _ = try await service.openThread(messageId: "m1")
                XCTFail("\(status) \(body): opened")
            } catch let error as SideChatError {
                XCTAssertEqual(error.message, words, "\(status) \(body)")
            }
        }

        let finished = LiveChatService(client: HubClient.make(
            serverURL: URL(string: "http://hub.test/api")!,
            transport: StubHub(status: 409, body: #"{"code":"task_finished","message":"Stopped."}"#)))
        do {
            _ = try await finished.reply(text: "Hi", clientId: "c1", conversationId: side)
            XCTFail("409: sent")
        } catch let error as SideChatError {
            XCTAssertEqual(error.kind, .taskFinished)
            XCTAssertEqual(error.message, "This task has stopped; start a new one?")
        }

        let gone = LiveChatService(client: HubClient.make(
            serverURL: URL(string: "http://hub.test/api")!,
            transport: StubHub(status: 404, body: #"{"code":"conversation_not_found","message":"No."}"#)))
        do {
            _ = try await gone.sendToMain(conversationId: side, messageId: "s1")
            XCTFail("404: sent")
        } catch let error as SideChatError {
            XCTAssertEqual(error.message, "This side chat is gone from the hub.")
        }

        let offline = LiveChatService(client: HubClient.make(
            serverURL: URL(string: "http://hub.test/api")!, transport: OfflineHub()))
        do {
            _ = try await offline.threadMessages(conversationId: side)
            XCTFail("offline: read")
        } catch let error as SideChatError {
            XCTAssertEqual(error.message, "Can't reach the hub right now. Check the connection and try again.")
        }
    }

    func testTheRequestsMatchTheContract() async throws {
        let hub = RecordingHub()
        let service = LiveChatService(client: HubClient.make(serverURL: URL(string: "http://hub.test/api")!,
                                                             transport: hub))
        _ = try? await service.openThread(messageId: "m1")
        _ = try? await service.sendToMain(conversationId: side, messageId: "s2")
        XCTAssertEqual(hub.requests.map(\.0), ["POST /api/messages/m1/thread",
                                               "POST /api/conversations/\(side)/send-to-main"])
        let body = try XCTUnwrap(hub.requests.last?.1.data(using: .utf8))
        XCTAssertEqual(try JSONSerialization.jsonObject(with: body) as? [String: String], ["message_id": "s2"])
    }

    // MARK: VoiceOver

    func testVoiceOverWords() throws {
        XCTAssertEqual(ThreadSummary(conversationId: side, replies: 1).accessibilityLabel, "Side chat, 1 reply")
        let quest = FeedItem.quest(QuestLine(id: "m1", taskId: "t1", title: "Energy contracts", state: .working,
                                             project: nil, tone: .teal, detail: "On the NUC"))
        let message: Message = Fx.decode(Fx.milestone("m1", at: 1, task: "t1", title: "Energy contracts",
                                                      event: "started"))
        let parent = try XCTUnwrap(SideChatParent.of(quest, message: message, agentName: "Marcella"))
        XCTAssertEqual(parent.speaker, "Energy contracts")
        XCTAssertEqual(parent.taskId, "t1")
        let pending = FeedItem.user(UserLine(id: "pending-c1", text: "Hi", status: .sending, clientId: "c1"))
        XCTAssertNil(SideChatParent.of(pending, message: nil, agentName: "Marcella"), "no thread on a message on its way")
        let said = FeedItem.marcel(MarcelLine(id: "m2", body: ChatMarkdown.render("Hello")))
        let agentMessage: Message = Fx.decode(Fx.message("m2", at: 2, text: "Hello"))
        XCTAssertEqual(SideChatParent.of(said, message: agentMessage, agentName: "Marcella")?.speaker, "Marcella")
    }
}

/// A hub that cannot be reached.
final class OfflineHub: ClientTransport, @unchecked Sendable {
    func send(_ request: HTTPRequest, body: HTTPBody?, baseURL: URL, operationID: String) async throws -> (
        HTTPResponse, HTTPBody?
    ) {
        throw URLError(.notConnectedToInternet)
    }
}

/// A hub that records each request's method, path and body, and answers 500.
final class RecordingHub: ClientTransport, @unchecked Sendable {
    private(set) var requests: [(String, String)] = []

    func send(_ request: HTTPRequest, body: HTTPBody?, baseURL: URL, operationID: String) async throws -> (
        HTTPResponse, HTTPBody?
    ) {
        var text = ""
        if let body { text = try await String(collecting: body, upTo: 10_000) }
        let path = baseURL.path + (request.path ?? "")
        requests.append(("\(request.method.rawValue) \(path)", text))
        return (HTTPResponse(status: .internalServerError), nil)
    }
}
