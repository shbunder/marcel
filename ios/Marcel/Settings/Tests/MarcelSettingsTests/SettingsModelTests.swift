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

    // MARK: Name

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
        tokens.result = .success("a1b2c3")
        let model = await loaded(hub, tokens: tokens)
        await model.turnOnNotifications()
        XCTAssertEqual(model.push, .apns)
        XCTAssertEqual(hub.lastBody, ["kind": "apns", "apns_token": "a1b2c3"])
        XCTAssertEqual(model.devices.value?.first?.push, .apns)
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
        XCTAssertEqual(model.problem, "Built-in digests can be paused, not deleted.")
        XCTAssertFalse(hub.requests.contains("DELETE /schedules/sch_digest_morning"), "not even asked")
        await model.delete(report)
        XCTAssertEqual(model.schedules.value?.map(\.id), ["sch_digest_morning"])
        XCTAssertNil(model.problem)
    }

    func testAHubRefusingToDeleteADigestSaysWhy() async {
        let hub = FakeHub()
        hub["DELETE /schedules/sch_deps"] = (409, Fx.error("schedule_builtin", "Built-in."))
        let model = await loaded(hub)
        await model.delete(model.schedules.value![1])
        XCTAssertEqual(model.problem, "Built-in digests can be paused, not deleted.")
        XCTAssertEqual(model.schedules.value?.count, 2)
    }

    // MARK: Devices

    func testRevokeAnotherDeviceButNotThisOne() async {
        let hub = FakeHub()
        hub["DELETE /devices/dev_ipad"] = (204, "")
        let model = await loaded(hub)
        await model.revoke(model.devices.value![0])
        XCTAssertEqual(model.problem, "To remove this phone, use Unpair this phone.")
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

/// The review's fixes to Settings: unpairing tells the hub, a hub's own failure says so, the topic stays on this
/// phone, and an APNs timeout fails only its own attempt.
@MainActor
final class SettingsReviewFixTests: XCTestCase {
    private func loaded(_ hub: FakeHub, topics: NtfyTopicStore) async -> SettingsModel {
        let model = SettingsModel(service: hub.service, tokens: FakeTokens(), topics: topics, thisDevice: "dev_phone")
        await model.load()
        return model
    }

    /// Fix 2: unpairing revokes this phone on the hub (its token stops, pushes stop) and forgets the ntfy topic.
    func testUnpairingTellsTheHubAndForgetsTheTopic() async {
        let hub = FakeHub()
        hub["DELETE /devices/dev_phone"] = (204, "")
        let topics = NtfyTopicStore(defaults: .scratch())
        _ = topics.topic()
        let model = await loaded(hub, topics: topics)
        let note = await model.forgetThisPhone()
        XCTAssertNil(note)
        XCTAssertTrue(hub.requests.contains("DELETE /devices/dev_phone"), "\(hub.requests)")
        XCTAssertNil(topics.saved)
    }

    func testUnpairingWhenTheHubAlreadyForgotThisPhone() async {
        let hub = FakeHub()
        hub["DELETE /devices/dev_phone"] = (404, Fx.error("device_not_found", "No such device."))
        let model = await loaded(hub, topics: NtfyTopicStore(defaults: .scratch()))
        let note = await model.forgetThisPhone()
        XCTAssertNil(note)
    }

    func testUnpairingOfflineStillUnpairsAndSaysSo() async {
        let hub = FakeHub()
        let topics = NtfyTopicStore(defaults: .scratch())
        _ = topics.topic()
        let model = await loaded(hub, topics: topics)
        hub.offline = true
        let note = await model.forgetThisPhone()
        XCTAssertEqual(note, "Unpaired, but the hub wasn't told (it couldn't be reached), so it may still send to "
            + "this phone. Remove this phone under Devices on another phone.")
        XCTAssertTrue(hub.requests.contains("DELETE /devices/dev_phone"), "tried with the saved device id")
        XCTAssertNil(topics.saved, "the topic is forgotten either way")
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
