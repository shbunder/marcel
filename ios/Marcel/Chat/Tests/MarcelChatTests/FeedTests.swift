import Foundation
import MarcelKit
import PixelKit
import XCTest

@testable import MarcelChat

@MainActor
final class FeedTests: XCTestCase {
    func testYourMessagesAndMarcelsRepliesShowInOrder() throws {
        let fed = try FedCache()
        try fed.send("message.created", Fx.message("msg_2", at: 2, text: "18° and **dry**. Good for the garden."))
        try fed.send("message.created", Fx.message("msg_1", at: 1, role: "user", kind: "text",
                                                   text: "What's the weather Sunday?"))

        let items = try fed.items()
        XCTAssertEqual(items.first?.id, "time-msg_1", "the window starts with its time")
        XCTAssertEqual(Array(items.dropFirst()), [
            .user(UserLine(id: "msg_1", text: "What's the weather Sunday?")),
            .marcel(MarcelLine(id: "msg_2", markdown: "18° and **dry**. Good for the garden.")),
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

/// Marcel's replies are parsed by PixelKit's shared Markdown (S-18.3 retired ChatMarkdown): the same promises hold.
final class ReplyTests: XCTestCase {
    func testMarkersGoAndBlocksKeepTheirLines() {
        let line = MarcelLine(id: "m1", markdown: """
        Started **two** quests:

        - Energy contracts
        - Sunday lunch

        1. First
        2. Second

        Run `pytest` and see [the PR](https://github.com/shbunder/marcel/pull/14).
        """)
        XCTAssertEqual(line.text, """
        Started two quests:
        Energy contracts
        Sunday lunch
        1. First
        2. Second
        Run pytest and see the PR.
        """)
        XCTAssertEqual(line.parts.flatMap(\.spans).compactMap(\.link),
                       [URL(string: "https://github.com/shbunder/marcel/pull/14")!])
        XCTAssertEqual(line.parts.first?.spans.first { $0.bold }?.text, "two")
    }

    func testOnlyWebLinksAreLinks() {
        let line = MarcelLine(id: "m1", markdown: """
        [Pay](javascript:alert(1)), [call](tel:+3212345678), [run](shortcuts://run-shortcut?name=x), \
        [mail](mailto:a@b.be), [the board](https://www.example.com/b) and [again](https://www.example.com/b).
        """)
        XCTAssertEqual(line.text, "Pay, call, run, mail, the board and again.")
        XCTAssertEqual(line.parts.flatMap(\.links).map(\.title), ["the board", "again"], "two identical links are both kept")
        XCTAssertNil(ItemMarkdown.webLink(URL(string: "marcel://pair?code=x")!))
    }

    func testALinkWithABoldWordIsOneLink() {
        let line = MarcelLine(id: "m1", markdown: "See [the **new** board](https://example.com/b).")
        XCTAssertEqual(line.parts.flatMap(\.links).map(\.title), ["the new board"])
    }

    func testPlainTextStaysAsItIs() {
        XCTAssertEqual(MarcelLine(id: "m1", markdown: "−€486/jaar, l’été").text, "−€486/jaar, l’été")
    }
}

/// "Today 08:12" between groups more than 15 minutes apart, and above the first message of the window.
final class TimeLineTests: XCTestCase {
    private let calendar: Calendar = {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "Europe/Brussels")!
        return calendar
    }()

    private func date(_ day: Int, _ hour: Int, _ minute: Int) -> Date {
        calendar.date(from: DateComponents(year: 2026, month: 10, day: day, hour: hour, minute: minute))!
    }

    func testTheWordsForTodayYesterdayAndEarlier() {
        let now = date(13, 18, 0)
        let locale = Locale(identifier: "en_GB")
        func text(_ at: Date, first: Bool = false) -> String {
            var timeZoneCalendar = calendar
            timeZoneCalendar.locale = locale
            return TimeLine(id: "t", date: at).text(now: now, first: first, calendar: timeZoneCalendar, locale: locale)
        }
        XCTAssertEqual(text(date(13, 8, 12), first: true), "Today 08:12")
        XCTAssertEqual(text(date(13, 8, 31)), "08:31")
        XCTAssertEqual(text(date(12, 21, 40)), "Yesterday 21:40")
        XCTAssertTrue(text(date(5, 9, 0)).hasSuffix("09:00"))
        XCTAssertTrue(text(date(5, 9, 0)).contains("Oct"), text(date(5, 9, 0)))
    }

    func testATimeLineStartsTheWindowAndSeparatesGroupsMoreThan15MinutesApart() throws {
        func message(_ id: String, minutes: Double) -> Message {
            let json = #"{"id":"\#(id)","conversation_id":"cnv_main_marcel","role":"agent","kind":"text","text":"\#(id)","created_at":"\#(Date(timeIntervalSince1970: 1_791_000_000 + minutes * 60).formatted(.iso8601))"}"#
            return try! ChatJSON.decoder().decode(Message.self, from: Data(json.utf8))
        }
        let items = ChatFeed.items(FeedInput(messages: [
            message("a", minutes: 0), message("b", minutes: 10), message("c", minutes: 26), message("d", minutes: 41),
        ]))
        XCTAssertEqual(items.map(\.id), ["time-a", "a", "b", "time-c", "c", "d"],
                       "b is 10 min after a, c 16 min after b, d 15 min after c (not more)")
    }
}
