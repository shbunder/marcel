import AvatarKit
import MarcelItems
import MarcelKit
import PixelKit
import SwiftUI

/// What the top strip says: the giraffe's state, how many quests run and how many things need you.
///
/// The giraffe follows the hub's `agent.state` events (`ChatModel.agentState`, B-28): the hub knows more than the
/// phone (it is `thinking` while the brain runs a turn, `done` for 5 s after a completion). Until the hub has said,
/// and while the live stream is down, it is worked out from the cached tasks instead, so it is right offline too.
public struct ChatHeaderState: Equatable, Sendable {
    /// The agent's name: `AgentName.fallback` until the hub says otherwise (Settings can rename it).
    public var name: String = AgentName.fallback
    public var avatar: AvatarState
    public var running: Int
    public var needsYou: Int
    /// Set when the live stream is down: what the person should know, in plain words.
    public var offline: String?

    public init(avatar: AvatarState = .idle, running: Int = 0, needsYou: Int = 0, offline: String? = nil,
                name: String? = nil) {
        self.name = name ?? AgentName.fallback
        self.avatar = avatar
        self.running = running
        self.needsYou = needsYou
        self.offline = offline
    }

    /// From the cached tasks, and the hub's own word on the avatar (`live`, the last `agent.state`) when the stream
    /// is up. Without it, the contract's precedence (`AgentState`): needs you, then working(n), then idle.
    public init(tasks: some Collection<HubTask>, offline: String? = nil, name: String? = nil,
                live: AvatarState? = nil) {
        let needsYou = tasks.filter { $0.state == .needsYou && $0.archivedAt == nil }.count
        let working = tasks.filter { ($0.state == .working || $0.state == .starting) && $0.archivedAt == nil }.count
        let fromTasks: AvatarState = needsYou > 0 ? .needsYou : working > 0 ? .working(working) : .idle
        let avatar = (offline == nil ? live : nil) ?? fromTasks
        self.init(avatar: avatar, running: working, needsYou: needsYou, offline: offline, name: name)
    }

    public var subtitle: String {
        if let offline { return offline }
        switch running {
        case 0: return "Nothing running"
        case 1: return "1 quest running"
        default: return "\(running) quests running"
        }
    }

    /// What VoiceOver reads for the whole header: "Marcel is thinking. Nothing running. 1 thing needs you".
    public var accessibilityText: String {
        var parts = [avatar.accessibilityDescription(name: name), subtitle]
        switch needsYou {
        case 0: break
        case 1: parts.append("1 thing needs you")
        default: parts.append("\(needsYou) things need you")
        }
        return parts.joined(separator: ". ")
    }
}

/// The avatar header (S-13.3): the giraffe in a 120-pt box while you are at the latest messages, shrinking to 44 pt
/// once you scroll back or type. Hearts and the "needs you" count on the right (PixelKit's `HeartsHeader`).
///
/// `collapse` runs from 0 (120 pt) to 1 (44 pt) and is animatable, so the strip's height shrinks smoothly while the
/// sprite inside steps down whole zooms (6×, 5×, 4×, 3×, 2× on a 3× screen): every sprite pixel stays a sharp square
/// at every moment of the animation, never a blurred in-between. With Reduce Motion there is no animation (the
/// screen sets none) and the giraffe shows its state's first frame.
struct AvatarHeader: View, Animatable {
    static let expanded: CGFloat = AvatarSize.large.points
    static let collapsed: CGFloat = AvatarSize.medium.points

    var state: ChatHeaderState
    /// 0 is the big giraffe, 1 the small one; anything in between during the animation.
    var collapse: CGFloat

    @Environment(\.chatStillAvatars) private var still
    @Environment(\.dynamicTypeSize) private var dynamicType

    init(state: ChatHeaderState, collapse: CGFloat) {
        self.state = state
        self.collapse = collapse
    }

    init(state: ChatHeaderState, collapsed: Bool) {
        self.init(state: state, collapse: collapsed ? 1 : 0)
    }

    nonisolated var animatableData: CGFloat {
        get { collapse }
        set { collapse = newValue }
    }

    /// The avatar's box at `collapse`, in points: 120 at 0, 44 at 1, straight in between.
    static func box(collapse: CGFloat) -> CGFloat {
        let t = min(max(collapse, 0), 1)
        return expanded + (collapsed - expanded) * t
    }

    var body: some View {
        // At accessibility text sizes the big giraffe would squeeze the title; it stays at 44 pt there.
        let box = dynamicType.isAccessibilitySize ? Self.collapsed : Self.box(collapse: collapse)
        HeartsHeader(title: state.name, subtitle: state.subtitle, needsYou: state.needsYou) {
            ChatAvatar(state: state.avatar, box: box, still: still, name: state.name)
        }
        .accessibilityLabel(state.accessibilityText)
    }
}

/// The giraffe, animated in the app; one still frame in snapshots, where nothing may move. AvatarKit draws it at a
/// whole-number zoom with nearest-neighbour scaling; with Reduce Motion it holds the state's first frame.
struct ChatAvatar: View {
    var state: AvatarState
    var box: CGFloat
    var still: Bool
    var name: String = AgentName.fallback

    init(state: AvatarState, size: AvatarSize, still: Bool, name: String = AgentName.fallback) {
        self.init(state: state, box: size.points, still: still, name: name)
    }

    init(state: AvatarState, box: CGFloat, still: Bool, name: String = AgentName.fallback) {
        self.state = state
        self.box = box
        self.still = still
        self.name = name
    }

    var body: some View {
        if still {
            AvatarFrameView(frame: SpriteSheet.giraffe.loop(for: state).firstFrame, box: box)
        } else {
            AvatarView(state: state, box: box, name: name)
        }
    }
}

extension EnvironmentValues {
    /// Snapshot tests draw every avatar as its state's first frame, and the composer's field as plain text.
    @Entry public var chatStillAvatars = false
}
