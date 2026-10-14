import MarcelKit
import MarcelLook
import PixelKit
import SwiftUI
import UIKit
import XCTest

@testable import MarcelChat

/// S-18.3 (ADR-007): each piece of the chat's look in light and dark, at the default and the largest text size.
/// Re-record with `make -C ios chat-record` after a deliberate change and look at the PNGs before committing them.
@MainActor
final class ChatLookSnapshotTests: XCTestCase {
    private let all = Snapshot.Variant.allCases

    func testYourMessageKeepsItsNotch() {
        Snapshot.assert(
            VStack(spacing: 12) {
                UserBubble(text: "What's on Thursday?")
                UserBubble(text: "Compare the heat-pump offers in my Downloads and tell me which is cheapest over ten years.")
            },
            named: "Look-your-message", variants: all, scale: 3)
    }

    func testYourMessageInAHelpersColour() {
        Snapshot.assert(
            VStack(spacing: 12) {
                UserBubble(text: "In Pip's tangerine").environment(\.chatBubbleColor, PixelColor(0xF5953F))
                UserBubble(text: "In ink, lightened to stay readable").environment(\.chatBubbleColor, PixelColor(0x5A5A78))
            },
            named: "Look-your-message-colours", variants: [.light, .dark])
    }

    func testAReplyWithAHeadingAListATableAndCode() {
        Snapshot.assert(
            MarcelReply(line: MarcelLine(id: "m1", markdown: """
                ## Short version

                | Offer | Price | 10 yrs |
                |---|---|---|
                | Daikin | €9.8k | **€14.1k** |
                | Vaillant | €8.9k | €15.0k |

                - Daikin is *cheapest* over 10 years.
                - Vaillant can install in May. See [the offers](https://example.com/offers).

                ```
                quotes/daikin-2026.pdf
                ```
                """), actions: ChatActions()),
            named: "Look-reply", variants: all)
    }

    private let energy = QuestLine(id: "q1", taskId: "t1", title: "Compare heat-pump offers", state: .working,
                                   project: "House", tone: .teal, detail: "Reading 3 offers", location: .nuc)

    func testStartedCard() {
        Snapshot.assert(QuestRow(line: energy, actions: ChatActions()), named: "Look-card-started", variants: all)
    }

    func testNeedsYouCard() {
        Snapshot.assert(
            ApprovalRow(line: ApprovalLine(id: "a1", approvalId: "apr_1", taskId: "t1",
                                           question: "Compare heat-pump offers asks: May I read 3 PDFs in ~/Downloads?",
                                           detail: "Reads offer-daikin.pdf, offer-vaillant.pdf, offer-nibe.pdf.",
                                           status: .open, showsDetail: false),
                        actions: ChatActions()),
            named: "Look-card-needs-you", variants: all)
    }

    func testDoneCard() {
        Snapshot.assert(
            ItemRow(line: ItemLine(id: "i1", artifactId: "art_1", title: "Heat-pump comparison", savedTo: "House",
                                   badge: "New", rows: [PreviewRow("Report · 3 offers"),
                                                        PreviewRow("Cheapest over 10 years", value: "Daikin")],
                                   showsBanner: true, isDigest: false, url: nil),
                    actions: ChatActions()),
            named: "Look-card-done", variants: all)
    }

    func testFailedCard() {
        var failed = energy
        failed.state = .failed
        failed.detail = "The NAS did not answer. Marcel will try again tonight."
        Snapshot.assert(QuestRow(line: failed, actions: ChatActions()), named: "Look-card-failed", variants: all)
    }

    func testThinkingRow() {
        Snapshot.assert(ThinkingRow(state: .working(2), name: "Marcel"), named: "Look-thinking", variants: all)
    }

    /// The whole chat, as the wireframe's Q1: a time line, your bubbles, a reply, the four cards and the thinking row.
    func testTheChatAsAWhole() {
        let now = Date(timeIntervalSince1970: 1_791_900_000)
        Snapshot.assert(
            ChatStill(header: ChatHeaderState(avatar: .working(2), running: 2, needsYou: 1), items: [
                .time(TimeLine(id: "t1", date: now.addingTimeInterval(-3600))),
                .user(UserLine(id: "u1", text: "What's on Thursday?")),
                .marcel(MarcelLine(id: "m1", markdown: "Dentist at **9:30**, then nothing until school pickup at 15:20.")),
                .user(UserLine(id: "u2", text: "Compare the heat-pump offers in my Downloads")),
                .quest(energy),
                .approval(ApprovalLine(id: "a1", approvalId: "apr_1", taskId: "t1",
                                       question: "May I read 3 PDFs in ~/Downloads?", detail: nil, status: .open,
                                       showsDetail: false)),
            ])
            .environment(\.chatNow, now)
            .environment(\.timeZone, TimeZone(identifier: "Europe/Brussels")!)
            .environment(\.locale, Locale(identifier: "en_GB")),
            named: "Look-chat", variants: [.light, .dark], padding: 0)
    }
}

