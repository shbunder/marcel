import MarcelKit
import PixelKit
import XCTest

@testable import MarcelChat

/// The cards in the feed: quest cards that follow their task, approvals, items and the morning board.
@MainActor
final class CardTests: XCTestCase {
    private func quests(_ items: [FeedItem]) -> [QuestLine] {
        items.compactMap { if case .quest(let line) = $0 { line } else { nil } }
    }

    private func approvals(_ items: [FeedItem]) -> [ApprovalLine] {
        items.compactMap { if case .approval(let line) = $0 { line } else { nil } }
    }

    private func itemLines(_ items: [FeedItem]) -> [ItemLine] {
        items.compactMap { if case .item(let line) = $0 { line } else { nil } }
    }

    /// The story's scenario, driven through the cache and the model the way the screen drives them: send → two
    /// started cards → a follow-up routed to a card → the approval choice → approve → item get + artifact card.
    func testTheScenarioFromSendToItemGet() async throws {
        let fed = try FedCache()
        let hub = FakeService()
        let model = ChatModel(service: hub, conversationId: Fx.conversation)
        func items() throws -> [FeedItem] { ChatFeed.items(try fed.input(), local: model.local) }

        try fed.send("project.created", Fx.project("prj_energy", name: "Home energy"))
        try fed.send("project.created", Fx.project("prj_family", name: "Family"))
        try fed.send("message.created", Fx.message("msg_1", at: 1, role: "user",
                                                   text: "Compare energy contracts and plan Sunday lunch"))
        try fed.send("message.created", Fx.message("msg_2", at: 2, text: "Started two quests."))
        try fed.send("task.created", Fx.task("tsk_energy", title: "Energy contracts", state: "working", at: 3))
        try fed.send("message.created", Fx.milestone("msg_3", at: 3, task: "tsk_energy", title: "Energy contracts",
                                                     event: "started"))
        try fed.send("task.created", Fx.task("tsk_lunch", title: "Sunday lunch", state: "working",
                                             project: "prj_family", location: "cloud", at: 4))
        try fed.send("message.created", Fx.milestone("msg_4", at: 4, task: "tsk_lunch", title: "Sunday lunch",
                                                     event: "started"))

        var cards = quests(try items())
        XCTAssertEqual(cards.map(\.title), ["Energy contracts", "Sunday lunch"])
        XCTAssertEqual(cards.map(\.state), [.working, .working])
        XCTAssertEqual(cards.map(\.project), ["Home energy", "Family"])
        XCTAssertEqual(cards.map(\.detail), ["On the NUC", "In the cloud"])

        // A follow-up: Marcel routes it to the energy thread and says so; the thread's card shows under the reply.
        try fed.send("message.created", Fx.message("msg_5", at: 5, role: "user", text: "Also check solar panels"))
        try fed.send("message.created", Fx.message("msg_6", at: 6, text: "Added to Energy contracts.",
                                                   extra: ["task_id": "tsk_energy"]))
        guard case .marcel(let reply) = try items().last else { return XCTFail("Marcel's reply is last") }
        XCTAssertEqual(reply.quest?.taskId, "tsk_energy")
        XCTAssertEqual(quests(try items()).count, 2, "a routed follow-up starts no new card")

        // The thread asks. The approval reaches the app over REST; the task's card turns to "Your OK".
        hub.approvalsOnHub["apr_1"] = Fx.decode(Fx.approval(
            "apr_1", task: "tsk_energy", summary: "Send the switch request to **Luminus**",
            detail: "To: switch@luminus.be"))
        try fed.send("message.created", Fx.approvalMessage("msg_7", at: 7, approval: "apr_1", task: "tsk_energy"))
        try fed.send("task.updated", Fx.task("tsk_energy", title: "Energy contracts", state: "needs_you", at: 7,
                                             extra: ["approval_ids": ["apr_1"], "waiting_for": "Your OK to send it."]))
        XCTAssertEqual(approvals(try items()).first?.status, .loading)
        await model.reconcile(try fed.input())
        var question = try XCTUnwrap(approvals(try items()).first)
        XCTAssertEqual(question.status, .open)
        XCTAssertEqual(question.question, "Energy contracts asks: Send the switch request to Luminus")
        XCTAssertEqual(quests(try items()).first?.state, .needsYou)
        XCTAssertEqual(quests(try items()).first?.detail, "Your OK to send it.")
        XCTAssertEqual(ChatHeaderState(tasks: try fed.input().tasks.values).needsYou, 1)

        model.toggleDetails(approvalId: "apr_1")
        XCTAssertTrue(try XCTUnwrap(approvals(try items()).first).showsDetail)

        // Approve.
        await model.answer(approvalId: "apr_1", approve: true)
        XCTAssertEqual(hub.answers.map(\.id), ["apr_1"])
        XCTAssertEqual(hub.answers.map(\.approve), [true])
        question = try XCTUnwrap(approvals(try items()).first)
        XCTAssertEqual(question.status, .approved(viaTerminal: false))
        XCTAssertEqual(question.outcome, "You approved this.")

        // The thread works on, makes an item and finishes.
        try fed.send("task.updated", Fx.task("tsk_energy", title: "Energy contracts", state: "working", at: 8))
        try fed.send("artifact.created", Fx.artifact("art_1", title: "Energy comparison", task: "tsk_energy"))
        try fed.send("message.created", Fx.artifactMessage("msg_8", at: 8, artifact: "art_1", project: "prj_energy",
                                                           task: "tsk_energy"))
        try fed.send("task.updated", Fx.task("tsk_energy", title: "Energy contracts", state: "done", at: 9,
                                             extra: ["artifact_count": 1]))
        try fed.send("message.created", Fx.milestone("msg_9", at: 9, task: "tsk_energy", title: "Energy contracts",
                                                     event: "done", summary: "Supplier A from 1 November."))

        let final = try items()
        let item = try XCTUnwrap(itemLines(final).first)
        XCTAssertTrue(item.showsBanner)
        XCTAssertEqual(item.title, "Energy comparison")
        XCTAssertEqual(item.savedTo, "Home energy")
        XCTAssertEqual(item.badge, "New")
        guard case .marcel(let done) = final.last else { return XCTFail("the done line is last") }
        XCTAssertEqual(done.body.text, "Done: Energy contracts. Supplier A from 1 November.")
        cards = quests(final)
        XCTAssertEqual(cards.map(\.state), [.done, .working], "the energy card ended in place")
        XCTAssertEqual(cards.first?.detail, "Finished · 1 item")
        XCTAssertEqual(final.count, 9, "one row per message (nine sent), nothing doubled")
    }

