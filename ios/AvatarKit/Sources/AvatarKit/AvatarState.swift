/// What the avatar shows (B-28), as the hub sends it in `agent.state` (`AgentState` in contracts/app-api.yaml).
public enum AvatarState: Hashable, Sendable {
    case idle
    case thinking
    /// `n` is the number of working tasks. The giraffe shows one glowing spot per task, at most 3.
    case working(Int)
    case needsYou
    case done

    /// Builds the state from the contract's fields: `kind` (`idle | thinking | working | needs_you | done`) and
    /// `active_tasks`. A kind this version does not know shows `idle`, so a newer hub never breaks the avatar.
    public init(kind: String, activeTasks: Int? = nil) {
        switch kind {
        case "thinking": self = .thinking
        case "working": self = .working(activeTasks ?? 1)
        case "needs_you": self = .needsYou
        case "done": self = .done
        default: self = .idle
        }
    }

    /// The loop in the sprite sheet (`contracts/avatar-sheet.schema.json`) that plays this state.
    /// `working(n)` clamps n to 1…3: the sheet has `working1`, `working2` and `working3`.
    public var loopName: String {
        switch self {
        case .idle: "idle"
        case .thinking: "thinking"
        case .working(let n): "working\(min(max(n, 1), 3))"
        case .needsYou: "needs_you"
        case .done: "done"
        }
    }

    /// What VoiceOver reads for the avatar.
    public var accessibilityDescription: String {
        switch self {
        case .idle: "Marcel is free"
        case .thinking: "Marcel is thinking"
        case .working(let n): n == 1 ? "Marcel is working on 1 task" : "Marcel is working on \(max(n, 0)) tasks"
        case .needsYou: "Marcel needs you"
        case .done: "Marcel is done"
        }
    }
}
