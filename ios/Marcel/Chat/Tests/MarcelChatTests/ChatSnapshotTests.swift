import MarcelKit
import SwiftUI
import XCTest

@testable import MarcelChat

/// The chat screen's main states in light and dark (and the plain feed at the largest text size), compared with
/// `__Snapshots__/` pixel for pixel. Re-record with `make -C ios chat-record` after a deliberate change and look at
/// the PNGs before committing them.
@MainActor
final class ChatSnapshotTests: XCTestCase {
    private let twoModes: [Snapshot.Variant] = [.light, .dark]

    func testConversation() {
        Snapshot.assert(
            ChatStill(header: ChatHeaderState(), items: [
                .marcel(MarcelLine(id: "m1", body: ChatMarkdown.render(
                    "Good morning. Today: swim class **16:30**, paper bins tonight."))),
                .user(UserLine(id: "u1", text: "What's the weather Sunday?")),
                .marcel(MarcelLine(id: "m2", body: ChatMarkdown.render(
                    "18° and dry. Good for the garden lunch option. See [the forecast](https://example.com)."))),
            ]),
            named: "Chat-conversation", padding: 0)
    }

    // S-12.7: a message sent from a project room carries that project's chip.
    func testAMessageFromAProjectRoom() {
        Snapshot.assert(
            ChatStill(header: ChatHeaderState(), items: [
                .user(UserLine(id: "u1", text: "Did the second installer reply?", project: "Home energy",
                               tone: .teal)),
            ], collapsed: true),
            named: "Chat-project-tag", variants: twoModes, padding: 0)
    }

    // S-12.11: a renamed agent in the header, a question sent with an item, and the next item above the composer.
    func testAskingAboutAnItemWithARenamedAgent() {
        Snapshot.assert(
            ChatStill(header: ChatHeaderState(name: "Marcella"), items: [
                .user(UserLine(id: "u1", text: "Is Engie cheaper with solar?", project: "Home energy", tone: .teal,
                               attachment: "Energy offers compared")),
                .marcel(MarcelLine(id: "m1", body: ChatMarkdown.render("Yes: **€486** a year less with 4 kWp."))),
            ], draft: "And without the heat pump?", attachment: "Bills 2026"),
            named: "Chat-ask", variants: twoModes, padding: 0)
    }

    func testCollapsedHeaderOffline() {
        Snapshot.assert(
            ChatStill(header: ChatHeaderState(offline: "Offline · showing what's saved"), items: [
                .user(UserLine(id: "u1", text: "Where's the dentist thing?")),
            ], collapsed: true),
            named: "Chat-offline", variants: twoModes, padding: 0)
    }

    // MARK: Cards (wireframes 1A, 1B, 1C, 3B, 4A, 6A)

    private let energy = QuestLine(id: "q1", taskId: "t1", title: "Energy contracts", state: .working,
                                   project: "Home energy", tone: .teal, detail: "On the NUC")
    private let lunch = QuestLine(id: "q2", taskId: "t2", title: "Sunday lunch", state: .working,
                                  project: "Family", tone: .rose, detail: "In the cloud")

    private func approval(_ status: ApprovalLine.Status, details: Bool = false) -> FeedItem {
        .approval(ApprovalLine(
            id: "a1", approvalId: "apr_1", taskId: "t1",
            question: "Energy contracts asks: Supplier A is cheapest, €486 a year less with solar. Switch now?",
            detail: "Send the switch request to switch@supplier-a.be from 1 November.", status: status,
            showsDetail: details))
    }

    func testThreadsStarted() {
        Snapshot.assert(
            ChatStill(header: ChatHeaderState(avatar: .working(2), running: 2), items: [
                .user(UserLine(id: "u1", text: "Compare energy contracts and plan Sunday lunch")),
                .marcel(MarcelLine(id: "m1", body: ChatMarkdown.render(
                    "Started two quests. I'll come back when they need you."))),
                .quest(energy), .quest(lunch),
            ]),
            named: "Chat-threads", variants: twoModes, padding: 0)
    }

    func testFollowUpFindsItsThread() {
        Snapshot.assert(
            ChatStill(header: ChatHeaderState(avatar: .working(2), running: 2), items: [
                .user(UserLine(id: "u1", text: "Also check whether solar panels change the picture")),
                .marcel(MarcelLine(id: "m1", body: ChatMarkdown.render(
                    "Added to **Energy contracts**. It will model with and without solar."), quest: energy)),
                .user(UserLine(id: "u2", text: "What's the weather Sunday?")),
                .marcel(MarcelLine(id: "m2", body: ChatMarkdown.render("18° and dry. Good for the garden lunch."))),
            ]),
            named: "Chat-followup", variants: twoModes, padding: 0)
    }

    func testApprovalWaiting() {
        var waiting = energy
        waiting.state = .needsYou
        waiting.detail = "Your OK to switch"
        Snapshot.assert(
            ChatStill(header: ChatHeaderState(avatar: .needsYou, running: 1, needsYou: 1), items: [
                .quest(waiting), approval(.open, details: true),
            ]),
            named: "Chat-approval", padding: 0)
    }

    func testApprovalAnswered() {
        Snapshot.assert(
            ChatStill(header: ChatHeaderState(avatar: .working(1), running: 1), items: [
                approval(.approved(viaTerminal: false)),
            ]),
            named: "Chat-approval-answered", variants: twoModes, padding: 0)
    }

    func testItemGet() {
        var done = energy
        done.state = .done
        done.detail = "Finished · 1 item"
        Snapshot.assert(
            ChatStill(header: ChatHeaderState(avatar: .idle), items: [
                .item(ItemLine(id: "i1", artifactId: "art_1", title: "Energy comparison", savedTo: "Home energy",
                               badge: "New", rows: [
                                   PreviewRow("Supplier A + solar", value: "€1,010", emphasised: true),
                                   PreviewRow("Supplier A", value: "€1,284"), PreviewRow("Current", value: "€1,496"),
                               ], showsBanner: true, isDigest: false, url: nil)),
                .marcel(MarcelLine(id: "m1", body: ChatMarkdown.render(
                    "Done: Energy contracts. Switched to Supplier A from 1 November."))),
            ]),
            named: "Chat-item", padding: 0)
    }

    func testMorningDigest() {
        Snapshot.assert(
            ChatStill(header: ChatHeaderState(avatar: .idle, needsYou: 2), items: [
                .marcel(MarcelLine(id: "m1", body: ChatMarkdown.render(
                    "Good morning. Today: swim class 16:30, paper bins tonight. Two things wait for you."))),
                .item(ItemLine(id: "i1", artifactId: "art_b", title: "Morning board", savedTo: nil,
                               badge: "Board · v7", rows: [
                                   PreviewRow("Approve dentist Tue 9:10"), PreviewRow("Sunday lunch: pick 1 of 2"),
                                   PreviewRow("Threads finished overnight", value: "3", emphasised: true),
                               ], showsBanner: false, isDigest: true, url: nil)),
                .user(UserLine(id: "u1", text: "Compare energy contracts and plan Sunday lunch")),
            ]),
            named: "Chat-digest", variants: twoModes, padding: 0)
    }

    // MARK: Composer

    func testSendingAndNotSent() {
        Snapshot.assert(
            ChatStill(header: ChatHeaderState(), items: [
                .user(UserLine(id: "u1", text: "Book the dentist for Tuesday", status: .failed, clientId: "c1")),
                .user(UserLine(id: "u2", text: "What's the weather Sunday?", status: .sending, clientId: "c2")),
            ], collapsed: true, draft: "Also check solar"),
            named: "Chat-composer", variants: twoModes, padding: 0)
    }
}
