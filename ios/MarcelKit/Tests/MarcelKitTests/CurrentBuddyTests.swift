import XCTest

@testable import MarcelKit

@MainActor
final class CurrentBuddyTests: XCTestCase {
    private func fresh() -> (CurrentBuddy, UserDefaults) {
        let name = "CurrentBuddyTests-\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: name)!
        addTeardownBlock { defaults.removePersistentDomain(forName: name) }
        return (CurrentBuddy(defaults: defaults), defaults)
    }

    func testASwitchRemembersThePreviousBuddyAcrossLaunches() {
        let (buddy, defaults) = fresh()
        XCTAssertNil(buddy.agentId)
        buddy.show("agt_marcel")
        buddy.show("agt_pip")
        XCTAssertEqual(buddy.agentId, "agt_pip")
        XCTAssertEqual(buddy.previousId, "agt_marcel")
        buddy.show("agt_pip")
        XCTAssertEqual(buddy.previousId, "agt_marcel", "showing the same buddy again changes nothing")

        let again = CurrentBuddy(defaults: defaults)
        XCTAssertEqual(again.agentId, "agt_pip", "the next launch starts on it, offline too")
        XCTAssertEqual(again.previousId, "agt_marcel")
        again.forget()
        XCTAssertNil(CurrentBuddy(defaults: defaults).agentId)
    }

    func testOnlyThisPhonesDeviceUpdateSwitchesIt() {
        let (buddy, _) = fresh()
        buddy.show("agt_marcel")
        buddy.apply(deviceId: "dev_other", agentId: "agt_pip", thisDevice: "dev_me")
        XCTAssertEqual(buddy.agentId, "agt_marcel")
        buddy.apply(deviceId: "dev_me", agentId: "agt_pip", thisDevice: "dev_me")
        XCTAssertEqual(buddy.agentId, "agt_pip")
    }
}
