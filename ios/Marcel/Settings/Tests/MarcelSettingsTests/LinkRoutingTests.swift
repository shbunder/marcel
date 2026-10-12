import Foundation
import MarcelKit
import UserNotifications
import XCTest

@testable import MarcelSettings

/// The story's test: every deep link routes correctly from a cold start, and while the app is running.
///
/// XCUITest needs the iOS 26.5 platform this Mac's Xcode lacks (see ios/README.md), so these drive the layer the app
/// is wired from, exactly as `AppDelegate` and `MainTabs` wire it: one `LinkRouter` and `PushHandler` made at launch,
/// links and tapped pushes handed to them before any screen exists ("cold start"), then the tabs coming up and
/// attaching the router to the paired hub (`LiveLinkLookup` over a real in-memory cache and the real generated client,
/// with a fake hub behind it). "Running" attaches first, then delivers the link.
@MainActor
final class LinkRoutingTests: XCTestCase {
    /// What the phone has cached: the main chat's message, a quest's own thread message, and one project.
    private func lookup(_ hub: FakeHub) throws -> LiveLinkLookup {
        let cache = MarcelCache(container: try MarcelCache.makeContainer(inMemory: true))
        try cache.upsert(Fx.decode(Fx.message("msg_main", conversation: "cnv_main")) as Message)
        try cache.upsert(Fx.decode(Fx.message("msg_side", conversation: "cnv_side_1", task: "tsk_energy")) as Message)
        try cache.upsert(Fx.decode(Fx.project("prj_garden", slug: "garden", name: "Garden")) as Project)
        try cache.save()
        return LiveLinkLookup(cache: cache, service: hub.service, mainConversationId: { "cnv_main" })
    }

    /// Every link the app routes, and where it must land.
    static let routes: [(link: String, place: LinkDestination)] = [
        ("marcel://task/tsk_energy", .quest(taskId: "tsk_energy")),
        ("marcel://message/msg_main", .chat(messageId: "msg_main")),
        // A message in a quest's own thread opens that quest (the app has no other thread view).
        ("marcel://message/msg_side", .quest(taskId: "tsk_energy")),
        // Not on this phone yet (the push outran the stream): the chat, which scrolls to it if it comes.
        ("marcel://message/msg_new", .chat(messageId: "msg_new")),
        // The contract has no GET /approvals/{id}: the task is found in GET /approvals.
        ("marcel://approval/apr_engie", .quest(taskId: "tsk_energy")),
        // A cached project, and one only the hub knows yet.
        ("marcel://project/garden", .room(projectId: "prj_garden")),
        ("marcel://project/energy", .room(projectId: "prj_energy")),
        ("marcel://artifact/art_board", .artifact(id: "art_board")),
        ("marcel://status", .status(alert: nil)),
    ]

    // MARK: Cold start

    func testEveryLinkRoutesFromAColdStart() async throws {
        for (link, place) in Self.routes {
            let hub = FakeHub()
            let router = LinkRouter()
            // The app is launched by the link: no tabs, no hub yet.
            await router.handle(URL(string: link)!)
            XCTAssertNil(router.destination, "\(link): nothing is shown before the tabs are up")
            XCTAssertNotNil(router.pending, "\(link) waits")
            XCTAssertEqual(hub.requests, [], "\(link): nothing is asked of the hub before pairing is known")
            // The tabs come up and attach the router to the paired hub.
            await router.attach(try lookup(hub))
            XCTAssertEqual(router.destination, place, link)
            XCTAssertNil(router.pending)
            XCTAssertNil(router.notice, link)
            router.consume()
            XCTAssertNil(router.destination)
        }
    }

