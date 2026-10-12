import XCTest
@testable import AvatarKit

final class LoopTimingTests: XCTestCase {
    // Frames 7, 8, 9 for 100, 200 and 300 ms: one pass is 600 ms.
    let loop = SpriteLoop(steps: [.init(frame: 7, milliseconds: 100), .init(frame: 8, milliseconds: 200),
                                  .init(frame: 9, milliseconds: 300)])

    func testFrameAtTime() {
        XCTAssertEqual(loop.duration, 600)
        XCTAssertEqual(loop.frame(at: 0), 7)
        XCTAssertEqual(loop.frame(at: 99), 7)
        XCTAssertEqual(loop.frame(at: 100), 8)
        XCTAssertEqual(loop.frame(at: 299), 8)
        XCTAssertEqual(loop.frame(at: 300), 9)
        XCTAssertEqual(loop.frame(at: 599), 9)
    }

    func testLoopRepeats() {
        XCTAssertEqual(loop.frame(at: 600), 7)
        XCTAssertEqual(loop.frame(at: 700), 8)
        XCTAssertEqual(loop.frame(at: 6_000 + 350), 9)
    }

    func testNextBoundary() {
        XCTAssertEqual(loop.nextBoundary(atOrAfter: 0), 0)
        XCTAssertEqual(loop.nextBoundary(atOrAfter: 1), 100)
        XCTAssertEqual(loop.nextBoundary(atOrAfter: 100), 100)
        XCTAssertEqual(loop.nextBoundary(atOrAfter: 150), 300)
        XCTAssertEqual(loop.nextBoundary(atOrAfter: 301), 600)
        XCTAssertEqual(loop.nextBoundary(atOrAfter: 1_250), 1_300, "third pass starts at 1 200")
    }

    func testGiraffeIdleTimingMatchesTheSheet() {
        // giraffe.json: idle starts [0, 300], [1, 200], [2, 100] … and lasts 6.4 s.
        let idle = SpriteSheet.giraffe.loop(named: "idle")
        XCTAssertEqual(idle.duration, 6_400)
        XCTAssertEqual(idle.frame(at: 0), 0)
        XCTAssertEqual(idle.frame(at: 299), 0)
        XCTAssertEqual(idle.frame(at: 300), 1)
        XCTAssertEqual(idle.frame(at: 500), 2)
        XCTAssertEqual(idle.frame(at: 6_400), 0)
    }

    func testPlayerPlaysTheLoopFromWhenItStarted() {
        let player = AvatarPlayer(state: .idle, at: 1_000)
        XCTAssertEqual(player.frame(at: 1_000), 0)
        XCTAssertEqual(player.frame(at: 1_000.3), 1)
        XCTAssertEqual(player.frame(at: 1_000.55), 2)
        XCTAssertEqual(player.frame(at: 1_006.4), 0, "the loop wraps after 6.4 s")
    }
}
