import SwiftUI
import XCTest
@testable import AvatarKit

/// The first frame of each state at every size, rendered by SwiftUI at 3× (an iPhone screen).
/// References live in __Snapshots__; a missing one is written and the test fails so it gets looked at and committed.
/// Re-record all with `SIMCTL_CHILD_AVATARKIT_RECORD=1 make -C ios test`.
@MainActor
final class SnapshotTests: XCTestCase {
    static let scale: CGFloat = 3
    static let loops = ["idle", "thinking", "working1", "working2", "working3", "needs_you", "done"]
    static let snapshots = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
        .appendingPathComponent("__Snapshots__")

    func testFirstFrameOfEveryStateAtEverySizeMatchesItsReference() throws {
        let record = ProcessInfo.processInfo.environment["AVATARKIT_RECORD"] == "1"
        var recorded: [String] = []
        for loop in Self.loops {
            for size in AvatarSize.allCases {
                let frame = SpriteSheet.giraffe.loop(named: loop).firstFrame
                let image = try render(frame: frame, size: size)
                let name = "\(loop)-\(Int(size.points))pt.png"
                let url = Self.snapshots.appendingPathComponent(name)
                if record || !FileManager.default.fileExists(atPath: url.path) {
                    try writePNG(image, to: url)
                    recorded.append(name)
                    continue
                }
                let reference = try XCTUnwrap(loadPNG(url), "could not read \(name)")
                XCTAssertEqual(image.width, reference.width, name)
                XCTAssertEqual(image.height, reference.height, name)
                XCTAssertTrue(rgba(image) == rgba(reference), "\(name) no longer matches its reference")
            }
        }
        XCTAssertTrue(recorded.isEmpty, "recorded new references, look at them and commit: \(recorded)")
    }

    /// Every rendered pixel is exactly one sprite pixel, repeated zoom × zoom times: no blur, no half pixels.
    func testRenderedPixelsAreCrispWholeNumberZoom() throws {
        let sheet = SpriteSheet.giraffe
        for loop in Self.loops {
            let frame = sheet.loop(named: loop).firstFrame
            let sprite = rgba(sheet.image(frame: frame))
            for size in AvatarSize.allCases {
                let image = try render(frame: frame, size: size)
                let zoom = size.zoom(frameWidth: 48, frameHeight: 54, displayScale: Self.scale)
                let box = Int(size.points * Self.scale)
                XCTAssertEqual(image.width, box)
                let ox = (box - 48 * zoom) / 2, oy = (box - 54 * zoom) / 2
                let pixels = rgba(image)
                var wrong = 0
                for y in 0..<box {
                    for x in 0..<box {
                        let sx = (x - ox).floorDiv(zoom), sy = (y - oy).floorDiv(zoom)
                        let inside = (0..<48).contains(sx) && (0..<54).contains(sy)
                        for c in 0..<4 {
                            let want = inside ? sprite[(sy * 48 + sx) * 4 + c] : 0
                            if abs(Int(pixels[(y * box + x) * 4 + c]) - Int(want)) > 2 { wrong += 1 }
                        }
                    }
                }
                XCTAssertEqual(wrong, 0, "\(loop) at \(Int(size.points)) pt has \(wrong) blurred or shifted channels")
            }
        }
    }

    func testZoomIsAWholeNumberThatFitsTheBox() {
        // 48 × 54 sprite on a 3× screen: 24 pt = 72 px, 32 pt = 96 px, 44 pt = 132 px, 120 pt = 360 px.
        XCTAssertEqual(AvatarSize.tiny.zoom(frameWidth: 48, frameHeight: 54, displayScale: 3), 1)
        XCTAssertEqual(AvatarSize.small.zoom(frameWidth: 48, frameHeight: 54, displayScale: 3), 1)
        XCTAssertEqual(AvatarSize.medium.zoom(frameWidth: 48, frameHeight: 54, displayScale: 3), 2)
        XCTAssertEqual(AvatarSize.large.zoom(frameWidth: 48, frameHeight: 54, displayScale: 3), 6)
        XCTAssertEqual(AvatarSize.large.zoom(frameWidth: 48, frameHeight: 54, displayScale: 2), 4)
        XCTAssertEqual(AvatarSize.tiny.zoom(frameWidth: 48, frameHeight: 54, displayScale: 2), 1, "never below 1")
    }

