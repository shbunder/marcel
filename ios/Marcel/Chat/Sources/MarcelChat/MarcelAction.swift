import AvatarKit
import Foundation

/// What the agent is doing right now, as the chat header shows it: a Pixel Zoo pose and what VoiceOver reads.
///
/// Built from the hub's `agent.state` (the contract's kinds), refined by the last tool a task called (`task.event`
/// `tool_call`) and a recent `task.event` `error`. Precedence: waiting for you, then a problem, then reading,
/// writing or working, then thinking, then done, then idle.
public enum MarcelAction: Equatable, Sendable {
    case idle
    case thinking
    /// `n` working tasks, the last tool not one of the reading or writing ones.
    case working(Int)
    /// Editing or writing files: the laptop pose, said as "writing".
    case writing(Int)
    case reading
    case waiting
    case done
    /// Something went wrong a moment ago; shown for `MarcelAction.errorFor`.
    case error

    /// How long a problem stays in the header.
    public static let errorFor: TimeInterval = 5

    /// What a tool call says Marcel is doing, by the tool's name (Claude Code's built-in tools).
    public enum ToolKind: Equatable, Sendable {
        case reading, writing

        public init?(tool: String) {
            switch tool {
            case "Read", "Grep", "Glob", "LS", "WebSearch", "WebFetch": self = .reading
            case "Edit", "Write", "MultiEdit", "NotebookEdit": self = .writing
            default: return nil
            }
        }
    }

    /// From the hub's state, the last tool's kind (nil: another tool, or none yet) and whether a problem is recent.
    public init(agent: AvatarState, tool: ToolKind? = nil, failed: Bool = false) {
        if agent == .needsYou {
            self = .waiting
        } else if failed || agent == .error {
            self = .error
        } else {
            switch agent {
            case .working(let n):
                switch tool {
                case .reading: self = .reading
                case .writing: self = .writing(n)
                case nil: self = .working(n)
                }
            case .reading: self = .reading
            case .thinking: self = .thinking
            case .done: self = .done
            default: self = .idle
            }
        }
    }

    /// The pose the avatar plays.
    public var avatar: AvatarState {
        switch self {
        case .idle: .idle
        case .thinking: .thinking
        case .working(let n), .writing(let n): .working(n)
        case .reading: .reading
        case .waiting: .needsYou
        case .done: .done
        case .error: .error
        }
    }

    /// What VoiceOver reads: "Marcel, working on: reading and searching".
    public func spoken(name: String) -> String {
        switch self {
        case .idle: "\(name), free"
        case .thinking: "\(name), working on: thinking"
        case .working(let n): n <= 1 ? "\(name), working on: 1 task" : "\(name), working on: \(n) tasks"
        case .writing: "\(name), working on: writing"
        case .reading: "\(name), working on: reading and searching"
        case .waiting: "\(name), waiting for you"
        case .done: "\(name), done"
        case .error: "\(name), ran into a problem"
        }
    }
}

/// Keeps the header calm: a new pose shows only once the current one has been up for `hold` seconds. A pose asked
/// for sooner waits (the newest wish wins); one that is already showing just stays.
struct PoseGate {
    static let hold: TimeInterval = 2

    private(set) var shown: MarcelAction?
    private(set) var pending: MarcelAction?
    private var since: TimeInterval = -.infinity

    /// Asks for `action` at time `now`. Returns how many seconds until the pending pose may show, or nil when
    /// nothing waits.
    mutating func want(_ action: MarcelAction, at now: TimeInterval) -> TimeInterval? {
        if action == shown {
            pending = nil
            return nil
        }
        let left = since + Self.hold - now
        guard left > 0 else {
            shown = action
            since = now
            pending = nil
            return nil
        }
        pending = action
        return left
    }

}
