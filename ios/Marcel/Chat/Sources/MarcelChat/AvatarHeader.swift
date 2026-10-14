import AvatarKit
import MarcelItems
import MarcelKit
import PixelKit
import SwiftUI

/// What the top strip says: what the helper is doing (its pose), how many quests run and how many things need you.
///
/// The pose follows `ChatModel.action`: the hub's `agent.state` events (B-28), refined by the last tool call and a
/// recent problem (`MarcelAction`), held at least two seconds each. Until the hub has said, and while the live stream
/// is down, it is worked out from the cached tasks instead, so it is right offline too.
public struct ChatHeaderState: Equatable, Sendable {
    /// The agent's name: `AgentName.fallback` until the hub says otherwise (Settings can rename it).
    public var name: String = AgentName.fallback
    /// Whose avatar the header plays (B-44); nil plays the bundled giraffe.
    public var agentId: String?
    /// What the helper is doing; its pose is `avatar`.
    public var action: MarcelAction
    public var avatar: AvatarState { action.avatar }
    /// A mood loop playing over the pose (`ChatModel.mood`), or nil for the pose's own loop.
    public var mood: String?
    /// The helper's personality: it weights the idle moods.
    public var personality: LookPersonality?
    public var running: Int
    public var needsYou: Int
    /// Set when the live stream is down: what the person should know, in plain words.
    public var offline: String?

    public init(action: MarcelAction = .idle, running: Int = 0, needsYou: Int = 0, offline: String? = nil,
                name: String? = nil, agentId: String? = nil, mood: String? = nil,
                personality: LookPersonality? = nil) {
        self.name = name ?? AgentName.fallback
        self.agentId = agentId
        self.action = action
        self.mood = mood
        self.personality = personality
        self.running = running
        self.needsYou = needsYou
        self.offline = offline
    }

    /// For a plain pose (previews and tests): the hub's state without a tool or a problem.
    public init(avatar: AvatarState, running: Int = 0, needsYou: Int = 0, offline: String? = nil, name: String? = nil,
                agentId: String? = nil) {
        self.init(action: MarcelAction(agent: avatar), running: running, needsYou: needsYou, offline: offline,
                  name: name, agentId: agentId)
    }

    /// From the cached tasks, and the hub's own word (`live`, `ChatModel.action`) when the stream is up. Without it,
    /// the contract's precedence (`AgentState`): needs you, then working(n), then idle.
    public init(tasks: some Collection<HubTask>, offline: String? = nil, name: String? = nil,
                live: MarcelAction? = nil, agentId: String? = nil, mood: String? = nil,
                personality: LookPersonality? = nil) {
        let needsYou = tasks.filter { $0.state == .needsYou && $0.archivedAt == nil }.count
        let working = tasks.filter { ($0.state == .working || $0.state == .starting) && $0.archivedAt == nil }.count
        let fromTasks: MarcelAction = needsYou > 0 ? .waiting : working > 0 ? .working(working) : .idle
        let action = (offline == nil ? live : nil) ?? fromTasks
        self.init(action: action, running: working, needsYou: needsYou, offline: offline, name: name, agentId: agentId,
                  mood: offline == nil ? mood : nil, personality: personality)
    }

    public var subtitle: String {
        if let offline { return offline }
        switch running {
        case 0: return "Nothing running"
        case 1: return "1 quest running"
        default: return "\(running) quests running"
        }
    }

    /// What VoiceOver reads for the whole header: "Marcel, working on: thinking. Nothing running. 1 thing needs you".
    public var accessibilityText: String {
        var parts = [action.spoken(name: name), subtitle]
        switch needsYou {
        case 0: break
        case 1: parts.append("1 thing needs you")
        default: parts.append("\(needsYou) things need you")
        }
        return parts.joined(separator: ". ")
    }
}

/// The avatar header, after the owner's pick (a mix of the reference's centred helper and a pixel speech bubble;
/// this overrides wireframe Q1's strip): the helper big and centred at 96 pt (an exact 9× of the 32-pixel sprite on a
/// 3× screen), its name under it, and what it is doing in a speech bubble beside its head; no bubble while it is
/// free. A round gold count at the top left says how many things need you. It floats over the chat with no background
/// of its own, so the bow tiling stays whole behind it: the messages start below it, and as they scroll under it they
/// blur and fade (`UnderHeader`).
///
/// The pose follows `ChatHeaderState.action`. While the helper is free it plays a calm mood now and then
/// (`IdleMoods`), never in the background, in snapshots or with Reduce Motion; with Reduce Motion poses also switch
/// without animating (AvatarKit shows each pose's first frame).
struct AvatarHeader: View {
    static let box: CGFloat = 96
    /// How much of the top the header takes; the feed's first message starts below it.
    static let height: CGFloat = 128