    func testEveryTappedPushRoutesFromAColdStart() async throws {
        for (link, place) in Self.routes {
            let hub = FakeHub()
            let router = LinkRouter()
            let push = PushHandler(router: router, answer: { _, _ in }, alerts: LastAlertStore(defaults: .scratch()), report: { _ in })
            // iOS launches the app and hands the tapped push to the delegate set in didFinishLaunching.
            let category = link.contains("status") ? "broke" : "task"
            await push.respond(action: UNNotificationDefaultActionIdentifier,
                               payload: PushPayload(title: "Marcel", body: "About \(link)", category: category,
                                                    deepLink: link))
            XCTAssertNil(router.destination, link)
            await router.attach(try lookup(hub))
            if case .status(let alert) = place {
                XCTAssertNil(alert)
                guard case .status(let shown) = router.destination else { return XCTFail("\(link): \(String(describing: router.destination))") }
                XCTAssertEqual(shown?.body, "About marcel://status", "the status screen shows the push that opened it")
            } else {
                XCTAssertEqual(router.destination, place, link)
            }
        }
    }

    func testOnlyTheNewestLinkWaitingIsOpened() async throws {
        let router = LinkRouter()
        await router.handle(URL(string: "marcel://task/tsk_old")!)
        await router.handle(URL(string: "marcel://artifact/art_new")!)
        await router.attach(try lookup(FakeHub()))
        XCTAssertEqual(router.destination, .artifact(id: "art_new"))
    }

    // MARK: Running

    func testEveryLinkRoutesWhileRunning() async throws {
        let hub = FakeHub()
        let router = LinkRouter()
        await router.attach(try lookup(hub))
        for (link, place) in Self.routes {
            await router.handle(URL(string: link)!)
            XCTAssertEqual(router.destination, place, link)
            XCTAssertNil(router.notice, link)
            router.consume()
        }
    }

    func testUnpairingMakesLinksWaitAgain() async throws {
        let router = LinkRouter()
        await router.attach(try lookup(FakeHub()))
        router.detach()
        await router.handle(URL(string: "marcel://task/tsk_1")!)
        XCTAssertNil(router.destination)
        XCTAssertEqual(router.pending, .link(.task("tsk_1"), alert: nil))
    }

    // MARK: Bad links

    func testBadLinksSayWhyAndOpenNothing() async throws {
        let bad: [(String, String)] = [
            ("https://evil.example/task/1", "That isn't a Marcel link, so the app ignored it."),
            ("marcel://settings", "That Marcel link points somewhere this app doesn't know. The app may need an update."),
            ("marcel://task/../../etc", "That Marcel link is damaged, so the app couldn't open it."),
            ("marcel://task/tsk_1?then=https://evil.example", "That Marcel link is damaged, so the app couldn't open it."),
            ("marcel://pair?hub=https%3A%2F%2Fevil.example&code=K7Q2M9",
             "This phone is paired already. To pair again, unpair in Settings and scan the new code."),
        ]
        for attached in [false, true] {
            let hub = FakeHub()
            let router = LinkRouter()
            if attached { await router.attach(try lookup(hub)) }
            for (link, words) in bad {
                await router.handle(URL(string: link)!)
                XCTAssertNil(router.destination, link)
                XCTAssertNil(router.pending, "\(link): a bad link never waits")
                XCTAssertEqual(router.notice, words, link)
            }
            XCTAssertEqual(hub.requests, [], "a bad link never reaches the hub")
        }
    }

    func testAPushWithABadOrMissingLinkSaysSo() async {
        let router = LinkRouter()
        let push = PushHandler(router: router, answer: { _, _ in }, alerts: LastAlertStore(defaults: .scratch()), report: { _ in })
        for link in [nil, "marcel://task/a b", "https://example.com"] as [String?] {
            router.notice = nil
            await push.respond(action: UNNotificationDefaultActionIdentifier,
                               payload: PushPayload(title: "t", body: "b", category: "task", deepLink: link))
            XCTAssertNil(router.destination)
            XCTAssertNotNil(router.notice, String(describing: link))
        }
    }

