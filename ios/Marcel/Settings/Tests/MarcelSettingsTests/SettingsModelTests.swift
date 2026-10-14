import Foundation
import MarcelKit
import UIKit
import XCTest

@testable import MarcelSettings

/// Settings against a fake hub through the real generated client: each section, what the buttons send, and a hub
/// that has not built schedules (S-09.1) or usage (S-10.1) yet.
@MainActor
final class SettingsModelTests: XCTestCase {
    private func loaded(_ hub: FakeHub = FakeHub(), tokens: FakeTokens = FakeTokens(),
                        defaults: UserDefaults = .scratch(#function)) async -> SettingsModel {
        let model = SettingsModel(service: hub.service, tokens: tokens, topics: NtfyTopicStore(defaults: defaults))
        await model.load()
        return model
    }

    func testEverySectionLoads() async {
        let hub = FakeHub()
        let model = await loaded(hub)
        XCTAssertEqual(model.name, "Marcel")
        XCTAssertEqual(model.devices.value?.map(\.id), ["dev_phone", "dev_ipad"])
        XCTAssertEqual(model.schedules.value?.map(\.id), ["sch_digest_morning", "sch_deps"])
        XCTAssertEqual(model.usage.value?.fiveHour.usedPct, 91)
        XCTAssertNil(model.problem)
        XCTAssertTrue(hub.requests.contains("GET /schedules?agent_id=agt_marcel"), "\(hub.requests)")
    }

    func testAHubWithoutSchedulesOrUsageSaysNotAvailableYet() async {
        for status in [404, 405, 501] {
            let hub = FakeHub()
            hub["GET /schedules"] = status == 404 ? nil : (status, "")
            hub["GET /usage"] = status == 404 ? nil : (status, "")
            let model = await loaded(hub)
            XCTAssertEqual(model.schedules, .notAvailableYet, "HTTP \(status)")
            XCTAssertEqual(model.usage, .notAvailableYet, "HTTP \(status)")
            XCTAssertNotNil(model.devices.value, "the other sections still load")
            XCTAssertNil(model.problem)
        }
    }

    func testOfflineSaysSoInEachSection() async {
        let hub = FakeHub()
        hub.offline = true
        let model = await loaded(hub)
        XCTAssertEqual(model.problem, "Couldn't reach the hub. Check the connection and try again.")
        XCTAssertEqual(model.devices, .failed("Couldn't reach the hub. Check the connection and try again."))
        XCTAssertEqual(model.usage, .failed("Couldn't reach the hub. Check the connection and try again."))
    }

    /// S-12.11: 503 `no_reading` (the runner has not read usage yet) is "usage unavailable", a note and not a
    /// problem; any other 503 is still the hub's own trouble.
    func testUsageWithNoReadingYetSaysUsageUnavailable() async {
        let hub = FakeHub()
        hub["GET /usage"] = (503, Fx.error("no_reading", "No usage reading yet."))
        let model = await loaded(hub)
        XCTAssertEqual(model.usage, .unavailable(
            "Usage unavailable: the hub hasn't read the plan's usage yet. It shows here once it has."))
        XCTAssertNil(model.problem)
        XCTAssertNotNil(model.schedules.value, "the other sections still load")

        hub["GET /usage"] = (503, Fx.error("runner_unreachable", "No runner."))
        await model.loadUsage()
        XCTAssertEqual(model.usage, .failed("The hub had a problem. Try again in a minute."))
    }

    // MARK: Name

    /// S-12.11: the chat header follows the agent's name: told on load and after a rename.
    func testTheNameIsPassedOnForTheChatHeader() async {
        let hub = FakeHub()
        hub["PATCH /agents/agt_marcel"] = (200, Fx.json(Fx.agent.merging(["name": "Marcella"]) { $1 }))
        var told: [String] = []
        let model = SettingsModel(service: hub.service, tokens: FakeTokens(), topics: NtfyTopicStore(defaults: .scratch()))
        model.nameChanged = { told.append($0) }
        await model.load()
        model.name = "Marcella"
        await model.saveName()
        XCTAssertEqual(told, ["Marcel", "Marcella"])
    }

    func testRenamingTheAgent() async {
        let hub = FakeHub()
        hub["PATCH /agents/agt_marcel"] = (200, Fx.json(Fx.agent.merging(["name": "Marcella"]) { $1 }))
        let model = await loaded(hub)
        XCTAssertNil(model.nameToSave, "unchanged")
        model.name = "   "
        XCTAssertNil(model.nameToSave, "empty")
        model.name = String(repeating: "x", count: 41)
        XCTAssertNil(model.nameToSave, "too long")
        model.name = "  Marcella "
        XCTAssertEqual(model.nameToSave, "Marcella")
        await model.saveName()
        XCTAssertEqual(hub.lastBody, ["name": "Marcella"])
        XCTAssertEqual(model.agent?.name, "Marcella")
        XCTAssertNil(model.nameToSave)
    }

    /// Review fix: the real hub answers a route it does not serve with 404 `not_found`, which the contract's 404 decodes;
    /// that is "not available yet", not "the hub no longer has that".
    func testRenamingOnAHubThatCannotYet() async {
        let model = await loaded()
        model.name = "Marcella"
        await model.saveName()
        XCTAssertEqual(model.problem, "Not available yet: this hub can't rename Marcel yet.")
    }

    // MARK: Notifications

    func testNtfyWhenTheHubHasNoAPNs() async throws {
        let hub = FakeHub()
        hub["GET /devices"] = (200, Fx.json(Fx.devices(phonePush: "none")))
        hub["PUT /devices/dev_phone/push"] = (200, Fx.json(Fx.devices()[0]))
        let tokens = FakeTokens()
        let model = await loaded(hub, tokens: tokens)
        XCTAssertEqual(model.push, .off)
        await model.turnOnNotifications()
        XCTAssertEqual(tokens.asked, 0, "the hub can't send through APNs: don't ask Apple")
        guard case .ntfy(let topic, let server) = model.push else { return XCTFail("\(model.push)") }
        XCTAssertEqual(server, "https://ntfy.sh")
        XCTAssertTrue(topic.hasPrefix("marcel-") && topic.count == 31, topic)
        XCTAssertEqual(hub.lastBody, ["kind": "ntfy", "ntfy_topic": topic])
    }

    func testTheNtfyTopicIsKeptAndUnguessable() {
        let defaults = UserDefaults.scratch()
        let store = NtfyTopicStore(defaults: defaults)
        let first = store.topic()
        XCTAssertEqual(store.topic(), first, "made once")
        XCTAssertNotEqual(NtfyTopicStore.make(), NtfyTopicStore.make())
        XCTAssertTrue(first.dropFirst("marcel-".count).allSatisfy { $0.isLowercase || $0.isNumber })
    }

    func testAPNsWhenTheHubAndThisBuildCan() async {
        let hub = FakeHub()
        hub["GET /me"] = (200, Fx.json(Fx.me(apns: true)))
        hub["GET /devices"] = (200, Fx.json(Fx.devices(phonePush: "none")))
        hub["PUT /devices/dev_phone/push"] = (200, Fx.json(Fx.devices(phonePush: "apns")[0]))
        let tokens = FakeTokens()
        let token = APNsToken.hex(Data((0..<32).map { UInt8($0 * 7 & 0xFF) }))
        tokens.result = .success(token)
        let model = await loaded(hub, tokens: tokens)
        await model.turnOnNotifications()
        XCTAssertEqual(model.push, .apns)
        XCTAssertEqual(hub.lastBody, ["kind": "apns", "apns_token": token])
        XCTAssertEqual(model.devices.value?.first?.push, .apns)
    }

    /// S-12.12: the token goes hex-encoded (`PushTarget.apns_token`'s pattern, app API 0.6), and a token that does not
    /// match is never sent: the hub would refuse it and Apple answer `BadDeviceToken`.
    func testTheAPNsTokenIsHexAndCheckedBeforeItIsSent() async {
        XCTAssertEqual(APNsToken.hex(Data([0x00, 0x0F, 0xAB, 0xFF])), "000fabff")
        let apple = Data((0..<32).map { UInt8($0) })
        XCTAssertEqual(APNsToken.hex(apple).count, 64)
        XCTAssertTrue(APNsToken.isValid(APNsToken.hex(apple)))
        XCTAssertTrue(APNsToken.isValid(String(repeating: "A", count: 200)), "upper case, at the longest")
        for bad in [nil, "", "a1b2c3", String(repeating: "a", count: 63), String(repeating: "a", count: 201),
                    String(repeating: "g", count: 64), "<" + String(repeating: "a", count: 64) + ">",
                    apple.base64EncodedString(), String(repeating: "é", count: 64)] as [String?] {
            XCTAssertFalse(APNsToken.isValid(bad), String(describing: bad))
        }

        let hub = FakeHub()
        hub["GET /me"] = (200, Fx.json(Fx.me(apns: true)))
        hub["PUT /devices/dev_phone/push"] = (200, Fx.json(Fx.devices(phonePush: "apns")[0]))
        let tokens = FakeTokens()
        tokens.result = .success(apple.base64EncodedString())
        let model = await loaded(hub, tokens: tokens)
        await model.turnOnNotifications()
        XCTAssertFalse(hub.requests.contains { $0.hasPrefix("PUT") }, "never sent")
        XCTAssertEqual(model.problem, "Apple gave this phone a push token the hub can't use, so notifications weren't "
                       + "turned on. Try again in a minute.")
        XCTAssertNotEqual(model.push, .apns)

        // The token Apple sends a running app (`AppDelegate` → `onToken` → `setPush`) is checked the same way.
        do {
            _ = try await hub.service.setPush(deviceId: "dev_phone", target: .init(kind: .apns, apnsToken: "xyz"))
            XCTFail("expected a refusal")
        } catch {
            XCTAssertEqual(error as? SettingsError, .badPushToken)
        }
        XCTAssertFalse(hub.requests.contains { $0.hasPrefix("PUT") })
    }

    func testFallsBackToNtfyWhenThisBuildHasNoPushEntitlement() async {
        let hub = FakeHub()
        hub["GET /me"] = (200, Fx.json(Fx.me(apns: true)))
        hub["PUT /devices/dev_phone/push"] = (200, Fx.json(Fx.devices()[0]))
        let model = await loaded(hub)
        await model.turnOnNotifications()
        guard case .ntfy = model.push else { return XCTFail("\(model.push)") }
        XCTAssertEqual(model.pushNote,
                       "This build can't use Apple's push service, so notifications go through the ntfy app.")
    }

    func testNotificationsRefusedByThePerson() async {
        let hub = FakeHub()
        hub["GET /me"] = (200, Fx.json(Fx.me(apns: true)))
        let tokens = FakeTokens()
        tokens.result = .failure(PushSetupError.notAllowed)
        let model = await loaded(hub, tokens: tokens)
        await model.turnOnNotifications()
        XCTAssertEqual(model.problem,
                       "Notifications are off for Marcel. Turn them on in the iPhone's Settings app, then try again.")
        XCTAssertFalse(hub.requests.contains { $0.hasPrefix("PUT") })
    }

    func testAHubWithNoPushAtAll() async {
        let hub = FakeHub()
        hub["GET /me"] = (200, Fx.json(Fx.me(apns: false, ntfy: false)))
        let model = await loaded(hub)
        await model.turnOnNotifications()
        XCTAssertEqual(model.push, .off)
        XCTAssertEqual(model.problem,
                       "The hub can't send notifications yet. Ask whoever runs it to set up APNs or ntfy.")
    }

    // MARK: Schedules

    func testPauseAndResume() async {
        let hub = FakeHub()
        hub["PATCH /schedules/sch_digest_morning"] = (200, Fx.json(Fx.schedules[0].merging(["paused": true]) { $1 }))
        let model = await loaded(hub)
        let digest = model.schedules.value![0]
        await model.setPaused(digest, true)
        XCTAssertEqual(hub.lastBody, ["paused": true])
        XCTAssertEqual(model.schedules.value?[0].paused, true)
    }

    func testDeleteAndBuiltInDigests() async {
        let hub = FakeHub()
        hub["DELETE /schedules/sch_deps"] = (204, "")
        let model = await loaded(hub)
        let (digest, report) = (model.schedules.value![0], model.schedules.value![1])
        await model.delete(digest)
        XCTAssertEqual(model.problem, Self.builtIn)
        XCTAssertEqual(model.pauseInstead?.id, "sch_digest_morning", "pause is offered instead")
        XCTAssertFalse(hub.requests.contains("DELETE /schedules/sch_digest_morning"), "not even asked")
        await model.delete(report)
        XCTAssertEqual(model.schedules.value?.map(\.id), ["sch_digest_morning"])
        XCTAssertNil(model.problem)
    }

    static let builtIn = "Built-in digests can't be deleted, only paused. A paused digest stops until you resume it."

    /// S-12.12: the hub's 409 `schedule_builtin` (app API 0.6) says why in plain words, hides Delete and offers pause;
    /// "Pause" pauses it.
    func testAHubRefusingToDeleteADigestOffersPauseInstead() async {
        let hub = FakeHub()
        hub["GET /schedules"] = (200, Fx.json([Fx.schedules[0], Fx.schedules[1].merging(["paused": false]) { $1 }]))
        hub["DELETE /schedules/sch_deps"] = (409, Fx.error("schedule_builtin", "Built-in."))
        hub["PATCH /schedules/sch_deps"] = (200, Fx.json(Fx.schedules[1].merging(["paused": true, "builtin": true]) { $1 }))
        let model = await loaded(hub)
        await model.delete(model.schedules.value![1])
        XCTAssertEqual(model.problem, Self.builtIn)
        XCTAssertEqual(model.schedules.value?.count, 2, "nothing was deleted")
        XCTAssertEqual(model.schedules.value?[1].builtin, true, "Delete is hidden from now on")
        XCTAssertEqual(model.pauseInstead?.id, "sch_deps")

        await model.pauseInsteadOfDeleting()
        XCTAssertEqual(hub.lastBody, ["paused": true])
        XCTAssertEqual(model.schedules.value?[1].paused, true)
        XCTAssertNil(model.pauseInstead)
        XCTAssertNil(model.problem)
    }

    /// A paused digest is not offered pause again.
    func testAPausedDigestIsNotOfferedPause() async {
        let hub = FakeHub()
        hub["DELETE /schedules/sch_deps"] = (409, Fx.error("schedule_builtin", "Built-in."))
        let model = await loaded(hub)
        XCTAssertEqual(model.schedules.value?[1].paused, true)
        await model.delete(model.schedules.value![1])
        XCTAssertEqual(model.problem, Self.builtIn)
        XCTAssertNil(model.pauseInstead)
    }

    /// S-12.12: 404 `schedule_not_found` means it is gone already; the hub's generic `not_found` means an older hub.
    func testDeletingAScheduleThatIsAlreadyGone() async {
        let hub = FakeHub()
        hub["DELETE /schedules/sch_deps"] = (404, Fx.error("schedule_not_found", "No such schedule."))
        let model = await loaded(hub)
        await model.delete(model.schedules.value![1])
        XCTAssertEqual(model.schedules.value?.map(\.id), ["sch_digest_morning"], "the row goes")
        XCTAssertEqual(model.problem, "That schedule was already deleted.")

        let older = FakeHub()
        older["DELETE /schedules/sch_deps"] = (404, Fx.error("not_found", "There is nothing at that address."))
        let olderModel = await loaded(older)
        await olderModel.delete(olderModel.schedules.value![1])
        XCTAssertEqual(olderModel.schedules.value?.count, 2)
        XCTAssertEqual(olderModel.problem, "Not available yet: the hub doesn't do this yet.")
    }

    // MARK: Devices

    func testRevokeAnotherDeviceButNotThisOne() async {
        let hub = FakeHub()
        hub["DELETE /devices/dev_ipad"] = (204, "")
        let model = await loaded(hub)
        await model.revoke(model.devices.value![0])
        XCTAssertEqual(model.problem, "To remove this phone, use Sign out of this phone.")
        await model.revoke(model.devices.value![1])
        XCTAssertEqual(model.devices.value?.map(\.id), ["dev_phone"])
        XCTAssertEqual(hub.requests.filter { $0.hasPrefix("DELETE") }, ["DELETE /devices/dev_ipad"])
    }

    // MARK: Words

    func testWhatTheSectionsSay() async {
        let text = Fx.text
        let usage: Usage = Fx.decode(Fx.usage)
        XCTAssertEqual(text.meters(usage).map(\.value), ["91 %", "48 %"])
        XCTAssertEqual(text.meters(usage).map(\.detail), ["resets 21:00", "resets Wed 7 Oct, 09:00"])
        XCTAssertEqual(text.usageNotes(usage), ["Plan usage is at 91 %. New tasks wait until 21:00.",
                                                "2 quests are waiting."])
        let schedules: [Schedule] = Fx.schedules.map { Fx.decode($0) }
        XCTAssertEqual(schedules.map(text.scheduleLine), ["Next: tomorrow 05:00 · Marcel", "Paused · Cloud"])
        let devices: [Device] = Fx.devices().map { Fx.decode($0) }
        XCTAssertEqual(devices.map(text.deviceLine), ["This phone · notifications by ntfy",
                                                      "Last seen Fri 2 Oct, 18:40 · no notifications"])
        let health: Health = Fx.decode(Fx.health)
        XCTAssertEqual(text.statusLines(health).map(\.value),
                       ["Running · v0.4.1", "Not reachable: The NUC runner didn't answer.", "Running"])
    }

    func testTheStatusScreenAsksHealthAndSaysWhenTheHubIsDown() async {
        let hub = FakeHub()
        let model = StatusModel(service: hub.service, alerts: LastAlertStore(defaults: .scratch()))
        await model.load()
        XCTAssertEqual(model.health.value?.runner.reachable, false)
        hub.offline = true
        await model.load()
        XCTAssertEqual(model.health, .failed("Couldn't reach the hub. It may be down, or this phone is offline."))
    }
}

/// The review's fixes to Settings: signing out forgets the topic, a hub's own failure says so, the topic stays on this
/// phone, and an APNs timeout fails only its own attempt.
@MainActor
final class SettingsReviewFixTests: XCTestCase {
    private func loaded(_ hub: FakeHub, topics: NtfyTopicStore) async -> SettingsModel {
        let model = SettingsModel(service: hub.service, tokens: FakeTokens(), topics: topics, thisDevice: "dev_phone")
        await model.load()
        return model
    }

