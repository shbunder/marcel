import AvatarKit
import Foundation
import MarcelKit

/// While the agent is free, the header plays a calm mood now and then: after a random 20–60 s gap, one pass of a mood
/// from `MoodBook`'s idle set (weighted by personality), then idle again. After a long quiet spell (ten minutes, five
/// for a cozy helper) it shows `sleepy` until something happens. A sheet without the mood loops stays idle.
///
/// Only the timing lives here, on an injected clock and random source; `AvatarHeader` drives it while the app is in
/// the foreground and Reduce Motion is off.
struct IdleMoods {
    static let gap: ClosedRange<TimeInterval> = 20...60

    let personality: LookPersonality?
    /// Whether the sheet has a loop.
    let has: (String) -> Bool
    /// One pass of a loop, in seconds.
    let duration: (String) -> TimeInterval

    private var idleSince: TimeInterval?
    private var nextAt: TimeInterval = 0
    private var playing: (loop: String, until: TimeInterval)?

    init(sheet: SpriteSheet, personality: LookPersonality?) {
        self.init(personality: personality, has: { sheet.loops[$0] != nil },
                  duration: { TimeInterval(sheet.loop(named: $0).duration) / 1000 })
    }

    init(personality: LookPersonality?, has: @escaping (String) -> Bool,
         duration: @escaping (String) -> TimeInterval) {
        self.personality = personality
        self.has = has
        self.duration = duration
    }

    var sleepyAfter: TimeInterval { MoodBook.sleepyAfter(personality) }

    /// The agent is (or is not) free at `now`. Returns the loop to play over idle (nil: plain idle) and when to ask
    /// again (nil: nothing changes until the agent's state does).
    mutating func update(now: TimeInterval, free: Bool,
                         random: inout some RandomNumberGenerator) -> (loop: String?, wake: TimeInterval?) {
        guard free else {
            idleSince = nil
            playing = nil
            return (nil, nil)
        }
        if idleSince == nil {
            idleSince = now
            nextAt = now + TimeInterval.random(in: Self.gap, using: &random)
        }
        let sleepy = MoodBook.loop("sleepy")
        let sleepyAt = (idleSince ?? now) + sleepyAfter
        if has(sleepy), now >= sleepyAt {
            playing = nil
            return (sleepy, nil)
        }
        let sleepyWake: TimeInterval? = has(sleepy) ? sleepyAt : nil
        func soonest(_ times: TimeInterval?...) -> TimeInterval? { times.compactMap { $0 }.min() }
        if let current = playing {
            if now < current.until { return (current.loop, soonest(current.until, sleepyWake)) }
            playing = nil
            nextAt = now + TimeInterval.random(in: Self.gap, using: &random)
        }
        if now >= nextAt {
            let loop = MoodBook.loop(MoodBook.pick(.idle, personality: personality, random: &random))
            guard has(loop) else {
                // A sheet with some moods but not this one: try again after another gap.
                nextAt = now + TimeInterval.random(in: Self.gap, using: &random)
                return (nil, soonest(nextAt, sleepyWake))
            }
            let until = now + max(duration(loop), PoseGate.hold)
            playing = (loop, until)
            return (loop, soonest(until, sleepyWake))
        }
        return (nil, soonest(nextAt, sleepyWake))
    }
}

/// A small seeded random source (SplitMix64) so tests pick the same moods every run.
struct SeededRandom: RandomNumberGenerator {
    var state: UInt64

    mutating func next() -> UInt64 {
        state &+= 0x9E37_79B9_7F4A_7C15
        var z = state
        z = (z ^ (z >> 30)) &* 0xBF58_476D_1CE4_E5B9
        z = (z ^ (z >> 27)) &* 0x94D0_49BB_1331_11EB
        return z ^ (z >> 31)
    }
}