    func testACardOnScreenUpdatesWhenItsTaskChangesWithoutANewRow() throws {
        let fed = try FedCache()
        try fed.send("task.created", Fx.task("tsk_1", title: "Fix the flaky login test", state: "starting"))
        try fed.send("message.created", Fx.milestone("msg_1", at: 1, task: "tsk_1", title: "Fix the flaky login test",
                                                     event: "started"))
        let before = try fed.items()
        XCTAssertEqual(quests(before).first?.state, .starting)

        try fed.send("task.updated", Fx.task("tsk_1", title: "Fix the flaky login test", state: "working", at: 2))
        try fed.send("task.event", ["task_id": "tsk_1", "seq": 1, "type": "progress", "at": Fx.at(2),
                                    "data": ["text": "Found the race."]])
        let after = try fed.items()
        XCTAssertEqual(after.map(\.id), before.map(\.id), "the same rows, the same ids")
        XCTAssertEqual(quests(after).first?.state, .working)
    }

    func testALaterMilestoneOfACardedThreadIsOneLineNotASecondCard() throws {
        let fed = try FedCache()
        try fed.send("message.created", Fx.milestone("msg_1", at: 1, task: "tsk_1", title: "Backup check",
                                                     event: "started"))
        try fed.send("message.created", Fx.milestone("msg_2", at: 2, task: "tsk_1", title: "Backup check",
                                                     event: "failed", summary: "The NAS did not answer."))
        let items = try fed.items()
        XCTAssertEqual(quests(items).count, 1)
        XCTAssertEqual(quests(items).first?.state, .working, "before the task itself arrives, the milestone says")
        guard case .marcel(let line) = items.last else { return XCTFail("a Marcel line") }
        XCTAssertEqual(line.body.text, "Backup check failed. The NAS did not answer.")
    }

    // MARK: The approval gate

