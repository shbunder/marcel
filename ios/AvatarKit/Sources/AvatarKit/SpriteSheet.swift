import CoreGraphics
import Foundation
import ImageIO

/// One loop of the sheet: [frame index, milliseconds] steps that repeat (`contracts/avatar-sheet.schema.json`).
public struct SpriteLoop: Sendable, Equatable {
    public struct Step: Sendable, Equatable {
        public var frame: Int
        public var milliseconds: Int
    }

    public let steps: [Step]
    /// One pass through the loop, in milliseconds.
    public let duration: Int

    public init(steps: [Step]) {
        precondition(!steps.isEmpty, "A sprite loop needs at least one frame.")
        self.steps = steps
        self.duration = steps.reduce(0) { $0 + max($1.milliseconds, 1) }
    }

    /// The first frame: what Reduce Motion shows.
    public var firstFrame: Int { steps[0].frame }

    /// The frame shown `ms` milliseconds after the loop started. The loop repeats.
    public func frame(at ms: Int) -> Int {
        var t = ms.mod(duration)
        for step in steps {
            let length = max(step.milliseconds, 1)
            if t < length { return step.frame }
            t -= length
        }
        return steps[0].frame
    }

    /// The first moment at or after `ms` (since the loop started) where one frame ends and the next begins.
    /// A state change waits for it, so the giraffe never jumps mid-blink.
    public func nextBoundary(atOrAfter ms: Int) -> Int {
        let pass = ms.floorDiv(duration) * duration
        var edge = pass
        if edge >= ms { return edge }
        for step in steps {
            edge += max(step.milliseconds, 1)
            if edge >= ms { return edge }
        }
        return pass + duration
    }
}

/// The avatar's sprite sheet: the PNG cut into frames, plus the loops from its JSON.
public final class SpriteSheet: Sendable {
    public struct LoadError: Error, CustomStringConvertible {
        public let description: String
    }

    public let name: String
    /// One frame, in sprite pixels (32 × 32 for every Pixel Zoo avatar).
    public let frameWidth: Int
    public let frameHeight: Int
    public let loops: [String: SpriteLoop]
    private let frames: [CGImage]

    /// Reads `<name>.json` and the PNG it names from `bundle`.
    public convenience init(named name: String, in bundle: Bundle) throws {
        guard let jsonURL = bundle.url(forResource: name, withExtension: "json") else {
            throw LoadError(description: "The avatar sheet \(name).json is missing from the app.")
        }
        let json = try Data(contentsOf: jsonURL)
        let info = try JSONDecoder().decode(SheetJSON.self, from: json)
        let pngURL = jsonURL.deletingLastPathComponent().appendingPathComponent(info.image)
        guard let source = CGImageSourceCreateWithURL(pngURL as CFURL, nil),
              let image = CGImageSourceCreateImageAtIndex(source, 0, nil)
        else { throw LoadError(description: "The avatar image \(info.image) is missing or unreadable.") }
        try self.init(info: info, image: image)
    }

    /// A sheet as the hub serves it (`GET /agents/{id}/avatar`): its JSON and its PNG, as bytes.
    public convenience init(json: Data, png: Data) throws {
        let info: SheetJSON
        do { info = try JSONDecoder().decode(SheetJSON.self, from: json) } catch {
            throw LoadError(description: "The avatar sheet's JSON could not be read: \(error)")
        }
        guard let source = CGImageSourceCreateWithData(png as CFData, nil),
              let image = CGImageSourceCreateImageAtIndex(source, 0, nil)
        else { throw LoadError(description: "The avatar image could not be read.") }
        try self.init(info: info, image: image)
    }

    init(info: SheetJSON, image: CGImage) throws {
        let w = info.frame.width, h = info.frame.height
        var frames: [CGImage] = []
        for i in 0..<info.frame_count {
            let rect = CGRect(x: (i % info.columns) * w, y: (i / info.columns) * h, width: w, height: h)
            guard let frame = image.cropping(to: rect) else {
                throw LoadError(description: "The avatar image is too small for frame \(i).")
            }
            frames.append(frame)
        }
        var loops: [String: SpriteLoop] = [:]
        for (loopName, steps) in info.loops {
            guard !steps.isEmpty, steps.allSatisfy({ $0.count == 2 && (0..<frames.count).contains($0[0]) }) else {
                throw LoadError(description: "The avatar loop \(loopName) points at a frame the image does not have.")
            }
            loops[loopName] = SpriteLoop(steps: steps.map { .init(frame: $0[0], milliseconds: $0[1]) })
        }
        guard loops["idle"] != nil else { throw LoadError(description: "The avatar sheet has no idle loop.") }
        self.name = info.name
        self.frameWidth = w
        self.frameHeight = h
        self.loops = loops
        self.frames = frames
    }

    /// Marcel's own avatar (giraffe, rose, professional), drawn by the Pixel Zoo and bundled: what plays until an
    /// agent's own sheet arrives, and offline.
    public static let giraffe: SpriteSheet = {
        do { return try SpriteSheet(named: "giraffe-rose-professional", in: .module) } catch {
            fatalError("AvatarKit could not load the giraffe: \(error)")
        }
    }()

    public var frameCount: Int { frames.count }

    /// Frame `index` as an image, `frameWidth` × `frameHeight` pixels.
    public func image(frame index: Int) -> CGImage { frames[index] }

    /// The loop that plays `state`. A loop the sheet lacks plays idle.
    public func loop(for state: AvatarState) -> SpriteLoop { loop(named: state.loopName) }

    public func loop(named loopName: String) -> SpriteLoop { loops[loopName] ?? loops["idle"]! }
}

/// The JSON next to the PNG, as `contracts/avatar-sheet.schema.json` describes it.
struct SheetJSON: Decodable {
    struct Frame: Decodable { var width: Int; var height: Int }
    var version: Int
    var name: String
    var image: String
    var frame: Frame
    var columns: Int
    var frame_count: Int
    var loops: [String: [[Int]]]
}

extension Int {
    /// Remainder that is never negative.
    func mod(_ n: Int) -> Int { ((self % n) + n) % n }
    /// Division that rounds down, also for negative numbers.
    func floorDiv(_ n: Int) -> Int { (self - mod(n)) / n }
}