    func testALinkToSomethingGoneSaysSo() async throws {
        let hub = FakeHub()
        let router = LinkRouter()
        await router.attach(try lookup(hub))
        await router.handle(URL(string: "marcel://approval/apr_gone")!)
        XCTAssertEqual(router.notice, "That question has gone: it was answered or it expired.")
        await router.handle(URL(string: "marcel://project/no-such")!)
        XCTAssertEqual(router.notice, "Marcel has no project at that link any more. It may have been archived.")
        hub.offline = true
        await router.handle(URL(string: "marcel://approval/apr_engie")!)
        XCTAssertEqual(router.notice, "Couldn't reach the hub to open that link. Check the connection and try again.")
        XCTAssertNil(router.destination)
        // A good link clears the notice.
        await router.handle(URL(string: "marcel://task/tsk_energy")!)
        XCTAssertNil(router.notice)
    }

    // MARK: Push categories and actions

    func testEveryCategoryTheContractListsIsRegistered() throws {
        let contract = try String(contentsOf: URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
            .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
            .appendingPathComponent("contracts/app-api.yaml"), encoding: .utf8)
        let push = try XCTUnwrap(contract.range(of: "    PushNotification:"))
        let category = try XCTUnwrap(contract.range(of: "enum: [", range: push.upperBound..<contract.endIndex))
        let end = try XCTUnwrap(contract.range(of: "]", range: category.upperBound..<contract.endIndex))
        let listed = contract[category.upperBound..<end.lowerBound].split(separator: ",")
            .map { $0.trimmingCharacters(in: .whitespaces) }
        XCTAssertEqual(Set(listed), ["approval", "task", "broke", "digest", "note"])

        let categories = PushCategories.make()
        XCTAssertEqual(Set(categories.map(\.identifier)), Set(listed))
        for category in categories {
            let actions = category.actions.map(\.identifier)
            XCTAssertEqual(actions, category.identifier == "approval" ? ["approve", "deny"] : [], category.identifier)
            for action in category.actions {
                XCTAssertTrue(action.options.contains(.authenticationRequired), "answering needs an unlocked phone")
                XCTAssertFalse(action.options.contains(.foreground), "answering does not open the app")
            }
        }
    }

    private func approvalPush(_ id: String? = "apr_engie") -> PushPayload {
        PushPayload(title: "Marcel needs you", body: "Allow `docker system df -v`?", category: "approval",
                    deepLink: "marcel://approval/apr_engie", approvalId: id, taskId: "tsk_energy")
    }

    /// The push handler exactly as `AppDelegate` makes it at launch: answering from the saved hub and token.
    private func launchedHandler(_ hub: FakeHub, paired: Bool = true, router: LinkRouter = LinkRouter(),
                                 reported: @escaping (String) -> Void) -> PushHandler {
        let stores = PairedStores(paired: paired)
        return PushHandler(router: router, answer: PushHandler.answering(hubStore: stores.hub, tokenStore: stores.token,
                                                                         transport: hub),
                           alerts: LastAlertStore(defaults: .scratch()), report: { reported($0) })
    }

    func testApproveAndDenyAnswerWithoutOpeningTheApp() async throws {
        let hub = FakeHub()
        hub["POST /approvals/apr_engie"] = (200, Fx.json(Fx.approvals[0].merging(["state": "approved"]) { $1 }))
        let router = LinkRouter()
        var reported: [String] = []
        let push = launchedHandler(hub, router: router) { reported.append($0) }

        await push.respond(action: PushCategories.approve, payload: approvalPush())
        await push.respond(action: PushCategories.deny, payload: approvalPush())
        XCTAssertEqual(hub.requests, ["POST /approvals/apr_engie", "POST /approvals/apr_engie"])
        XCTAssertEqual(hub.sentBodies.map { $0.contains("\"approve\"") }, [true, false])
        XCTAssertEqual(hub.sentBodies.map { $0.contains("\"deny\"") }, [false, true])
        XCTAssertNil(router.destination, "an action answers in place; it opens nothing")
        XCTAssertNil(router.pending)
        XCTAssertEqual(reported, [])
    }