    var state: ChatHeaderState

    @Environment(\.chatStillAvatars) private var still
    @Environment(\.chatAvatars) private var avatars
    @Environment(\.colorScheme) private var scheme
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.scenePhase) private var scenePhase
    /// An idle mood playing over idle (`mood_wink`, `mood_sleepy`), or nil.
    @State private var idleMood: String?

    init(state: ChatHeaderState) {
        self.state = state
    }

    /// Idle moods run only while free, in the foreground, animated and not in a snapshot.
    private var moodsRun: Bool {
        state.action == .idle && !still && !reduceMotion && scenePhase == .active
    }

    var body: some View {
        let palette = ChatPalette(scheme)
        let pose = Self.pose(state: state, idleMood: moodsRun ? idleMood : nil, reduceMotion: reduceMotion,
                             sheet: avatars?.sheet(for: state.agentId) ?? .giraffe)
        VStack(spacing: 0) {
            ChatAvatar(state: pose, box: Self.box, still: still, name: state.name, agentId: state.agentId)
                .overlay(alignment: .topLeading) {
                    // Beside the head, the tail pointing back at it. The helper's own marks ("!", "?") sit on the
                    // other side of its head (the zoo draws them there).
                    if state.action != .idle {
                        // One line when it fits; a long caption or big text wraps within the screen.
                        ViewThatFits(in: .horizontal) {
                            PixelBubble(text: state.action.caption, palette: palette).fixedSize()
                            PixelBubble(text: state.action.caption, palette: palette)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                        .frame(width: 160, alignment: .leading)
                        .offset(x: Self.box * 0.72, y: 2)
                    }
                }
            // A small white pill, like the reference's, so the name reads over a blurred message.
            PixelText(state.name, role: .smallLabel)
                .foregroundStyle(palette.ink.color)
                .padding(.horizontal, 10)
                .padding(.vertical, 3)
                .background(palette.card.color, in: Capsule())
                .overlay(Capsule().strokeBorder(palette.line.color, lineWidth: 1))
        }
        .padding(.top, 4)
        .frame(maxWidth: .infinity)
        .frame(height: Self.height, alignment: .top)
        .overlay(alignment: .topLeading) {
            if state.needsYou > 0 {
                PixelText("\(state.needsYou)", role: .label)
                    .foregroundStyle(ChatBadge.text(.needsYou, in: palette).color)
                    .frame(width: 30, height: 30)
                    .background(ChatBadge.fill(.needsYou, in: palette).color, in: Circle())
                    .padding(.leading, ChatScreenMetrics.inset)
                    .padding(.top, 8)
            }
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(state.accessibilityText)
        .accessibilityAddTraits(.isHeader)
        .task(id: moodsRun) { await playIdleMoods() }
    }

    /// What the helper plays: the mood over its pose when its sheet has that loop, else the pose itself, else (an
    /// older sheet without `reading` or `error`) the nearest state loop it does have. With Reduce Motion only the state
    /// poses show: no moods over them.
    static func pose(state: ChatHeaderState, idleMood: String?, reduceMotion: Bool, sheet: SpriteSheet) -> AvatarState {
        if !reduceMotion, let mood = state.mood ?? idleMood, sheet.loops[mood] != nil { return .mood(mood) }
        let pose = state.avatar
        guard sheet.loops[pose.loopName] == nil else { return pose }
        switch pose {
        case .reading: return .working(1)
        case .error: return .needsYou
        default: return pose
        }
    }

    private func playIdleMoods() async {
        idleMood = nil
        guard moodsRun else { return }
        let sheet = avatars?.sheet(for: state.agentId) ?? .giraffe
        var moods = IdleMoods(sheet: sheet, personality: state.personality)
        var random = SystemRandomNumberGenerator()
        let start = ContinuousClock.now
        func now() -> TimeInterval {
            let elapsed = ContinuousClock.now - start
            return TimeInterval(elapsed.components.seconds) + TimeInterval(elapsed.components.attoseconds) / 1e18
        }
        while !Task.isCancelled {
            let (loop, wake) = moods.update(now: now(), free: true, random: &random)
            idleMood = loop
            guard let wake else { return }
            try? await Task.sleep(for: .seconds(max(0.05, wake - now())))
        }
    }
}

extension MarcelAction {
    /// The few words in the speech bubble: "Thinking…", "Reading…", "Waiting for you".
    var caption: String {
        switch self {
        case .idle: "Free"
        case .thinking: "Thinking…"
        case .working(let n): n <= 1 ? "Working…" : "On \(n) quests…"
        case .writing: "Writing…"
        case .reading: "Reading…"
        case .waiting: "Waiting for you"
        case .done: "Done!"
        case .error: "Something went wrong"
        }
    }
}

/// A speech bubble with stepped, pixel corners and a two-step tail pointing down-left at the helper.
struct PixelBubble: View {
    var text: String
    var palette: ChatPalette

    var body: some View {
        Text(text)
            .font(.footnote.weight(.semibold))
            .foregroundStyle(palette.ink.color)
            .padding(.horizontal, 10)
            .padding(.vertical, 5)
            .background(PixelBox(step: 3).fill(palette.card.color))
            .overlay(PixelBox(step: 3).stroke(palette.ink.color, lineWidth: 2))
            .padding(.bottom, 6)
            .overlay(alignment: .bottomLeading) {
                VStack(alignment: .leading, spacing: 0) {
                    Rectangle().fill(palette.ink.color).frame(width: 6, height: 3).padding(.leading, 6)
                    Rectangle().fill(palette.ink.color).frame(width: 3, height: 3).padding(.leading, 3)
                }
            }
    }
}

/// A rectangle with each corner cut in one pixel step, like a Game Boy dialogue box.
struct PixelBox: Shape {
    var step: CGFloat

    func path(in r: CGRect) -> Path {
        let s = step
        let points = [
            (r.minX + s, r.minY), (r.maxX - s, r.minY), (r.maxX - s, r.minY + s), (r.maxX, r.minY + s),
            (r.maxX, r.maxY - s), (r.maxX - s, r.maxY - s), (r.maxX - s, r.maxY), (r.minX + s, r.maxY),
            (r.minX + s, r.maxY - s), (r.minX, r.maxY - s), (r.minX, r.minY + s), (r.minX + s, r.minY + s),
        ]
        var p = Path()
        p.addLines(points.map { CGPoint(x: $0.0, y: $0.1) })
        p.closeSubpath()
        return p
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

/// How messages look as they scroll under the header: blurred and see-through, more the further under they are. Only
/// the message layer changes; the bow tiling behind stays untouched.
struct UnderHeader: Equatable, Sendable {
    /// The blur, in points, of a message fully under the header.
    var blur: CGFloat
    /// How much of a message still shows at the very top (1: all of it).
    var shows: Double

    /// The owner's pick.
    static let soft = UnderHeader(blur: 3, shows: 0.6)
    static let medium = UnderHeader(blur: 6, shows: 0.35)
    static let strong = UnderHeader(blur: 10, shows: 0.12)

    /// The feed's coordinate space: y = 0 is the top of the header.
    static let space = "chat.feed"

    /// How far under the header a row whose middle is at `midY` is: 0 below it, 1 well under the helper.
    static func depth(midY: CGFloat) -> CGFloat {
        min(max((AvatarHeader.height + 12 - midY) / 72, 0), 1)
    }

    /// Fades the whole message layer towards the top, so a tall message also fades where it reaches up.
    var mask: some View {
        LinearGradient(stops: [.init(color: .black.opacity(shows), location: 0),
                               .init(color: .black, location: 1)],
                       startPoint: .top, endPoint: .bottom)
            .frame(height: AvatarHeader.height + 12)
            .frame(maxHeight: .infinity, alignment: .top)
            .background(alignment: .bottom) { Color.black.padding(.top, AvatarHeader.height + 12) }
    }
}

extension EnvironmentValues {
    /// How strongly messages blur and fade under the header.
    @Entry var underHeader: UnderHeader = .soft
}

/// One feed row: blurred and see-through by how far it has scrolled under the header.
struct UnderHeaderRow: ViewModifier {
    @Environment(\.underHeader) private var style
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency

    func body(content: Content) -> some View {
        let style = style
        // With Reduce Transparency, rows only fade: no blur to read through.
        let blur = reduceTransparency ? 0 : style.blur
        content.visualEffect { effect, proxy in
            let depth = UnderHeader.depth(midY: proxy.frame(in: .named(UnderHeader.space)).midY)
            return effect.blur(radius: blur * depth).opacity(1 - (1 - style.shows) * depth)
        }
    }
}
