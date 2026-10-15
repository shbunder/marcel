import AvatarKit
import MarcelKit
import SwiftUI
import XCTest

@testable import MarcelChat

/// S-22.12 (B-49): the buddy sheet switches this phone with `PATCH /devices/{id}` and reloads the chat for the new
/// buddy; a long-press goes back to the one before; another phone's switch arrives as `device.updated`.
@MainActor
final class BuddySwitchTests: XCTestCase {
    private func model(_ hub: FakeService) -> (ChatModel, CurrentBuddy) {
        let name = "BuddySwitchTests-\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: name)!
        addTeardownBlock { defaults.removePersistentDomain(forName: name) }
        let buddy = CurrentBuddy(defaults: defaults)
        return (ChatModel(service: hub, buddy: buddy), buddy)
    }

    private func pip() -> BuddyChoice {
        BuddyChoice(id: "agt_pip", name: "Pip", lastMessage: "Your seedlings need water.")
    }

    func testTheSwitchSendsThePatchAndReloadsTheChatForTheNewBuddy() async {
        let hub = FakeService()
        hub.buddiesOnHub = [pip()]
        let (model, buddy) = model(hub)
        await model.start()
        XCTAssertEqual(model.conversationId, Fx.conversation)
        XCTAssertEqual(buddy.agentId, "agt_marcel")

        await model.switchTo("agt_pip")
        XCTAssertEqual(hub.switches.map(\.agentId), ["agt_pip"])
        XCTAssertEqual(hub.switches.map(\.deviceId), ["dev_me"], "this phone, as /me named it")
        XCTAssertEqual(model.agentId, "agt_pip")
        XCTAssertEqual(model.agentName, "Pip")
        XCTAssertEqual(model.conversationId, "cnv_main_agt_pip", "the chat now reads Pip's conversation")
        XCTAssertEqual(buddy.agentId, "agt_pip", "every tab follows CurrentBuddy")
        XCTAssertEqual(buddy.previousId, "agt_marcel")

        await model.switchTo("agt_pip")
        XCTAssertEqual(hub.switches.count, 1, "the buddy shown: nothing sent")
    }

    func testALongPressGoesBackToThePreviousBuddy() async {
        let hub = FakeService()
        hub.buddiesOnHub = [pip()]
        let (model, _) = model(hub)
        await model.start()
        await model.loadBuddies()
        XCTAssertNil(model.previousBuddyName, "nothing to go back to yet")
        await model.switchTo("agt_pip")
        XCTAssertEqual(model.previousBuddyName, "Marcel")
        await model.switchBack()
        XCTAssertEqual(model.agentId, "agt_marcel")
        XCTAssertEqual(hub.switches.map(\.agentId), ["agt_pip", "agt_marcel"])
        XCTAssertEqual(model.previousBuddyName, "Pip")
    }

    func testAFailedSwitchSaysSoAndStaysPut() async {
        let hub = FakeService()
        let (model, buddy) = model(hub)
        await model.start()
        hub.failing = true
        await model.switchTo("agt_pip")
        XCTAssertEqual(model.problem, "Couldn't switch: can't reach the hub.")
        XCTAssertEqual(model.agentId, "agt_marcel")
        XCTAssertEqual(buddy.agentId, "agt_marcel")
    }

    func testAnotherPhoneSwitchingThisOneArrivesAsDeviceUpdated() async throws {
        let hub = FakeService()
        hub.buddiesOnHub = [pip()]
        let (model, buddy) = model(hub)
        await model.start()
        hub.shown = "agt_pip"
        let device = ["id": "dev_other", "name": "iPad", "created_at": "2026-10-15T19:02:00Z", "agent_id": "agt_pip",
                      "push": "none"]
        model.receive(Fx.frame("device.updated", device, id: 5))
        XCTAssertEqual(buddy.agentId, "agt_marcel", "another phone's change is not ours")
        model.receive(Fx.frame("device.updated", device.merging(["id": "dev_me"]) { $1 }, id: 6))
        XCTAssertEqual(buddy.agentId, "agt_pip")
        for _ in 0..<50 where model.agentId != "agt_pip" { try await Task.sleep(for: .milliseconds(20)) }
        XCTAssertEqual(model.conversationId, "cnv_main_agt_pip")
    }

    func testNewBuddyIsAddedAndShown() async {
        let hub = FakeService()
        let (model, _) = model(hub)
        await model.start()
        await model.addBuddy(named: "  Bo ")
        XCTAssertEqual(model.agentName, "Bo")
        XCTAssertEqual(hub.switches.map(\.agentId), ["agt_bo"])
    }

    func testVoiceOverReadsEachBuddy() {
        XCTAssertEqual(BuddyRow.spoken(pip(), shown: false), "Pip. Last message: Your seedlings need water.")
        XCTAssertEqual(BuddyRow.spoken(BuddyChoice(id: "agt_marcel", name: "Marcel"), shown: true),
                       "Marcel. shown on this phone. No messages yet")
        var links = AvatarLinks()
        links.buddies = {}
        links.previous = ("Pip", {})
        XCTAssertEqual(links.hint, "Switches buddy. Touch and hold: back to Pip")
    }

    /// The buddy sheet in light and dark (the owner signs off on it, ADR-012): each buddy plays its own sheet.
    func testTheBuddySheet() async throws {
        let sheets = AvatarSheets(directory: nil) { _, _ in try OwnAvatarTests.fox() }
        await sheets.refresh(agentId: "agt_pip")
        let buddies = [
            BuddyChoice(id: "agt_marcel", name: "Marcel", lastMessage: "Dentist at 9:30, then nothing until pickup."),
            pip(),
            BuddyChoice(id: "agt_bo", name: "Bo"),
        ]
        Snapshot.assert(BuddyList(buddies: buddies, current: "agt_marcel",
                                  links: AvatarLinks(notebook: {}, settings: {}).named("Marcel"),
                                  pick: { _ in }, newBuddy: {})
                            .environment(\.chatStillAvatars, true)
                            .environment(\.chatAvatars, sheets),
                        named: "Buddy-sheet", variants: [.light, .dark], padding: 0)
    }
}