    /// Review fix 1: iOS starts the app in the background for the lock screen's Approve. No screen loads, so the
    /// router is never attached; the answer must still go out with the saved hub and token.
    func testApproveFromTheLockScreenWorksWhenTheAppWasNotRunning() async {
        let hub = FakeHub()
        hub["POST /approvals/apr_engie"] = (200, Fx.json(Fx.approvals[0].merging(["state": "approved"]) { $1 }))
        let router = LinkRouter()
        var reported: [String] = []
        let push = launchedHandler(hub, router: router) { reported.append($0) }
        XCTAssertFalse(router.isAttached, "a background launch: no tabs, no attach")
        await push.respond(action: PushCategories.approve, payload: approvalPush())
        XCTAssertEqual(hub.requests, ["POST /approvals/apr_engie"])
        XCTAssertEqual(reported, [], "not the false \"isn't paired\"")
    }

    func testAnAnswerThatFailsIsReportedInPlainWords() async {
        let hub = FakeHub()
        var reported: [String] = []
        // Really not paired: no hub saved.
        let unpaired = launchedHandler(hub, paired: false) { reported.append($0) }
        await unpaired.respond(action: PushCategories.approve, payload: approvalPush())
        XCTAssertEqual(reported, ["This phone isn't paired, so nothing was answered."])
        XCTAssertEqual(hub.requests, [], "not paired: nothing is sent")

        reported = []
        let push = launchedHandler(hub) { reported.append($0) }
        hub["POST /approvals/apr_engie"] = (409, Fx.json(Fx.approvals[0].merging(["state": "denied"]) { $1 }))
        await push.respond(action: PushCategories.approve, payload: approvalPush())
        await push.respond(action: PushCategories.approve, payload: approvalPush(nil))
        hub["POST /approvals/apr_engie"] = (401, Fx.error("unauthorized", "Pair again."))
        await push.respond(action: PushCategories.approve, payload: approvalPush())
        hub["POST /approvals/apr_engie"] = (500, Fx.error("internal", "Boom."))
        await push.respond(action: PushCategories.approve, payload: approvalPush())
        hub.offline = true
        await push.respond(action: PushCategories.approve, payload: approvalPush())
        XCTAssertEqual(reported, [
            "That question was already answered, or it expired.",
            "That question came without its id, so nothing was answered. Open Marcel to answer it.",
            "The hub no longer accepts this phone, so nothing was answered. Pair it again.",
            "The hub had a problem, so nothing was answered. Open Marcel and answer it there.",
            "Couldn't reach the hub, so nothing was answered. Open Marcel and answer it there.",
        ])
    }

    func testAnApprovalIdThatIsNotAnIdIsNeverSent() {
        XCTAssertNil(approvalPush("../tasks/x").approvalId)
        XCTAssertNil(PushPayload(title: "", body: "", category: "approval",
                                 userInfo: ["approval_id": "a/b", "deep_link": 3]).approvalId)
    }

    func testAPushFromAnApprovalReadsFromItsUserInfo() {
        let payload = PushPayload(title: "Marcel needs you", body: "Allow?", category: "approval", userInfo: [
            "aps": ["alert": ["title": "Marcel needs you", "body": "Allow?"], "category": "approval"],
            "deep_link": "marcel://approval/apr_01J9ZR2", "approval_id": "apr_01J9ZR2", "task_id": "tsk_01J9ZQ6",
            "level": "urgent", "collapse_key": "tsk_01J9ZQ6",
        ])
        XCTAssertEqual(payload.deepLink, "marcel://approval/apr_01J9ZR2")
        XCTAssertEqual(payload.approvalId, "apr_01J9ZR2")
        XCTAssertEqual(payload.taskId, "tsk_01J9ZQ6")
    }

