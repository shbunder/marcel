import Foundation

/// Decides which frame the avatar shows at a moment in time. Pure and value-typed, so it can be tested without a
/// clock or a screen: the view feeds it `TimelineView`'s dates.
///
/// A state change does not cut the current loop: it waits for the current frame to end (the loop's next frame
/// boundary), then the new loop starts from its first frame.
public struct AvatarPlayer: Sendable {
    public let sheet: SpriteSheet
    /// The loop playing now, and when (seconds, any fixed reference) it started.
    public private(set) var loopName: String
    public private(set) var startedAt: TimeInterval
    /// A loop waiting for the current frame to end.
    public private(set) var pending: (loopName: String, at: TimeInterval)?

    public init(sheet: SpriteSheet = .giraffe, state: AvatarState, at time: TimeInterval) {
        self.sheet = sheet
        self.loopName = state.loopName
        self.startedAt = time
    }

    /// The state changed at `time`. The new loop starts at the current loop's next frame boundary.
    public mutating func setState(_ state: AvatarState, at time: TimeInterval) {
        settle(at: time)
        let target = state.loopName
        if target == loopName {
            pending = nil  // changed back before the switch happened: keep playing
            return
        }
        if pending != nil {
            pending!.loopName = target  // still waiting for the same boundary
            return
        }
        let elapsed = Self.milliseconds(time - startedAt)
        let boundary = sheet.loop(named: loopName).nextBoundary(atOrAfter: elapsed)
        pending = (target, startedAt + Double(boundary) / 1000)
        settle(at: time)  // already on a boundary: switch now
    }

    /// The frame index to show at `time`.
    public func frame(at time: TimeInterval) -> Int {
        var now = self
        now.settle(at: time)
        return now.sheet.loop(named: now.loopName).frame(at: Self.milliseconds(time - now.startedAt))
    }

    /// The loop playing at `time` (after any pending switch that is due).
    public func loop(at time: TimeInterval) -> String {
        var now = self
        now.settle(at: time)
        return now.loopName
    }

    private mutating func settle(at time: TimeInterval) {
        guard let next = pending, Self.milliseconds(time - next.at) >= 0 else { return }
        loopName = next.loopName
        startedAt = next.at
        pending = nil
    }

    static func milliseconds(_ seconds: TimeInterval) -> Int { Int((seconds * 1000).rounded()) }
}
