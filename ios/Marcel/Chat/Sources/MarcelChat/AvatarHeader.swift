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
    /// Whose avatar the header plays (B-44); nil plays the bundled giraffe.
    public var agentId: String?
    public var avatar: AvatarState
    public var running: Int
    public var needsYou: Int
    /// Set when the live stream is down: what the person should know, in plain words.
    public var offline: String?

    public init(avatar: AvatarState = .idle, running: Int = 0, needsYou: Int = 0, offline: String? = nil,
                name: String? = nil, agentId: String? = nil) {
        self.name = name ?? AgentName.fallback
        self.agentId = agentId
        self.avatar = avatar
        self.running = running
        self.needsYou = needsYou
        self.offline = offline
    }

    /// From the cached tasks, and the hub's own word on the avatar (`live`, the last `agent.state`) when the stream
    /// is up. Without it, the contract's precedence (`AgentState`): needs you, then working(n), then idle.
    public init(tasks: some Collection<HubTask>, offline: String? = nil, name: String? = nil,
                live: AvatarState? = nil, agentId: String? = nil) {
        let needsYou = tasks.filter { $0.state == .needsYou && $0.archivedAt == nil }.count
        let working = tasks.filter { ($0.state == .working || $0.state == .starting) && $0.archivedAt == nil }.count
        let fromTasks: AvatarState = needsYou > 0 ? .needsYou : working > 0 ? .working(working) : .idle
        let avatar = (offline == nil ? live : nil) ?? fromTasks
        self.init(avatar: avatar, running: working, needsYou: needsYou, offline: offline, name: name, agentId: agentId)
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
/// once you scroll back or type. In the chat's language (wireframe Q1 v10, the S-18.6 follow-up): a white strip with
/// a thin line under it, the name in bold, what is running in the muted colour, and a gold badge when something needs
/// you.
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
    @Environment(\.colorScheme) private var scheme

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
        let palette = ChatPalette(scheme)
        HStack(alignment: .center, spacing: 10) {
            ChatAvatar(state: state.avatar, box: box, still: still, name: state.name, agentId: state.agentId)
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 2) {
                Text(state.name)
                    .font(.headline)
                    .foregroundStyle(palette.ink.color)
                    .fixedSize(horizontal: false, vertical: true)
                Text(state.subtitle)
                    .font(.footnote)
                    .foregroundStyle(palette.muted.color)
                    .fixedSize(horizontal: false, vertical: true)
                // At accessibility text sizes the badge goes under the name, which keeps the whole width.
                if dynamicType.isAccessibilitySize, let badge = Self.badge(needsYou: state.needsYou) {
                    ChatBadge(badge)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            if !dynamicType.isAccessibilitySize, let badge = Self.badge(needsYou: state.needsYou) {
                ChatBadge(badge)
            }
        }
        .padding(.horizontal, ChatScreenMetrics.inset)
        .padding(.vertical, 6)
        .frame(maxWidth: .infinity)
        .background(palette.card.color)
        .overlay(alignment: .bottom) { Rectangle().fill(palette.line.color).frame(height: 1) }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(state.accessibilityText)
        .accessibilityAddTraits(.isHeader)
    }

    /// "1 needs you", "2 need you", or nil when nothing waits.
    static func badge(needsYou: Int) -> String? {
        switch needsYou {
        case ...0: nil
        case 1: "1 needs you"
        default: "\(needsYou) need you"
        }
    }
}

/// An agent's avatar (its own sheet from `chatAvatars`, else the giraffe), animated in the app; one still frame in
/// snapshots, where nothing may move. AvatarKit draws it at a whole-number zoom with nearest-neighbour scaling; with
/// Reduce Motion it holds the state's first frame.
struct ChatAvatar: View {
    var state: AvatarState
    var box: CGFloat
    var still: Bool
    var name: String = AgentName.fallback
    var agentId: String?

    @Environment(\.chatAvatars) private var avatars

    init(state: AvatarState, size: AvatarSize, still: Bool, name: String = AgentName.fallback, agentId: String? = nil) {
        self.init(state: state, box: size.points, still: still, name: name, agentId: agentId)
    }

    init(state: AvatarState, box: CGFloat, still: Bool, name: String = AgentName.fallback, agentId: String? = nil) {
        self.state = state
        self.box = box
        self.still = still
        self.name = name
        self.agentId = agentId
    }

    var sheet: SpriteSheet { avatars?.sheet(for: agentId) ?? .giraffe }

    var body: some View {
        let sheet = sheet
        if still {
            AvatarFrameView(sheet: sheet, frame: sheet.loop(for: state).firstFrame, box: box)
        } else {
            AvatarView(state: state, box: box, sheet: sheet, name: name)
        }
    }
}

extension EnvironmentValues {
    /// Each agent's avatar sheet (`ChatModel.avatars`); nil plays the bundled giraffe everywhere.
    @Entry public var chatAvatars: AvatarSheets? = nil

    /// Snapshot tests draw every avatar as its state's first frame, and the composer's field as plain text.
    @Entry public var chatStillAvatars = false
}