    /// Fix 2, since S-17.5: "Sign out of this phone" forgets the ntfy topic and runs the app's sign-out, which tells
    /// the hub (AppFlow.signOut, tested in MarcelWelcome) and passes on its note.
    func testSignOutForgetsTheTopicAndRunsTheAppsSignOut() async {
        let hub = FakeHub()
        let topics = NtfyTopicStore(defaults: .scratch())
        _ = topics.topic()
        let model = await loaded(hub, topics: topics)
        var ran = 0
        let note = await model.signOut {
            ran += 1
            return "Signed out, but the hub wasn't told."
        }
        XCTAssertEqual(ran, 1)
        XCTAssertEqual(note, "Signed out, but the hub wasn't told.")
        XCTAssertNil(topics.saved, "the topic is forgotten")
    }

    /// Fix 4: a 5xx is the hub's own problem, not "something the app doesn't understand".
    func testAHubFailureSaysTheHubHadAProblem() async {
        let hub = FakeHub()
        hub["GET /usage"] = (500, Fx.error("internal", "Boom."))
        hub["GET /schedules"] = (502, "")
        let model = await loaded(hub, topics: NtfyTopicStore(defaults: .scratch()))
        XCTAssertEqual(model.usage, .failed("The hub had a problem. Try again in a minute."))
        XCTAssertEqual(model.schedules, .failed("The hub had a problem. Try again in a minute."))
        let status = StatusModel(service: hub.service, alerts: LastAlertStore(defaults: .scratch()))
        hub["GET /health"] = (500, "")
        await status.load()
        XCTAssertEqual(status.health, .failed("The hub had a problem. Try again in a minute."))
    }

