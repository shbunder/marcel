import AvatarKit
import Foundation
import MarcelKit
import PixelKit
import SwiftUI
import XCTest

@testable import MarcelSettings

/// S-17.6's UI tests, one layer down (XCUITest cannot run on this Mac's Xcode): People for the owner only, adding
/// Emma shows a QR, removing her says what happens and she is gone. Through the real generated client and a fake hub.
@MainActor
final class PeopleTests: XCTestCase {
    private func settings(_ hub: FakeHub) async -> SettingsModel {
        let model = SettingsModel(service: hub.service, tokens: FakeTokens(),
                                  topics: NtfyTopicStore(defaults: .scratch("people")))
        await model.load()
        return model
    }

    func testTheOwnerSeesPeopleAndAMemberDoesNot() async {
        let owner = await settings(FakeHub())
        XCTAssertTrue(owner.showsPeople)
        let hub = FakeHub()
        hub["GET /me"] = (200, Fx.json(Fx.me().merging(["role": "member", "name": "Emma"]) { $1 }))
        let member = await settings(hub)
        XCTAssertFalse(member.showsPeople, "a member never sees People")
        let older = FakeHub()
        older["GET /me"] = (200, Fx.json(Fx.me().filter { $0.key != "role" }))
        let noRole = await settings(older)
        XCTAssertFalse(noRole.showsPeople, "a hub that does not say: no People")
    }

    func testEveryoneIsListedWithTheirHelperPhonesAndQuests() async {
        let people = PeopleModel(service: FakeHub().service)
        await people.load()
        let list = people.people.value ?? []
        XCTAssertEqual(list.map(\.name), ["Shaun", "Emma"])
        XCTAssertEqual(PeopleContent.line(list[1]), "Helper: Pip · 1 phone · 3 quests this week")
        XCTAssertEqual(PeopleContent.line(list[0]), "Helper: Marcel · 2 phones · 12 quests this week")
    }

    func testTheOwnerAddsEmmaAndSeesAQR() async throws {
        let hub = FakeHub()
        hub["GET /users"] = (200, Fx.json([Fx.users[0]]))
        hub["POST /users"] = (201, Fx.json(Fx.users[1]))
        hub["POST /users/usr_emma/pairing-codes"] = (201, Fx.json(Fx.issued()))
        let people = PeopleModel(service: hub.service)
        await people.load()

        let empty = await people.add(name: "  ", helperName: "Pip")
        XCTAssertFalse(empty)
        XCTAssertEqual(people.problem, "Type their name and their helper's name.")

        hub["GET /users"] = (200, Fx.json(Fx.users))
        let added = await people.add(name: " Emma ", helperName: "Pip")
        XCTAssertTrue(added)
        XCTAssertEqual(hub.lastBody?["name"] as? String, "Emma", "trimmed")
        let code = try XCTUnwrap(people.code)
        XCTAssertEqual(code.person, "Emma")
        XCTAssertEqual(code.code, "R4T8WZ")
        XCTAssertNotNil(PeopleModel.qr(code.link), "a QR to scan")
        XCTAssertEqual(PeopleModel.timeLeft(code, now: Fx.now), "Expires in 10 min.")
        XCTAssertEqual(PeopleModel.timeLeft(code, now: Fx.now.addingTimeInterval(9 * 60 + 30)),
                       "Expires in under a minute.")
        XCTAssertEqual(PeopleModel.timeLeft(code, now: Fx.now.addingTimeInterval(11 * 60)),
                       "Expired: make a new code.")
        XCTAssertEqual(people.people.value?.map(\.name), ["Shaun", "Emma"], "the list follows")
    }

    /// Review fix: added, but the code failed: said, and no earlier code left on screen.
    func testAddedButNoCodeSaysSoAndLeavesNoOldCode() async throws {
        let hub = FakeHub()
        hub["POST /users/usr_emma/pairing-codes"] = (201, Fx.json(Fx.issued("OLD111")))
        let people = PeopleModel(service: hub.service)
        await people.load()
        await people.newCode(for: try XCTUnwrap(people.people.value?.last))
        XCTAssertEqual(people.code?.code, "OLD111")
        hub["POST /users"] = (201, Fx.json(Fx.users[1].merging(["id": "usr_lien", "name": "Lien"]) { $1 }))
        hub["POST /users/usr_lien/pairing-codes"] = (500, "")
        let added = await people.add(name: "Lien", helperName: "Bo")
        XCTAssertTrue(added, "Lien was added")
        XCTAssertNil(people.code, "Emma's old code is not shown as Lien's")
        XCTAssertEqual(people.problem,
                       "Lien was added, but the hub didn't make a code. Tap New code next to their name.")
    }

    func testRemovingEmmaSaysWhatHappensAndSheIsGone() async throws {
        let hub = FakeHub()
        hub["DELETE /users/usr_emma"] = (204, "")
        let people = PeopleModel(service: hub.service)
        await people.load()
        let emma = try XCTUnwrap(people.people.value?.last)
        XCTAssertEqual(PeopleModel.removalWords(emma),
                       "Emma's phones are signed out and Emma's helper, Pip, stops. Their chats and notes stay on the hub.")
        hub["GET /users"] = (200, Fx.json([Fx.users[0]]))
        await people.remove(emma)
        XCTAssertTrue(hub.requests.contains("DELETE /users/usr_emma"), "\(hub.requests)")
        XCTAssertEqual(people.removed, PeopleModel.removalWords(emma), "said again once done")
        XCTAssertEqual(people.people.value?.map(\.name), ["Shaun"], "gone from the list")
    }

    func testAMemberAskingForPeopleIsToldItIsTheOwners() async {
        let hub = FakeHub()
        hub["GET /users"] = (403, Fx.error("owner_only", "Only Shaun can manage people. Ask Shaun to do it."))
        let people = PeopleModel(service: hub.service)
        await people.load()
        XCTAssertEqual(people.people, .failed("Only Shaun can manage people. Ask Shaun to do it."))
    }

    // MARK: How it looks

    func testPeopleWithACode() async {
        let hub = FakeHub()
        hub["POST /users/usr_emma/pairing-codes"] = (201, Fx.json(Fx.issued(minutesLeft: 9)))
        let people = PeopleModel(service: hub.service)
        await people.load()
        await people.newCode(for: people.people.value![1])
        Snapshot.assert(
            VStack(spacing: 0) {
                SettingsHeader(title: "People", back: {})
                PeopleContent(model: people, now: Fx.now).padding(PixelMetrics.padding)
            }
            .environment(\.settingsStill, true),
            named: "People", variants: SettingsSnapshotTests.both, padding: 0)
    }

    func testPeopleAfterRemovingEmma() async throws {
        let hub = FakeHub()
        hub["DELETE /users/usr_emma"] = (204, "")
        let people = PeopleModel(service: hub.service)
        await people.load()
        let emma = try XCTUnwrap(people.people.value?.last)
        hub["GET /users"] = (200, Fx.json([Fx.users[0]]))
        await people.remove(emma)
        Snapshot.assert(
            VStack(spacing: 0) {
                SettingsHeader(title: "People", back: {})
                PeopleContent(model: people, now: Fx.now).padding(PixelMetrics.padding)
            }
            .environment(\.settingsStill, true),
            named: "People-removed", variants: [.light], padding: 0)
    }
}
