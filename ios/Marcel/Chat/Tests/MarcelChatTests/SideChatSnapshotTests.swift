import MarcelKit
import SwiftUI
import XCTest

@testable import MarcelChat

/// The side chat (S-12.4, wireframe 2B) and "n replies" in the main chat, in light and dark (and the thread at the
/// largest text size), compared pixel for pixel like the chat's other snapshots (`make -C ios chat-record`).
@MainActor
final class SideChatSnapshotTests: XCTestCase {
    private let twoModes: [Snapshot.Variant] = [.light, .dark]

    private let parent = SideChatParent(id: "m1", speaker: "Marcel",
                                        text: "18° and dry. Good for the garden lunch option.")

    private var thread: [FeedItem] {
        [
            .user(UserLine(id: "s1", text: "And if it rains after three?")),
            .marcel(MarcelLine(id: "s2", markdown: 
                "Then the covered terrace: same plan, ten metres closer to the kitchen.")),
        ]
    }

    func testSideChat() {
        let look = SideChatLook(detail: "Sees the main chat", placeholder: "Reply in side chat…", canSendToMain: true)
        Snapshot.assert(SideChatStill(parent: parent, look: look, items: thread),
                        named: "SideChat-thread", padding: 0)
    }

    func testSentToMainWithAReplyOnItsWay() {
        let look = SideChatLook(detail: "Sees the main chat", placeholder: "Reply in side chat…", canSendToMain: true,
                                sentToMain: true)
        Snapshot.assert(
            SideChatStill(parent: parent, look: look, items: thread + [
                .user(UserLine(id: "p1", text: "Book the terrace", status: .failed, clientId: "c1")),
            ], draft: "And for six people?"),
            named: "SideChat-sent", variants: twoModes, padding: 0)
    }

    func testAStoppedTaskThread() {
        let look = SideChatLook(detail: "Replies go to Energy contracts", placeholder: "Talk to this quest…",
                                taskId: "t1", stopped: true, canSendToMain: true)
        Snapshot.assert(
            SideChatStill(
                parent: SideChatParent(id: "m1", speaker: "Energy contracts", text: "Stopped", taskId: "t1"),
                look: look, items: [
                    .user(UserLine(id: "p1", text: "Use 4 kWp, the roof is small", status: .failed, clientId: "c1")),
                ]),
            named: "SideChat-task-stopped", variants: [.light, .dark, .largestText], padding: 0)
    }

    func testRepliesUnderAMessage() {
        var actions = ChatActions()
        actions.reply = { _ in }
        actions.threads = [
            "m1": ThreadSummary(conversationId: "cnv_side_m1", replies: 2),
            "u1": ThreadSummary(conversationId: "cnv_side_u1", replies: 1),
        ]
        Snapshot.assert(
            ChatStill(header: ChatHeaderState(), items: [
                .user(UserLine(id: "u1", text: "What's the weather Sunday?")),
                .marcel(MarcelLine(id: "m1", markdown: 
                    "18° and dry. Good for the garden lunch option.")),
            ], actions: actions),
            named: "Chat-replies", variants: [.light, .dark, .largestText], padding: 0)
    }
}
