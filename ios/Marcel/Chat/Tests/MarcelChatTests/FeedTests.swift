import MarcelKit
import XCTest

@testable import MarcelChat

@MainActor
final class FeedTests: XCTestCase {
    func testYourMessagesAndMarcelsRepliesShowInOrder() throws {
        let fed = try FedCache()
        try fed.send("message.created", Fx.message("msg_2", at: 2, text: "18° and **dry**. Good for the garden."))
        try fed.send("message.created", Fx.message("msg_1", at: 1, role: "user", kind: "text",
                                                   text: "What's the weather Sunday?"))

        XCTAssertEqual(try fed.items(), [
            .user(UserLine(id: "msg_1", text: "What's the weather Sunday?")),
            .marcel(MarcelLine(id: "msg_2", body: .init(text: "18° and dry. Good for the garden.", links: []))),
        ])
    }

    // S-12.7 (B-32): a message sent from a project room is in the main chat, tagged with that project.
    func testAMessageSentFromAProjectRoomIsTaggedWithItsProject() throws {
        let fed = try FedCache()
        try fed.send("project.created", Fx.project("prj_energy", name: "Home energy"))
        try fed.send("message.created", Fx.message("msg_1", at: 1, role: "user", text: "Ask about the solar quote",
                                                   extra: ["project_id": "prj_energy"]))
        try fed.send("message.created", Fx.message("msg_2", at: 2, role: "user", text: "And the weather?"))

        let users = try fed.items().compactMap { if case .user(let line) = $0 { line } else { nil } }
        XCTAssertEqual(users.map(\.project), ["Home energy", nil])
        XCTAssertEqual(users[0].tone, ChatFeed.tone(for: "prj_energy"), "the project keeps its colour")
        XCTAssertEqual(users[0].accessibilityText, "You said, about Home energy: Ask about the solar quote")
        XCTAssertEqual(users[1].accessibilityText, "You said: And the weather?")
    }

    func testAnotherConversationsMessagesStayOut() throws {
        let fed = try FedCache()
        var side = Fx.message("msg_side", at: 1, role: "user", text: "In a side thread")
        side["conversation_id"] = "cnv_side_1"
        try fed.send("message.created", side)

        XCTAssertEqual(try fed.items(), [])
    }

    func testTheWindowHoldsTheNewestMessagesAndKnowsThereIsMore() throws {
        let fed = try FedCache()
        for n in 1...5 {
            try fed.send("message.created", Fx.message("msg_\(n)", at: n, role: "user", text: "Message \(n)"))
        }

        let window = try fed.input(limit: 3)
        XCTAssertEqual(window.messages.map(\.id), ["msg_3", "msg_4", "msg_5"])
        XCTAssertTrue(window.hasEarlier)

        let all = try fed.input(limit: 10)
        XCTAssertEqual(all.messages.count, 5)
        XCTAssertFalse(all.hasEarlier)
    }

    func testScrollingUpLoadsTheNextPageOnlyWhenTheWindowIsFull() {
        let model = ChatModel(service: FakeService())
        model.loadEarlier(shown: ChatModel.pageSize - 1)
        XCTAssertEqual(model.limit, ChatModel.pageSize, "a window that is not full has nothing older to load")
        model.loadEarlier(shown: ChatModel.pageSize)
        XCTAssertEqual(model.limit, 2 * ChatModel.pageSize)
    }

    func testTheConversationIdIsRememberedAndAProblemIsSaidPlainly() async {
        var remembered: String?
        let model = ChatModel(service: FakeService(), remember: { remembered = $0 })
        await model.start()
        XCTAssertEqual(model.conversationId, Fx.conversation)
        XCTAssertEqual(remembered, Fx.conversation)

        let offline = ChatModel(service: FakeService(failing: true))
        await offline.start()
        XCTAssertNil(offline.conversationId)
        XCTAssertEqual(offline.problem, "Can't reach the hub right now.")

        let remembers = ChatModel(service: FakeService(failing: true), conversationId: "cnv_saved")
        await remembers.start()
        XCTAssertEqual(remembers.conversationId, "cnv_saved", "offline, the saved conversation still shows")
        XCTAssertNil(remembers.problem)
    }

