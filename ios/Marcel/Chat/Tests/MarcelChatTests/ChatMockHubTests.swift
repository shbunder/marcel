import Foundation
import MarcelKit
import XCTest

@testable import MarcelChat

/// The story's scenario against the real mock hub, through the same model, cache and live stream the screen uses
/// (XCUITest cannot run on this Mac's Xcode; see ios/README.md). Skipped unless `MARCEL_MOCK_HUB` holds the
/// hub's server URL: start the hub with `MOCK_DELAY=0 MOCK_HUB_PORT=7488 make mock-hub`, then
/// `MOCK_HUB=http://127.0.0.1:7488/api make -C ios chat-integration`. Not part of the gate.
///
/// The mock runs one scripted thread at a time, so the two started cards come from two runs: the login run
/// (follow-up, approval, approve, the PR as an item) and then the energy run (a living document: "You got" on
/// version 1 only, version 2 updates the card).
@MainActor
final class ChatMockHubTests: XCTestCase {
    final class FixedToken: TokenStore, @unchecked Sendable {
        func token() throws -> String? { "chat-integration-test" }
        func save(_ token: String) throws {}
        func delete() throws {}
    }

    private func waitUntil(_ what: String, seconds: Double = 20, _ condition: () throws -> Bool) async throws {
        let deadline = Date().addingTimeInterval(seconds)
        while try !condition() {
            if Date() > deadline {
                XCTFail("timed out waiting for \(what)")
                throw CancellationError()
            }
            try await Task.sleep(for: .milliseconds(100))
        }
    }