/// Things about the look a picture alone would not prove.
@MainActor
final class ChatLookTests: XCTestCase {
    /// The notch: the bottom-right corner is squared off (4 pt), the other three are round (16 pt).
    func testTheBubblesBottomRightCornerIsTheNotch() {
        let rect = CGRect(x: 0, y: 0, width: 200, height: 60)
        let path = ChatBubbleShape().path(in: rect)
        // 2 pt in from each corner along the diagonal: inside only where the corner radius is small.
        XCTAssertTrue(path.contains(CGPoint(x: rect.maxX - 2, y: rect.maxY - 2)), "bottom right is square-ish (4 pt)")
        XCTAssertFalse(path.contains(CGPoint(x: rect.maxX - 2, y: rect.minY + 2)), "top right is round (16 pt)")
        XCTAssertFalse(path.contains(CGPoint(x: rect.minX + 2, y: rect.minY + 2)), "top left is round")
        XCTAssertFalse(path.contains(CGPoint(x: rect.minX + 2, y: rect.maxY - 2)), "bottom left is round")
        XCTAssertEqual(ChatBubbleShape.radius, 16)
        XCTAssertEqual(ChatBubbleShape.notch, 4)
    }

    /// Every text the chat draws reaches 4.5:1, in both themes, and so does your text on every helper's colour.
    func testEveryChatColourPairIsReadable() {
        for palette in [ChatPalette.light, ChatPalette.dark] {
            for pair in palette.textPairs {
                XCTAssertGreaterThanOrEqual(pair.text.contrast(with: pair.on), 4.5,
                                            "\(palette == .dark ? "dark" : "light"): \(pair.name)")
            }
            for animal in LookAnimal.allCases {
                for color in LookColor.allCases {
                    let fill = palette.bubble(LookColors.bubble(KnownLook(animal: animal, color: color, personality: .cozy)))
                    XCTAssertGreaterThanOrEqual(palette.youInk.contrast(with: fill), 4.5, "\(animal) \(color)")
                }
            }
        }
        let rose = LookColors.bubble(KnownLook(animal: .giraffe, color: .natural, personality: .professional))
        XCTAssertEqual(rose, PixelColor(0xF08AA0), "Marcel the rose giraffe gives rose")
        XCTAssertEqual(ChatPalette.dark.bubble(rose), ChatPalette.dark.you, "and the wireframe's dark rose in dark mode")
    }

    func testCardColoursFollowTheTask() {
        XCTAssertEqual(QuestRow.kind(.working), .started)
        XCTAssertEqual(QuestRow.kind(.needsYou), .needsYou)
        XCTAssertEqual(QuestRow.kind(.done), .done)
        XCTAssertEqual(QuestRow.kind(.failed), .failed)
        XCTAssertEqual(QuestRow.label(.working), "Started")
        XCTAssertEqual(QuestRow.open(.failed), "See why ›")
    }

    func testTheThinkingRowShowsOnlyWhileBusy() {
        XCTAssertTrue(ThinkingRow.shows(.thinking))
        XCTAssertTrue(ThinkingRow.shows(.working(1)))
        XCTAssertFalse(ThinkingRow.shows(.idle))
        XCTAssertFalse(ThinkingRow.shows(.needsYou))
        XCTAssertFalse(ThinkingRow.shows(.done))
    }
}

/// The story's UI test, one layer down (XCUITest cannot run on this Mac's Xcode, see ios/README.md): the chat is put
/// in a real window, and the accessibility tree SwiftUI builds is searched for the "Reply in thread" action that a
/// long-press shows. It is on a reply and on a special card, and it opens the side chat on that message.
@MainActor
final class LongPressTests: XCTestCase {
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
        let window = UIWindow(frame: CGRect(x: 0, y: 0, width: 390, height: 3000))
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

    private func replyInThread(_ element: NSObject) -> UIAccessibilityCustomAction? {
        element.accessibilityCustomActions?.first { $0.name == "Reply in thread" }
    }

    func testLongPressOnAReplyAndOnASpecialCardOpensTheSideChat() throws {
        var opened: [String] = []
        var actions = ChatActions()
        actions.reply = { opened.append($0.id) }
        let quest = QuestLine(id: "q1", taskId: "t1", title: "Compare heat-pump offers", state: .working,
                              project: "House", tone: .teal, detail: "Reading 3 offers", location: .nuc)
        let found = elements(of: VStack(alignment: .leading, spacing: 12) {
            FeedStack(items: [
                .marcel(MarcelLine(id: "m1", markdown: "Dentist at **9:30**. See [the calendar](https://example.com/cal).")),
                .quest(quest),
            ], actions: actions)
        }.padding(16))

        let reply = try XCTUnwrap(found.first { ($0.accessibilityLabel ?? "").contains("Dentist at 9:30") },
                                  "the reply is one element")
        let card = try XCTUnwrap(found.first { ($0.accessibilityLabel ?? "").hasPrefix("Started: Compare heat-pump") },
                                 "the card is one element")
        // VoiceOver reaches the reply's link as a named action on the same element.
        XCTAssertNotNil(reply.accessibilityCustomActions?.first { $0.name == "Open the calendar" })
        for (element, id) in [(reply, "m1"), (card, "q1")] {
            let action = try XCTUnwrap(replyInThread(element), "\(id) has Reply in thread")
            _ = action.actionHandler?(action)
            XCTAssertEqual(opened.last, id)
        }
        XCTAssertEqual(opened, ["m1", "q1"])
    }
}

extension Array where Element == FeedItem {
    /// The rows' ids without the time lines between groups (S-18.3), for tests about which messages show.
    var rowIds: [String] {
        compactMap { if case .time = $0 { nil } else { $0.id } }
    }
}
