import XCTest
@testable import AvatarKit

final class AvatarStateTests: XCTestCase {
    func testEachStatePlaysItsLoop() {
        XCTAssertEqual(AvatarState.idle.loopName, "idle")
        XCTAssertEqual(AvatarState.thinking.loopName, "thinking")
        XCTAssertEqual(AvatarState.working(1).loopName, "working1")
        XCTAssertEqual(AvatarState.working(2).loopName, "working2")
        XCTAssertEqual(AvatarState.working(3).loopName, "working3")
        XCTAssertEqual(AvatarState.needsYou.loopName, "needs_you")
        XCTAssertEqual(AvatarState.done.loopName, "done")
    }

    func testWorkingClampsToOneThroughThree() {
        XCTAssertEqual(AvatarState.working(0).loopName, "working1")
        XCTAssertEqual(AvatarState.working(-2).loopName, "working1")
        XCTAssertEqual(AvatarState.working(4).loopName, "working3")
        XCTAssertEqual(AvatarState.working(12).loopName, "working3")
    }

    func testBuildsFromTheContractsAgentState() {
        // contracts/app-api.yaml AgentState.kind and events.schema.json's agent.state example.
        XCTAssertEqual(AvatarState(kind: "idle"), .idle)
        XCTAssertEqual(AvatarState(kind: "thinking"), .thinking)
        XCTAssertEqual(AvatarState(kind: "working", activeTasks: 2), .working(2))
        XCTAssertEqual(AvatarState(kind: "needs_you"), .needsYou)
        XCTAssertEqual(AvatarState(kind: "done"), .done)
        XCTAssertEqual(AvatarState(kind: "dancing"), .idle, "an unknown kind shows idle")
    }

    func testEveryLoopExistsInTheSheet() {
        let states: [AvatarState] = [.idle, .thinking, .working(1), .working(2), .working(3), .needsYou, .done]
        for state in states {
            XCTAssertNotNil(SpriteSheet.giraffe.loops[state.loopName], "the sheet has no \(state.loopName) loop")
        }
    }

    func testSheetLoadsFromThePackageBundle() {
        let sheet = SpriteSheet.giraffe
        XCTAssertEqual(sheet.frameWidth, 48)
        XCTAssertEqual(sheet.frameHeight, 54)
        XCTAssertEqual(sheet.frameCount, 129)
        XCTAssertEqual(sheet.image(frame: 128).width, 48)
        XCTAssertEqual(sheet.image(frame: 128).height, 54)
    }

    func testVoiceOverReadsTheState() {
        XCTAssertEqual(AvatarState.working(2).accessibilityDescription, "Marcel is working on 2 tasks")
        XCTAssertEqual(AvatarState.needsYou.accessibilityDescription, "Marcel needs you")
    }
}