    func testOnlyAnOpenApprovalCanBeAnsweredAndOnlyOnce() async throws {
        let hub = FakeService()
        hub.approvalsOnHub["apr_open"] = Fx.decode(Fx.approval("apr_open", task: "t", summary: "Push"))
        hub.approvalsOnHub["apr_done"] = Fx.decode(Fx.approval("apr_done", task: "t", summary: "Push",
                                                                state: "denied", via: "terminal"))
        let model = ChatModel(service: hub, conversationId: Fx.conversation)

        await model.answer(approvalId: "apr_open", approve: true)
        XCTAssertEqual(hub.answers.count, 0, "an approval the app has not loaded is never answered blind")

        await model.refreshApprovals()
        await model.answer(approvalId: "apr_done", approve: true)
        XCTAssertEqual(hub.answers.count, 0, "a resolved approval sends nothing")

        await model.answer(approvalId: "apr_open", approve: false)
        await model.answer(approvalId: "apr_open", approve: true)
        XCTAssertEqual(hub.answers.map(\.approve), [false], "the first answer wins; a second tap sends nothing")
    }

    func testAnApprovalAnsweredInATerminalGreysOutHere() async throws {
        let fed = try FedCache()
        let hub = FakeService()
        let model = ChatModel(service: hub, conversationId: Fx.conversation)
        hub.approvalsOnHub["apr_1"] = Fx.decode(Fx.approval("apr_1", task: "tsk_1", summary: "Run `git push`"))
        try fed.send("task.updated", Fx.task("tsk_1", title: "Login test", state: "needs_you",
                                             extra: ["approval_ids": ["apr_1"]]))
        try fed.send("message.created", Fx.approvalMessage("msg_1", at: 1, approval: "apr_1", task: "tsk_1"))
        await model.reconcile(try fed.input())
        XCTAssertEqual(approvals(ChatFeed.items(try fed.input(), local: model.local)).first?.status, .open)

        hub.approvalsOnHub["apr_1"] = Fx.decode(Fx.approval("apr_1", task: "tsk_1", summary: "Run `git push`",
                                                             state: "approved", via: "terminal"))
        try fed.send("task.updated", Fx.task("tsk_1", title: "Login test", state: "working", at: 2))
        await model.reconcile(try fed.input())
        let line = try XCTUnwrap(approvals(ChatFeed.items(try fed.input(), local: model.local)).first)
        XCTAssertEqual(line.status, .approved(viaTerminal: true))
        XCTAssertFalse(line.status.isOpen)
        XCTAssertEqual(line.outcome, "Approved in a terminal.")

        let fetches = hub.approvalFetches
        await model.reconcile(try fed.input())
        XCTAssertEqual(hub.approvalFetches, fetches, "nothing new to ask: no fetch")
    }

    func testAFailedAnswerIsSaidPlainlyAndTheQuestionStaysOpen() async throws {
        let hub = FakeService()
        hub.approvalsOnHub["apr_1"] = Fx.decode(Fx.approval("apr_1", task: "t", summary: "Push"))
        let model = ChatModel(service: hub, conversationId: Fx.conversation)
        await model.refreshApprovals()
        hub.failing = true
        await model.answer(approvalId: "apr_1", approve: true)
        XCTAssertEqual(model.problem, "Couldn't send your answer. Check the connection and try again.")
        XCTAssertEqual(model.local.approvals["apr_1"]?.state, .open)
        XCTAssertTrue(model.local.answering.isEmpty)
    }

    // MARK: Items

    func testANewVersionUpdatesTheCardAndNeverAnnouncesTheItemAgain() throws {
        let fed = try FedCache()
        try fed.send("artifact.created", Fx.artifact("art_1", title: "Switching to Luminus"))
        try fed.send("message.created", Fx.artifactMessage("msg_1", at: 1, artifact: "art_1", project: "prj_energy"))
        XCTAssertEqual(itemLines(try fed.items()).map(\.badge), ["New"])

        try fed.send("artifact.updated", [
            "artifact": Fx.artifact("art_1", title: "Switching to Luminus", version: 2),
            "version": ["artifact_id": "art_1", "version": 2, "created_at": Fx.at(2)],
        ])
        // Even a second "item arrived" message for the same item (which the hub should not send) adds nothing.
        try fed.send("message.created", Fx.artifactMessage("msg_2", at: 2, artifact: "art_1", project: "prj_energy"))
        let items = itemLines(try fed.items())
        XCTAssertEqual(items.count, 1)
        XCTAssertEqual(items.first?.badge, "Updated · v2")
        XCTAssertEqual(items.filter(\.showsBanner).count, 1)
    }

