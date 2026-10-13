import Foundation
import MarcelKit
import PixelKit
import SwiftUI
import UIKit
import XCTest

@testable import MarcelChat

/// S-18.5 (B-41, B-42): the "re:" line above an answer to an older message, and `@` / `#` mentions.
@MainActor
final class QuoteAndMentionTests: XCTestCase {
    // MARK: Quoted replies (B-41)

    func testAnAnswerToAnOlderMessageSaysWhichAndTheLatestDoesNot() throws {
        let fed = try FedCache()
        try fed.send("message.created", Fx.message("u1", at: 1, role: "user", text: "Compare the heat-pump offers"))
        try fed.send("message.created", Fx.milestone("m1", at: 2, task: "t1", title: "Heat pumps", event: "started")
            .merging(["reply_to_message_id": "u1"]) { _, new in new })
        try fed.send("message.created", Fx.message("u2", at: 60, role: "user", text: "What's on Thursday?"))
        try fed.send("message.created", Fx.message("r2", at: 61, text: "Dentist at 9:30.",
                                                   extra: ["reply_to_message_id": "u2"]))
        try fed.send("message.created", Fx.milestone("m2", at: 120, task: "t1", title: "Heat pumps", event: "done",
                                                     summary: "Daikin is cheapest.")
            .merging(["reply_to_message_id": "u1"]) { _, new in new })
        try fed.send("message.created", Fx.message("r3", at: 121, text: "Here is the short version.",
                                                   extra: ["reply_to_message_id": "u1"]))

        let items = try fed.items()
        let quotes = Dictionary(uniqueKeysWithValues: items.map { ($0.id, $0.quote?.messageId) })
        XCTAssertEqual(quotes["m1"] ?? nil, nil, "the card answers your latest message: no line")
        XCTAssertEqual(quotes["r2"] ?? nil, nil, "the reply answers your latest message: no line")
        XCTAssertEqual(quotes["m2"] ?? nil, "u1", "a later line about the first quest answers an older message")
        XCTAssertEqual(quotes["r3"] ?? nil, "u1", "a plain reply to an older message")
        let quote = try XCTUnwrap(items.first { $0.id == "r3" }?.quote)
        XCTAssertEqual(quote.date, Fx.base.addingTimeInterval(1), "the time of the message it answers")
    }

    func testTheCardOfAnOlderQuestCarriesTheLineToo() throws {
        let fed = try FedCache()
        try fed.send("message.created", Fx.message("u1", at: 1, role: "user", text: "Book the dentist"))
        try fed.send("message.created", Fx.message("u2", at: 2, role: "user", text: "And the weather?"))
        try fed.send("message.created", Fx.milestone("m1", at: 3, task: "t1", title: "Dentist", event: "started")
            .merging(["reply_to_message_id": "u1"]) { _, new in new })
        guard case .quest(let card) = try fed.items().first(where: { $0.id == "m1" }) else {
            return XCTFail("the quest card")
        }
        XCTAssertEqual(card.quote?.messageId, "u1")
    }