    func testOnlyAnimatesOnScreenActiveAndWithoutReduceMotion() {
        XCTAssertTrue(AvatarView.animates(onScreen: true, reduceMotion: false, scenePhase: .active))
        XCTAssertFalse(AvatarView.animates(onScreen: false, reduceMotion: false, scenePhase: .active), "off screen")
        XCTAssertFalse(AvatarView.animates(onScreen: true, reduceMotion: false, scenePhase: .background))
        XCTAssertFalse(AvatarView.animates(onScreen: true, reduceMotion: true, scenePhase: .active), "Reduce Motion")
    }

    func testReduceMotionShowsTheFirstFrameOfEachState() {
        for (state, first) in [(AvatarState.idle, 0), (.thinking, 25), (.working(1), 44), (.working(2), 64),
                               (.working(5), 86), (.needsYou, 109), (.done, 0)] {
            let player = AvatarPlayer(state: state, at: 0)
            for t in [0.0, 0.35, 1.7, 5.05] {
                XCTAssertEqual(AvatarView.frame(player: player, state: state, reduceMotion: true, at: t), first,
                               "Reduce Motion should hold frame \(first) for \(state)")
            }
        }
        // Without Reduce Motion the giraffe moves: idle is on frame 1 at 0.35 s.
        XCTAssertEqual(AvatarView.frame(player: AvatarPlayer(state: .idle, at: 0), state: .idle, reduceMotion: false,
                                        at: 0.35), 1)
        // With it, a state change shows the new state's first frame at once, not at the next boundary.
        var player = AvatarPlayer(state: .idle, at: 0)
        player.setState(.thinking, at: 0.35)
        XCTAssertEqual(AvatarView.frame(player: player, state: .thinking, reduceMotion: true, at: 0.35), 25)
    }

    func testAvatarViewDrawsAFrameOfItsStatesLoop() throws {
        // The live view reads the clock, so which frame it is on depends on how long rendering took. It must be one of
        // the needs_you loop's frames, drawn exactly like the still frame.
        let live = rgba(try render(AvatarView(state: .needsYou, size: .large), size: .large))
        let frames = Set(SpriteSheet.giraffe.loop(named: "needs_you").steps.map(\.frame))
        let matches = try frames.filter { rgba(try render(frame: $0, size: .large)) == live }
        XCTAssertFalse(matches.isEmpty, "AvatarView drew something that is not a needs_you frame")
    }

    // MARK: - Rendering

    private func render(frame: Int, size: AvatarSize) throws -> CGImage {
        try render(AvatarFrameView(frame: frame, size: size), size: size)
    }

    private func render(_ view: some View, size: AvatarSize) throws -> CGImage {
        let renderer = ImageRenderer(content: view.environment(\.displayScale, Self.scale)
            .frame(width: size.points, height: size.points))
        renderer.scale = Self.scale
        renderer.isOpaque = false
        return try XCTUnwrap(renderer.cgImage, "SwiftUI rendered nothing")
    }

    /// Straight RGBA8 in sRGB, so images from different sources compare byte for byte.
    private func rgba(_ image: CGImage) -> [UInt8] {
        var bytes = [UInt8](repeating: 0, count: image.width * image.height * 4)
        let context = CGContext(data: &bytes, width: image.width, height: image.height, bitsPerComponent: 8,
                                bytesPerRow: image.width * 4, space: CGColorSpace(name: CGColorSpace.sRGB)!,
                                bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
        context.interpolationQuality = .none
        context.draw(image, in: CGRect(x: 0, y: 0, width: image.width, height: image.height))
        return bytes
    }

    private func writePNG(_ image: CGImage, to url: URL) throws {
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        guard let dest = CGImageDestinationCreateWithURL(url as CFURL, "public.png" as CFString, 1, nil) else {
            throw CocoaError(.fileWriteUnknown)
        }
        CGImageDestinationAddImage(dest, image, nil)
        guard CGImageDestinationFinalize(dest) else { throw CocoaError(.fileWriteUnknown) }
    }

    private func loadPNG(_ url: URL) -> CGImage? {
        guard let source = CGImageSourceCreateWithURL(url as CFURL, nil) else { return nil }
        return CGImageSourceCreateImageAtIndex(source, 0, nil)
    }
}