    func testSendTwoCardsFollowUpApproveAndItemGet() async throws {
        guard let hub = ProcessInfo.processInfo.environment["MARCEL_MOCK_HUB"], let serverURL = URL(string: hub) else {
            throw XCTSkip("Set MARCEL_MOCK_HUB (make -C ios chat-integration) to run against the mock hub.")
        }
        let tokens = FixedToken()
        let cache = MarcelCache(container: try MarcelCache.makeContainer(inMemory: true))
        let stream = EventStream(connector: URLSessionEventSocketConnector(serverURL: serverURL, tokenStore: tokens),
                                 sink: cache)
        let streaming = Task { await stream.run() }
        defer { streaming.cancel() }
        let model = ChatModel(service: LiveChatService(client: HubClient.make(serverURL: serverURL, tokenStore: tokens)))
        await model.start()
        // A run left waiting by an earlier test keeps the mock busy (one run at a time): say no to it first.
        let service = LiveChatService(client: HubClient.make(serverURL: serverURL, tokenStore: tokens))
        for approval in try await service.approvals() where approval.state == .open {
            _ = try await service.answer(approvalId: approval.id, approve: false)
            try await Task.sleep(for: .seconds(2))
        }
        let conversation = try XCTUnwrap(model.conversationId)
        try await waitUntil("the stream to go live") { stream.status == .live }

        func input() throws -> FeedInput {
            try FeedInput.read(from: cache.container.mainContext, conversationId: conversation, limit: 500)
        }
        func items() throws -> [FeedItem] {
            let input = try input()
            model.prune(input)
            return ChatFeed.items(input, local: model.local)
        }
        func quests() throws -> [QuestLine] { try items().compactMap { if case .quest(let q) = $0 { q } else { nil } } }
        func items(_ title: String) throws -> [ItemLine] {
            try items().compactMap { if case .item(let i) = $0, i.title == title { i } else { nil } }
        }
        func openQuestion() async throws -> ApprovalLine? {
            await model.reconcile(try input())
            return try items().compactMap { if case .approval(let a) = $0, a.status.isOpen { a } else { nil } }.last
        }
        let cardsBefore = try quests().count

        // Send → the first started card.
        model.draft = "Can you fix the flaky login test? (chat integration \(UUID().uuidString.prefix(6)))"
        await model.send()
        XCTAssertNil(model.problem)
        try await waitUntil("the login thread's card") { try quests().count == cardsBefore + 1 }
        let login = try XCTUnwrap(quests().last)

        // The approval choice → approve → the PR arrives as an item, the card ends in place.
        var question: ApprovalLine?
        try await waitUntil("the login thread's question") {
            question = try items().compactMap { if case .approval(let a) = $0, a.taskId == login.taskId { a } else { nil } }.last
            return question != nil
        }
        await model.reconcile(try input())
        let open = try await openQuestion()
        let ask = try XCTUnwrap(open)
        await model.answer(approvalId: ask.approvalId, approve: true)
        XCTAssertNil(model.problem)
        try await waitUntil("the PR item") { try !items("Fix flaky login test: wait on the session cookie").isEmpty }
        XCTAssertEqual(try items("Fix flaky login test: wait on the session cookie").first?.showsBanner, true)
        try await waitUntil("the login card to show done") { try quests().first { $0.taskId == login.taskId }?.state == .done }
        XCTAssertEqual(try quests().filter { $0.taskId == login.taskId }.count, 1, "the card updated in place")
        await model.reconcile(try input())
        let answered = try items().compactMap { if case .approval(let a) = $0, a.approvalId == ask.approvalId { a } else { nil } }.first
        XCTAssertEqual(answered?.status, .approved(viaTerminal: false))

        // Once the mock is free: the energy run → the second started card, its item, and version 2.
        try await waitUntil("the mock to finish the login run") {
            try items().contains { if case .marcel(let m) = $0 { m.body.text.hasPrefix("Done: Fix the flaky login test") } else { false } }
        }
        try await Task.sleep(for: .milliseconds(500))
        model.draft = "Compare the energy offers for us"
        await model.send()
        try await waitUntil("the energy thread's card") { try quests().count == cardsBefore + 2 }
        let energy = try XCTUnwrap(quests().last)

        // A follow-up about the energy thread while it runs. The mock stores it and starts nothing (it has no
        // brain to route it and reply), so what can be checked here: it shows once, from the hub's copy, and
        // the energy card stays the newest card with no new one. Marcel's routed reply (a message with the
        // thread's task_id, drawn with that thread's live card) is checked in the gate's scenario test.
        model.draft = "Also check whether solar panels change the picture."
        await model.send()
        try await waitUntil("the follow-up to come back from the hub") {
            try items().contains { if case .user(let u) = $0 { u.text.hasPrefix("Also check whether solar") && u.id.hasPrefix("msg") } else { false } }
        }
        XCTAssertEqual(try items().filter { if case .user(let u) = $0 { u.text.hasPrefix("Also check whether solar") } else { false } }.count, 1)
        XCTAssertEqual(try quests().count, cardsBefore + 2, "the follow-up starts no new card")
        XCTAssertEqual(try quests().last?.taskId, energy.taskId)
        try await waitUntil("the energy item") { try !items("Switching to Luminus").isEmpty }
        XCTAssertEqual(try items("Switching to Luminus").first?.badge, "New")
        var energyAsk: ApprovalLine?
        try await waitUntil("the energy question") {
            energyAsk = try items().compactMap { if case .approval(let a) = $0, a.taskId == energy.taskId { a } else { nil } }.last
            return energyAsk != nil
        }
        await model.reconcile(try input())
        let energyQuestion = try XCTUnwrap(try items().compactMap {
            if case .approval(let a) = $0, a.approvalId == energyAsk?.approvalId { a } else { nil }
        }.first)
        XCTAssertEqual(energyQuestion.status, .open)
        XCTAssertTrue(energyQuestion.question.contains("Send the switch request to Luminus"), energyQuestion.question)
        await model.answer(approvalId: energyQuestion.approvalId, approve: true)
        try await waitUntil("version 2 of the item") { try items("Switching to Luminus").first?.badge == "Updated · v2" }
        XCTAssertEqual(try items("Switching to Luminus").count, 1)
        XCTAssertEqual(try items("Switching to Luminus").filter(\.showsBanner).count, 1, "You got only once")
    }
}