    func testTheWordsOfTheLine() {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "Europe/Brussels")!
        let locale = Locale(identifier: "en_GB")
        func date(_ day: Int, _ hour: Int, _ minute: Int) -> Date {
            calendar.date(from: DateComponents(year: 2026, month: 10, day: day, hour: hour, minute: minute))!
        }
        let now = date(13, 18, 0)
        func text(_ at: Date?) -> String { ReplyQuote(messageId: "u", date: at).text(now: now, calendar: calendar, locale: locale) }
        XCTAssertEqual(text(date(13, 8, 12)), "re: your 08:12 message")
        XCTAssertEqual(text(date(12, 21, 40)), "re: your message yesterday 21:40")
        XCTAssertTrue(text(date(5, 9, 0)).hasPrefix("re: your message "), text(date(5, 9, 0)))
        XCTAssertEqual(text(nil), "re: your earlier message")
    }

    func testAJumpScrollsToTheMessageAndOutlinesItForAMoment() async throws {
        let model = ChatModel(service: FakeService(), conversationId: Fx.conversation)
        model.jump(to: "u1")
        XCTAssertEqual(model.focus, "u1", "the feed scrolls to it")
        XCTAssertEqual(model.highlighted, "u1", "and outlines it")
        try await Task.sleep(for: ChatModel.highlightFor + .milliseconds(300))
        XCTAssertNil(model.highlighted, "the outline goes after a moment")
        XCTAssertEqual(model.focus, "u1", "the feed clears focus itself once it has scrolled")
    }

    // MARK: Mentions (B-42)

    func testAnAtOrHashStartsAMentionOnlyAtTheStartOfAWord() {
        XCTAssertEqual(MentionTrigger.find(in: "also run it on iPad @fi")?.query, "fi")
        XCTAssertEqual(MentionTrigger.find(in: "@fi")?.kinds, [.task, .artifact])
        XCTAssertEqual(MentionTrigger.find(in: "news for #Hou")?.kinds, [.project])
        XCTAssertEqual(MentionTrigger.find(in: "about #")?.query, "")
        XCTAssertNil(MentionTrigger.find(in: "mail a@b.be"), "an address is not a mention")
        XCTAssertNil(MentionTrigger.find(in: "no sign here"))
        XCTAssertNil(MentionTrigger.find(in: "@fi\nnext line"), "a finished line is not being typed")
    }

    func testTypingAtFiOffersQuestsPickingMakesAChipAndItIsSent() async throws {
        let hub = FakeService()
        let fix = Mention(kind: .task, id: "tsk_1", title: "Fix flaky login test")
        hub.mentionResults = [fix, Mention(kind: .project, id: "prj_1", title: "Fishing trip"),
                              Mention(kind: .artifact, id: "art_1", title: "Fix notes")]
        let model = ChatModel(service: hub, conversationId: Fx.conversation)

        model.draft = "also run it on iPad @fi"
        await model.draftChanged()
        XCTAssertEqual(hub.searches.last?.query, "fi")
        XCTAssertEqual(model.mentionSuggestions.map(\.title), ["Fix flaky login test", "Fix notes"],
                       "@ lists quests and items, not projects")

        model.pick(fix)
        XCTAssertEqual(model.draft, "also run it on iPad", "the @word leaves the text")
        XCTAssertEqual(model.mentions, [fix])
        XCTAssertNil(model.mentionTrigger)

        await model.send()
        XCTAssertEqual(hub.sent.last?.text, "also run it on iPad")
        XCTAssertEqual(hub.sentMentions.last, [fix], "sent with `mentions`")
        XCTAssertEqual(model.mentions, [], "the chips went with that message")
        guard case .user(let line) = ChatFeed.items(FeedInput(), local: model.local).last else {
            return XCTFail("your message shows")
        }
        XCTAssertEqual(line.mentions, [fix], "the sent message shows its chip")
        XCTAssertTrue(line.accessibilityText.contains("mentioning Fix flaky login test"))
    }

    func testAHashListsProjectsAndAChipCanBeTakenOff() async {
        let hub = FakeService()
        let house = Mention(kind: .project, id: "prj_1", title: "House")
        hub.mentionResults = [house, Mention(kind: .task, id: "tsk_1", title: "House plants")]
        let model = ChatModel(service: hub, conversationId: Fx.conversation)
        model.draft = "#hou"
        await model.draftChanged()
        XCTAssertEqual(model.mentionSuggestions, [house])
        model.pick(house)
        XCTAssertEqual(model.draft, "")
        model.unpick(house)
        XCTAssertEqual(model.mentions, [])
    }

    func testAStraySignStopsAskingOnceASpaceFollowsAndNothingMatches() async {
        let hub = FakeService()
        hub.mentionResults = [Mention(kind: .project, id: "prj_1", title: "Summer house")]
        let model = ChatModel(service: hub, conversationId: Fx.conversation)
        model.draft = "for #summer h"
        await model.draftChanged()
        XCTAssertEqual(model.mentionSuggestions.map(\.title), ["Summer house"], "a title with a space still matches")
        model.draft = "I paid #5 for"
        await model.draftChanged()
        XCTAssertNil(model.mentionTrigger, "no project matches '5 for': it is just text, the list closes")
        model.draft = "I paid #5"
        await model.draftChanged()
        XCTAssertNotNil(model.mentionTrigger, "without a space the list stays, saying nothing matches")
    }

    func testAHubOutOfReachIsSaidAsSuchNotAsNoMatch() async {
        let hub = FakeService()
        hub.mentionsFail = true
        let model = ChatModel(service: hub, conversationId: Fx.conversation)
        model.draft = "run it @fi"
        await model.draftChanged()
        XCTAssertTrue(model.mentionsUnreachable)
        XCTAssertNotNil(model.mentionTrigger, "the list stays open to say so")
        hub.mentionsFail = false
        hub.mentionResults = [Mention(kind: .task, id: "t", title: "Fix flaky login test")]
        model.draft = "run it @fix"
        await model.draftChanged()
        XCTAssertFalse(model.mentionsUnreachable)
    }

    func testAPickTakesOutTheWordAsTheDraftIsNow() async {
        let hub = FakeService()
        let fix = Mention(kind: .task, id: "tsk_1", title: "Fix flaky login test")
        hub.mentionResults = [fix]
        let model = ChatModel(service: hub, conversationId: Fx.conversation)
        model.draft = "please @fi"
        await model.draftChanged()
        model.draft = "please now @fix"
        model.pick(fix)
        XCTAssertEqual(model.draft, "please now", "the @word is found again, not cut where it used to be")
    }

    func testAStoredMessageShowsItsMentionsAsChips() throws {
        let fed = try FedCache()
        try fed.send("message.created", Fx.message("u1", at: 1, role: "user", text: "also run it on iPad",
                                                   extra: ["mentions": [["kind": "task", "id": "tsk_1",
                                                                         "title": "Fix flaky login test"]]]))
        guard case .user(let line) = try fed.items().last else { return XCTFail("your message") }
        XCTAssertEqual(line.mentions.map(\.label), ["@Fix flaky login test"])
    }

    // MARK: How it looks

    func testTheLineAndTheChipsLookLikeTheChat() {
        let now = Fx.base.addingTimeInterval(3600)
        Snapshot.assert(
            VStack(alignment: .leading, spacing: 12) {
                FeedStack(items: [
                    .user(UserLine(id: "u2", text: "also run it on iPad",
                                   mentions: [Mention(kind: .task, id: "t", title: "Fix flaky login test")])),
                    .marcel(MarcelLine(id: "r1", markdown: "Daikin is cheapest over **10 years**.",
                                       quote: ReplyQuote(messageId: "u1", date: Fx.base.addingTimeInterval(1800)))),
                ], actions: ChatActions())
                MentionSuggestions(trigger: MentionTrigger.find(in: "@fi")!,
                                   found: [Mention(kind: .task, id: "t", title: "Fix flaky login test"),
                                           Mention(kind: .artifact, id: "a", title: "Fix notes")], pick: { _ in })
                ComposerMentions(mentions: [Mention(kind: .project, id: "p", title: "House")], remove: { _ in })
            }
            .environment(\.chatNow, now)
            .environment(\.timeZone, TimeZone(identifier: "Europe/Brussels")!)
            .environment(\.locale, Locale(identifier: "en_GB")),
            named: "Quote-and-mentions", variants: [.light, .dark])
    }
}