    /// Fix 5: the topic is copied to this phone only, and expires.
    func testTheTopicIsCopiedToThisPhoneOnlyAndExpires() throws {
        let now = Date(timeIntervalSince1970: 1_000)
        let options = TopicClipboard.options(now: now)
        XCTAssertEqual(options[.localOnly] as? Bool, true, "never Universal Clipboard")
        XCTAssertEqual(options[.expirationDate] as? Date, now.addingTimeInterval(120))
        let board = try XCTUnwrap(UIPasteboard(name: UIPasteboard.Name("settings-tests"), create: true))
        TopicClipboard.copy("marcel-abc", to: board)
        XCTAssertEqual(board.string, "marcel-abc")
        UIPasteboard.remove(withName: board.name)
    }

    /// Fix 6: attempt 1 succeeds; its 0.6 s timer fires while attempt 2 waits and must not fail attempt 2.
    func testAnEarlierTimeoutNeverFailsALaterRegistration() async throws {
        let registrar = APNsRegistrar(authorize: { true }, register: {}, timeout: .milliseconds(600))
        async let first = registrar.token()
        try await Task.sleep(for: .milliseconds(50))
        registrar.didRegister(deviceToken: Data([0xAB]))
        let firstToken = try await first
        XCTAssertEqual(firstToken, "ab")

        try await Task.sleep(for: .milliseconds(250))  // t ≈ 0.3 s: start attempt 2
        let second = Task { try await registrar.token() }
        try await Task.sleep(for: .milliseconds(500))  // t ≈ 0.8 s: attempt 1's timer has fired
        registrar.didRegister(deviceToken: Data([0xCD]))
        let secondToken = try await second.value
        XCTAssertEqual(secondToken, "cd")
    }

    func testARegistrationThatGetsNoAnswerTimesOut() async {
        let registrar = APNsRegistrar(authorize: { true }, register: {}, timeout: .milliseconds(100))
        do {
            _ = try await registrar.token()
            XCTFail("no answer from Apple must time out")
        } catch {
            XCTAssertEqual(error as? PushSetupError, .apnsUnavailable)
        }
    }
}
