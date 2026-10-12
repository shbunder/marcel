import AvatarKit
import MarcelKit
import PixelKit
import SwiftUI

/// What the top strip says: the giraffe's state, how many quests run and how many things need you. Worked out
/// from the cached tasks, so it is right offline too and follows every `task.updated`.
public struct ChatHeaderState: Equatable, Sendable {
    public var avatar: AvatarState
    public var running: Int
    public var needsYou: Int
    /// Set when the live stream is down: what the person should know, in plain words.
    public var offline: String?

    public init(avatar: AvatarState = .idle, running: Int = 0, needsYou: Int = 0, offline: String? = nil) {
        self.avatar = avatar
        self.running = running
        self.needsYou = needsYou
        self.offline = offline
    }

    /// The contract's precedence for the avatar (`AgentState`): needs you, then working(n), then idle.
    public init(tasks: some Collection<HubTask>, offline: String? = nil) {
        let needsYou = tasks.filter { $0.state == .needsYou && $0.archivedAt == nil }.count
        let working = tasks.filter { ($0.state == .working || $0.state == .starting) && $0.archivedAt == nil }.count
        let avatar: AvatarState = needsYou > 0 ? .needsYou : working > 0 ? .working(working) : .idle
        self.init(avatar: avatar, running: working, needsYou: needsYou, offline: offline)
    }

    public var subtitle: String {
        if let offline { return offline }
        switch running {
        case 0: return "Nothing running"
        case 1: return "1 quest running"
        default: return "\(running) quests running"
        }
    }
}

/// The avatar header: Marcel's giraffe at 120 pt while you are at the latest messages, 44 pt once you scroll
/// back or type. Hearts and the "needs you" count on the right (PixelKit's `HeartsHeader`).
struct ChatHeader: View {
    var state: ChatHeaderState
    var collapsed: Bool

    @Environment(\.chatStillAvatars) private var still

    var body: some View {
        HeartsHeader(title: "Marcel", subtitle: state.subtitle, needsYou: state.needsYou) {
            ChatAvatar(state: state.avatar, size: collapsed ? .medium : .large, still: still)
        }
        .accessibilityLabel(accessibilityText)
    }

    /// The header's own words plus what the giraffe is doing (the sprite itself is hidden from VoiceOver).
    var accessibilityText: String {
        var parts = ["Marcel", state.avatar.accessibilityDescription, state.subtitle]
        switch state.needsYou {
        case 0: break
        case 1: parts.append("1 thing needs you")
        default: parts.append("\(state.needsYou) things need you")
        }
        return parts.joined(separator: ". ")
    }
}

/// The giraffe, animated in the app; one still frame in snapshots, where nothing may move.
struct ChatAvatar: View {
    var state: AvatarState
    var size: AvatarSize
    var still: Bool

    var body: some View {
        if still {
            AvatarFrameView(frame: SpriteSheet.giraffe.loops[state.loopName]?.firstFrame ?? 0, size: size)
        } else {
            AvatarView(state: state, size: size)
        }
    }
}

extension EnvironmentValues {
    /// Snapshot tests draw every avatar as its state's first frame.
    @Entry var chatStillAvatars = false
}