/// "Tap jumps" (S-18.5's UI test, one layer down): the "re:" line in a real window is a button VoiceOver and a tap
/// reach, and pressing it asks for the message it answers.
@MainActor
final class ReplyQuoteTapTests: XCTestCase {
    override class func setUp() {
        super.setUp()
        let handle = dlopen("/usr/lib/libAccessibility.dylib", RTLD_NOW)
        typealias Enable = @convention(c) (Bool) -> Void
        if let symbol = dlsym(handle, "_AXSApplicationAccessibilitySetEnabled") {
            unsafeBitCast(symbol, to: Enable.self)(true)
        }
    }

    private var windows: [UIWindow] = []

    override func tearDown() {
        windows.forEach { $0.isHidden = true }
        windows = []
        super.tearDown()
    }

    private func elements<V: View>(of view: V) -> [NSObject] {
        let host = UIHostingController(rootView: view)
        let window = UIWindow(frame: CGRect(x: 0, y: 0, width: 390, height: 1200))
        window.rootViewController = host
        window.makeKeyAndVisible()
        windows.append(window)
        host.view.layoutIfNeeded()
        RunLoop.main.run(until: Date().addingTimeInterval(0.3))
        var found: [NSObject] = []
        func walk(_ object: NSObject, depth: Int) {
            guard depth < 25 else { return }
            if object.isAccessibilityElement { found.append(object) }
            if let children = object.accessibilityElements as? [NSObject], !children.isEmpty {
                children.forEach { walk($0, depth: depth + 1) }
            } else if object.accessibilityElementCount() != NSNotFound, object.accessibilityElementCount() > 0 {
                for index in 0..<object.accessibilityElementCount() {
                    if let child = object.accessibilityElement(at: index) as? NSObject { walk(child, depth: depth + 1) }
                }
            } else if let view = object as? UIView {
                view.subviews.forEach { walk($0, depth: depth + 1) }
            }
        }
        walk(host.view, depth: 0)
        return found
    }

    func testPressingTheLineJumpsToTheMessageItAnswers() throws {
        var jumped: [String] = []
        var actions = ChatActions()
        actions.jump = { jumped.append($0) }
        let found = elements(of: FeedStack(items: [
            .user(UserLine(id: "u1", text: "Compare the heat-pump offers")),
            .user(UserLine(id: "u2", text: "What's on Thursday?")),
            .marcel(MarcelLine(id: "r1", markdown: "Daikin is cheapest.",
                               quote: ReplyQuote(messageId: "u1", date: nil))),
        ], actions: actions).padding(16))
        let line = try XCTUnwrap(found.first { $0.accessibilityLabel == "Answers your earlier message" },
                                 "the line is one button")
        XCTAssertTrue(line.accessibilityTraits.contains(.button))
        XCTAssertTrue(line.accessibilityActivate(), "the line can be pressed")
        XCTAssertEqual(jumped, ["u1"], "pressing it jumps to the message it answers")
    }
}
