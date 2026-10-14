import AvatarKit
import Foundation
import MarcelKit

/// Which Pixel Zoo moods the header picks from for what happens (the owner's table). A pick plays the sheet's
/// `mood_<name>` loop; a sheet without it plays the state's own loop instead.
///
/// Some moods hold while their state lasts (needs you, done, a problem); the rest play one pass over the current
/// pose (`Flourish`). The helper's personality weights each pick: its favourite moods count three times.
enum MoodBook {
    /// What happened.
    enum Situation: Equatable, Sendable {
        case needsYou, done, problem
        case longDone, stopped, longStopped, denied
        case manyQuests, listening, thanks, greeting
        case idle
    }

    /// A quest that ran this long counts as long.
    static let long: TimeInterval = 600

    static func moods(_ situation: Situation, personality: LookPersonality?) -> [String] {
        switch situation {
        case .needsYou: ["surprised", "shocked", "idea"]
        case .done: ["happy", "laughing", "proud", "excited"]
        case .problem:
            // Only a grumpy helper ever gets angry.
            ["embarrassed", "grumpy", "confused", "sad", "worried"] + (personality == .grumpy ? ["angry"] : [])
        case .longDone: ["calm", "proud", "love"]
        case .stopped: ["sad", "embarrassed"]
        case .longStopped: ["crying", "sad"]
        case .denied: ["sad", "embarrassed", "grumpy"]
        case .manyQuests: ["dizzy"]
        case .listening: ["focused"]
        case .thanks: ["love", "kiss", "happy"]
        case .greeting: ["happy", "excited", "love"]
        case .idle: ["calm", "happy", "wink", "singing", "proud", "idea", "love", "kiss", "neutral"]
        }
    }

    /// The moods a personality picks three times as often (when the situation offers them).
    static func favourites(_ personality: LookPersonality?) -> Set<String> {
        switch personality {
        case .grumpy: ["grumpy", "angry", "proud"]
        case .cheerful: ["laughing", "singing", "excited"]
        case .shy: ["embarrassed", "love", "calm"]
        case .bold: ["proud", "excited", "laughing"]
        case .cool: ["wink", "proud", "calm"]
        case .cozy: ["calm", "sleepy", "singing"]
        case .curious: ["idea", "confused", "focused"]
        case .sporty: ["excited", "laughing", "proud"]
        case .professional, nil: ["proud", "focused", "calm"]
        }
    }

    /// How long a helper stays free before it gets sleepy: a cozy one sooner.
    static func sleepyAfter(_ personality: LookPersonality?) -> TimeInterval {
        personality == .cozy ? 300 : 600
    }

    /// One mood for `situation`, weighted by `personality`.
    static func pick(_ situation: Situation, personality: LookPersonality?,
                     random: inout some RandomNumberGenerator) -> String {
        let moods = moods(situation, personality: personality)
        let liked = favourites(personality)
        let weighted = moods.flatMap { liked.contains($0) ? [$0, $0, $0] : [$0] }
        return weighted.randomElement(using: &random) ?? moods[0]
    }

    /// The loop that plays `mood` in a sheet.
    static func loop(_ mood: String) -> String { "mood_\(mood)" }

    /// The situation whose mood holds while the header shows `action`, if any.
    static func holding(_ action: MarcelAction) -> Situation? {
        switch action {
        case .waiting: .needsYou
        case .done: .done
        case .error: .problem
        default: nil
        }
    }

    /// Your message, said as a thank-you ("thanks", "thank you", "merci", "dank je", a heart)?
    static func isThanks(_ text: String) -> Bool {
        let lower = text.lowercased()
        return ["thank", "thx", "merci", "dank", "❤️", "♥️", "🙏"].contains { lower.contains($0) }
    }
}