    func testABrokePushIsKeptForTheStatusScreen() async {
        let defaults = UserDefaults.scratch()
        let alerts = LastAlertStore(defaults: defaults)
        let push = PushHandler(router: LinkRouter(), answer: { _, _ in }, alerts: alerts, report: { _ in })
        push.arrived(PushPayload(title: "Marcel", body: "Finished", category: "task", deepLink: "marcel://task/t1"))
        XCTAssertNil(alerts.load(), "only faults are kept")
        push.arrived(PushPayload(title: "Something broke", body: "The NUC runner stopped answering.",
                                 category: "broke", deepLink: "marcel://status"))
        XCTAssertEqual(alerts.load()?.body, "The NUC runner stopped answering.")
        XCTAssertEqual(StatusModel(service: FakeHub().service, alerts: alerts).alert?.title, "Something broke")
    }

    func testDismissingAPushDoesNothing() async {
        let router = LinkRouter()
        let push = PushHandler(router: router, answer: { _, _ in }, alerts: LastAlertStore(defaults: .scratch()), report: { _ in })
        await push.respond(action: UNNotificationDismissActionIdentifier, payload: approvalPush())
        XCTAssertNil(router.pending)
        XCTAssertNil(router.notice)
    }

    func testEachDestinationKnowsItsTab() {
        XCTAssertEqual(LinkDestination.chat(messageId: "m").tab, "chat")
        XCTAssertEqual(LinkDestination.room(projectId: "p").tab, "map")
        XCTAssertNil(LinkDestination.quest(taskId: "t").tab)
        XCTAssertNil(LinkDestination.artifact(id: "a").tab)
        XCTAssertNil(LinkDestination.status(alert: nil).tab)
    }

    // MARK: Review fixes 3 and 4

    /// Review fix 3: a pairing link while unpaired is said on the pairing screen, and goes away once paired.
    func testAPairingLinkWhileUnpairedIsSaidOnThePairingScreenAndClearedOnPairing() async throws {
        let pair = URL(string: "marcel://pair?hub=https%3A%2F%2Fhub.example%2Fapi&code=K7Q2M9")!
        let router = LinkRouter()
        router.detach()  // the pairing screen appeared
        await router.handle(pair)
        XCTAssertEqual(router.notice, LinkRouter.pairOnTheScreen)
        await router.attach(try lookup(FakeHub()))  // paired: the tabs came up
        XCTAssertNil(router.notice, "no stale message after pairing")

        // Cold launch by the link, before the app knew it was unpaired.
        let cold = LinkRouter()
        await cold.handle(pair)
        cold.detach()
        XCTAssertEqual(cold.notice, LinkRouter.pairOnTheScreen)

        // Paired: the link is refused with the paired wording, and it stays.
        let paired = LinkRouter()
        await paired.attach(try lookup(FakeHub()))
        await paired.handle(pair)
        XCTAssertEqual(paired.notice, DeepLink.Problem.pairing.errorDescription)
    }

    /// Review fix 4: a refused phone, a failing hub and no network are three different sentences.
    func testLookupFailuresSayWhatWentWrong() async throws {
        let cases: [((Int, String)?, Bool, String)] = [
            ((401, Fx.error("unauthorized", "No.")), false,
             "The hub no longer accepts this phone, so that link couldn't be opened. Pair it again."),
            ((500, Fx.error("internal", "Boom.")), false, "The hub had a problem opening that link. Try again in a minute."),
            ((503, ""), false, "The hub had a problem opening that link. Try again in a minute."),
            (nil, true, "Couldn't reach the hub to open that link. Check the connection and try again."),
        ]
        for (answer, offline, words) in cases {
            let hub = FakeHub()
            hub["GET /approvals"] = answer
            hub.offline = offline
            let router = LinkRouter()
            await router.attach(try lookup(hub))
            await router.handle(URL(string: "marcel://approval/apr_engie")!)
            XCTAssertEqual(router.notice, words, "\(String(describing: answer))")
            XCTAssertNil(router.destination)
        }
    }
}
