import AvatarKit
import MarcelLook
import Foundation
import MarcelKit
import PixelKit
import SwiftUI
import XCTest

@testable import MarcelSettings

/// Settings › Buddies (S-22.12, B-49), through the real generated client and a fake hub: every live buddy listed,
/// renamed, archived after saying what happens, and a code for another phone that can start on one buddy.
@MainActor
final class BuddiesTests: XCTestCase {
    private func buddies(_ hub: FakeHub, looks: (any LookService)? = nil) -> BuddiesModel {
        hub["GET /me"] = (200, Fx.json(Fx.meWithPip()))
        let shown = CurrentBuddy(defaults: .scratch("buddies-\(UUID().uuidString)"))
        shown.show("agt_marcel")
        return BuddiesModel(service: hub.service, looks: looks, buddy: shown)
    }

    func testEveryLiveBuddyIsListedAndTheShownOneIsMarked() async {
        let hub = FakeHub()
        let model = buddies(hub)
        await model.load()
        XCTAssertEqual(model.buddies.value?.map(\.name), ["Marcel", "Pip"])
        XCTAssertEqual(model.shown, "agt_marcel")
    }

    func testRenameSendsTheNewName() async {
        let hub = FakeHub()
        hub["PATCH /agents/agt_pip"] = (200, Fx.json(Fx.pip.merging(["name": "Pippa"]) { $1 }))
        let model = buddies(hub)
        await model.load()
        let pip = try! XCTUnwrap(model.buddies.value?.last)
        let renamed = await model.rename(pip, to: "  Pippa ")
        XCTAssertTrue(renamed)
        XCTAssertTrue(hub.requests.contains("PATCH /agents/agt_pip"))
        XCTAssertEqual(hub.lastBody?["name"] as? String, "Pippa")
        let same = await model.rename(pip, to: "Pip")
        XCTAssertFalse(same, "the same name sends nothing")
    }

    func testArchivingSaysTheChatStaysReadableAndDropsTheBuddy() async {
        let hub = FakeHub()
        hub["POST /agents/agt_pip/archive"] = (200, Fx.json(Fx.pip.merging(["archived_at": Fx.at(0)]) { $1 }))
        let model = buddies(hub)
        await model.load()
        let pip = try! XCTUnwrap(model.buddies.value?.last)
        XCTAssertEqual(BuddiesModel.archiveWords(pip),
                       "Pip stops working and leaves your buddies. Its chat and notebook stay readable.")
        hub["GET /me"] = (200, Fx.json(Fx.me()))
        await model.archive(pip)
        XCTAssertEqual(model.archived, "Pip is archived. Its chat and notebook stay readable.")
        XCTAssertEqual(model.buddies.value?.map(\.name), ["Marcel"])
    }

    func testTheLastBuddyCannotBeArchived() async {
        let hub = FakeHub()
        hub["POST /agents/agt_marcel/archive"] = (409, Fx.error("last_buddy", "Marcel is your only buddy."))
        let model = buddies(hub)
        await model.load()
        await model.archive(try! XCTUnwrap(model.buddies.value?.first))
        XCTAssertEqual(model.problem, "Marcel is your only buddy.")
        XCTAssertNil(model.archived)
    }

    func testACodeForAnotherPhoneCanStartOnABuddy() async throws {
        let hub = FakeHub()
        hub["POST /pairing-codes"] = (201, Fx.json(Fx.issued()))
        let model = buddies(hub)
        await model.load()

        await model.newCode()
        XCTAssertEqual(hub.requests.last, "POST /pairing-codes")
        XCTAssertNil(hub.lastBody?["agent_id"], "no buddy named: the first live buddy")
        XCTAssertNil(model.code?.startsOn)

        model.startOn = "agt_pip"
        await model.newCode()
        XCTAssertEqual(hub.lastBody?["agent_id"] as? String, "agt_pip")
        let code = try XCTUnwrap(model.code)
        XCTAssertEqual(code.startsOn, "Pip")
        XCTAssertEqual(CodeCard.words(code), "On the other phone, open Marcel and scan this, or send the link. "
                                             + "It starts on Pip.")
        XCTAssertNotNil(BuddiesModel.qr(code.link), "a QR to scan")
        XCTAssertEqual(BuddiesModel.timeLeft(code, now: Fx.now), "Expires in 10 min.")
        XCTAssertEqual(BuddiesModel.timeLeft(code, now: Fx.now.addingTimeInterval(11 * 60)),
                       "Expired: make a new code.")
        model.startOn = nil
        XCTAssertNil(model.code, "another pick takes the old code off the screen")
    }

    func testTheBuddiesScreen() async {
        let hub = FakeHub()
        hub["POST /pairing-codes"] = (201, Fx.json(Fx.issued()))
        let model = buddies(hub, looks: ZooPreviews())
        await model.load()
        model.startOn = "agt_pip"
        await model.newCode()
        Snapshot.assert(BuddiesContent(model: model, now: Fx.now)
                            .padding(PixelMetrics.padding)
                            .environment(\.settingsStill, true),
                        named: "Buddies", variants: [.light, .dark], padding: 0)
    }
}