    func testTheHeaderCountsWhatRunsAndWhatNeedsYou() {
        let tasks: [HubTask] = [
            Fx.decode(Fx.task("t1", title: "A", state: "working")),
            Fx.decode(Fx.task("t2", title: "B", state: "working")),
            Fx.decode(Fx.task("t3", title: "C", state: "needs_you")),
            Fx.decode(Fx.task("t4", title: "D", state: "done")),
        ]
        let header = ChatHeaderState(tasks: tasks)
        XCTAssertEqual(header.avatar, .needsYou, "needs you wins over working, as the contract's AgentState says")
        XCTAssertEqual(header.running, 2)
        XCTAssertEqual(header.needsYou, 1)
        XCTAssertEqual(header.subtitle, "2 quests running")
        XCTAssertEqual(ChatHeaderState(tasks: Array(tasks.prefix(2))).avatar, .working(2))
        XCTAssertEqual(ChatHeaderState(tasks: []).subtitle, "Nothing running")
        XCTAssertEqual(ChatHeaderState(tasks: [], offline: "Offline · showing what's saved").subtitle,
                       "Offline · showing what's saved")
        XCTAssertEqual(AvatarHeader(state: header, collapsed: false).state.accessibilityText,
                       "Marcel needs you. 2 quests running. 1 thing needs you")
    }
}

final class MarkdownTests: XCTestCase {
    func testMarkersGoAndBlocksKeepTheirLines() {
        let rendered = ChatMarkdown.render("""
        Started **two** quests:

        - Energy contracts
        - Sunday lunch

        1. First
        2. Second

        Run `pytest` and see [the PR](https://github.com/shbunder/marcel/pull/14).
        """)
        XCTAssertEqual(rendered.text, """
        Started two quests:

        • Energy contracts
        • Sunday lunch

        1. First
        2. Second

        Run pytest and see the PR.
        """)
        XCTAssertEqual(rendered.links, [.init(title: "the PR", url: URL(string: "https://github.com/shbunder/marcel/pull/14")!)])
    }

    func testOnlyWebLinksBecomeButtonsAndSayWhereTheyGo() {
        let rendered = ChatMarkdown.render("""
        [Pay](javascript:alert(1)), [call](tel:+3212345678), [run](shortcuts://run-shortcut?name=x), \
        [mail](mailto:a@b.be), [the board](https://www.example.com/b) and [again](https://www.example.com/b).
        """)
        XCTAssertEqual(rendered.text, "Pay, call, run, mail, the board and again.")
        XCTAssertEqual(rendered.links.map(\.title), ["the board", "again"], "two identical links are both kept")
        XCTAssertEqual(LinkButton.address(rendered.links[0].url), "example.com")
        XCTAssertEqual(LinkButton.accessibilityText(rendered.links[0]), "Open link: the board, on example.com")
        XCTAssertNil(ChatMarkdown.webLink(URL(string: "javascript:alert(1)")!))
        XCTAssertNil(ChatMarkdown.webLink(URL(string: "marcel://pair?code=x")!))
        XCTAssertNotNil(ChatMarkdown.webLink(URL(string: "http://example.com")!))
    }

    func testALinkWithABoldWordIsOneLink() {
        let rendered = ChatMarkdown.render("See [the **new** board](https://example.com/b).")
        XCTAssertEqual(rendered.links.map(\.title), ["the new board"])
    }

    func testPlainTextStaysAsItIs() {
        XCTAssertEqual(ChatMarkdown.render("−€486/jaar, l’été").text, "−€486/jaar, l’été")
    }
}