    func testAnItemWhoseArtifactHasNotArrivedSaysSo() throws {
        let fed = try FedCache()
        try fed.send("message.created", Fx.artifactMessage("msg_1", at: 1, artifact: "art_x", project: "prj_energy"))
        let item = try XCTUnwrap(itemLines(try fed.items()).first)
        XCTAssertEqual(item.badge, "Arriving")
        XCTAssertFalse(item.showsBanner)
    }

    func testTheMorningBoardIsADigestCardWithItsFirstEntries() async throws {
        let fed = try FedCache()
        let hub = FakeService()
        let model = ChatModel(service: hub, conversationId: Fx.conversation)
        let board = Fx.artifact("art_board", title: "Morning board", kind: "board", version: 7)
        hub.contents["art_board"] = Data("""
        {"version": 1, "title": "Morning", "widgets": [
          {"type": "section", "title": "Waiting on you", "widgets": [{"type": "list", "items": [
            {"text": "Approve dentist Tue 9:10"}, {"text": "Sunday lunch: pick 1 of 2"}]}]},
          {"type": "section", "title": "Upcoming", "empty_text": "Nothing scheduled.", "widgets": []},
          {"type": "stat", "label": "Threads finished overnight", "value": 3}
        ]}
        """.utf8)
        try fed.send("artifact.created", board)
        try fed.send("message.created", Fx.artifactMessage("msg_1", at: 1, artifact: "art_board", project: "prj_energy"))

        var digest = try XCTUnwrap(itemLines(try fed.items()).first)
        XCTAssertTrue(digest.isDigest)
        XCTAssertFalse(digest.showsBanner, "the board is not a new item")
        XCTAssertEqual(digest.badge, "Board · v7")

        await model.loadPreview(Fx.decode(board))
        digest = try XCTUnwrap(itemLines(ChatFeed.items(try fed.input(), local: model.local)).first)
        XCTAssertEqual(digest.rows, [
            PreviewRow("Approve dentist Tue 9:10"), PreviewRow("Sunday lunch: pick 1 of 2"),
            PreviewRow("Threads finished overnight", value: "3", emphasised: true),
        ])
    }

    func testPreviewsOfADocumentAndAPullRequest() {
        let doc: Artifact = Fx.decode(Fx.artifact("art_doc", title: "Offers"))
        XCTAssertTrue(ArtifactPreview.fetchesContent(doc))
        XCTAssertEqual(ArtifactPreview.contentRows(doc, data: Data("# Switch to Luminus\n\nCheapest of **4** offers.\n\n- €412 a year less\n- From 1 November\n- No exit fee".utf8)),
                       [PreviewRow("Switch to Luminus", emphasised: true), PreviewRow("Cheapest of 4 offers."),
                        PreviewRow("• €412 a year less"), PreviewRow("• From 1 November")])
        XCTAssertEqual(ArtifactPreview.summaryRows(doc),
                       [PreviewRow("Made by", value: "Energy contracts"), PreviewRow("Version", value: "1")])

        let pr: Artifact = Fx.decode(Fx.artifact("art_pr", title: "Fix login", kind: "pr"))
        XCTAssertFalse(ArtifactPreview.fetchesContent(pr))
        XCTAssertEqual(ArtifactPreview.summaryRows(pr), [
            PreviewRow("Pull request #14", value: "Open", emphasised: true), PreviewRow("Checks", value: "Passing"),
            PreviewRow("Review", value: "Requested"),
        ])
        XCTAssertNil(ArtifactPreview.contentRows(doc, data: Data()))
    }

    func testAProjectKeepsItsColour() {
        XCTAssertEqual(ChatFeed.tone(for: "prj_01JA0A1"), ChatFeed.tone(for: "prj_01JA0A1"))
        let tones = Set(["prj_a", "prj_b", "prj_c", "prj_d", "prj_e", "prj_f"].map(ChatFeed.tone(for:)))
        XCTAssertGreaterThan(tones.count, 1, "projects spread over the colours")
    }
}
