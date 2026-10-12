import XCTest
@testable import AvatarKit

/// A state change waits for the current frame to end, so the giraffe never jumps mid-blink.
final class BoundarySwitchTests: XCTestCase {
    // idle: frame 0 for 0–300 ms, frame 1 for 300–500 ms, frame 2 for 500–600 ms …
    // thinking starts with frame 25 (300 ms); working2 with 64; done with 0, then 121.

    func testSwitchWaitsForTheCurrentFrameToEnd() {
        var player = AvatarPlayer(state: .idle, at: 0)
        player.setState(.thinking, at: 0.350)  // mid frame 1, which ends at 500 ms
        XCTAssertEqual(player.loop(at: 0.350), "idle")
        XCTAssertEqual(player.frame(at: 0.350), 1, "still the current frame")
        XCTAssertEqual(player.frame(at: 0.499), 1, "still the current frame, a millisecond before the boundary")
        XCTAssertEqual(player.loop(at: 0.500), "thinking")
        XCTAssertEqual(player.frame(at: 0.500), 25, "the new loop starts on its first frame at the boundary")
        XCTAssertEqual(player.frame(at: 0.799), 25)
        XCTAssertEqual(player.frame(at: 0.800), 26, "the new loop's clock started at the boundary, not the request")
    }

    func testOnABoundaryTheSwitchIsImmediate() {
        var player = AvatarPlayer(state: .idle, at: 0)
        player.setState(.working(2), at: 0.300)
        XCTAssertNil(player.pending)
        XCTAssertEqual(player.frame(at: 0.300), 64)
    }

    func testAnotherChangeBeforeTheBoundaryReplacesTheTarget() {
        var player = AvatarPlayer(state: .idle, at: 0)
        player.setState(.thinking, at: 0.050)
        player.setState(.needsYou, at: 0.100)
        XCTAssertEqual(player.frame(at: 0.299), 0)
        XCTAssertEqual(player.loop(at: 0.300), "needs_you", "the boundary does not move, the target does")
        XCTAssertEqual(player.frame(at: 0.300), 109)
    }

    func testChangingBackBeforeTheBoundaryKeepsPlaying() {
        var player = AvatarPlayer(state: .idle, at: 0)
        player.setState(.done, at: 0.050)
        player.setState(.idle, at: 0.100)
        XCTAssertNil(player.pending)
        XCTAssertEqual(player.frame(at: 0.300), 1, "idle carries on without restarting")
    }

    func testSameLoopDoesNotRestart() {
        var player = AvatarPlayer(state: .working(3), at: 0)
        player.setState(.working(7), at: 0.250)  // clamps to working3, already playing
        XCTAssertEqual(player.frame(at: 0.250), SpriteSheet.giraffe.loop(named: "working3").frame(at: 250))
        XCTAssertNil(player.pending)
    }

    func testSwitchLaterInTheLoopAndSecondPass() {
        var player = AvatarPlayer(state: .idle, at: 10)
        player.setState(.done, at: 10 + 6.4 + 0.320)  // second pass, mid frame 1 (300–500 ms)
        XCTAssertEqual(player.loop(at: 10 + 6.4 + 0.499), "idle")
        XCTAssertEqual(player.frame(at: 10 + 6.4 + 0.5), 0, "done's first frame")
        XCTAssertEqual(player.frame(at: 10 + 6.4 + 0.7), 121, "done's second frame after 200 ms")
    }

    func testChainedSwitchesEachWaitForTheirOwnBoundary() {
        var player = AvatarPlayer(state: .idle, at: 0)
        player.setState(.thinking, at: 0.100)  // switches at 0.300
        player.setState(.done, at: 0.400)  // thinking frame 25 runs 0.300–0.600
        XCTAssertEqual(player.loop(at: 0.599), "thinking")
        XCTAssertEqual(player.loop(at: 0.600), "done")
    }
}
