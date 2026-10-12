import MarcelKit
import PixelKit
import SwiftData
import SwiftUI

/// The Chat tab: the avatar header, the one conversation with Marcel, and the composer. Reads everything from the
/// cache (`.modelContainer` must be set), which the live event stream keeps current.
public struct ChatScreen: View {
    let model: ChatModel
    /// What the live stream says when it is down ("Lost the connection…"); nil while live.
    var offline: String?

    public init(model: ChatModel, offline: String? = nil) {
        self.model = model
        self.offline = offline
    }

    @State private var path: [ChatRoute] = []

    public var body: some View {
        NavigationStack(path: $path) {
            content
                .toolbar(.hidden, for: .navigationBar)
                .navigationDestination(for: ChatRoute.self) { route in
                    switch route {
                    case .quest(let line): QuestPlaceholder(line: line)
                    case .item(let line): ItemPlaceholder(line: line)
                    }
                }
        }
        .task { await model.start() }
    }

    private var actions: ChatActions {
        var actions = ChatActions()
        actions.openQuest = { path.append(.quest($0)) }
        actions.openItem = { path.append(.item($0)) }
        actions.answer = { id, approve in Task { await model.answer(approvalId: id, approve: approve) } }
        actions.toggleDetails = { model.toggleDetails(approvalId: $0) }
        actions.retry = { id in Task { await model.retry(clientId: id) } }
        return actions
    }

    private var content: some View {
        Group {
            if let conversationId = model.conversationId {
                // A new `limit` re-creates the query with the bigger window; the scroll state stays.
                LiveFeed(model: model, conversationId: conversationId, limit: model.limit, offline: offline,
                         actions: actions)
            } else {
                ChatLayout(header: ChatHeaderState(offline: offline), collapsed: false, problem: model.problem,
                           composer: { EmptyView() }) {
                    PixelText(model.problem == nil ? "Opening the conversation…" : "Nothing saved on this phone yet.",
                              role: .detail)
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                }
            }
        }
    }
}

/// Where a tap in the feed leads.
enum ChatRoute: Hashable {
    case quest(QuestLine)
    case item(ItemLine)
}

/// The screen once the conversation is known: queries the cache and lays the feed out.
struct LiveFeed: View {
    let model: ChatModel
    let conversationId: String
    let offline: String?
    let actions: ChatActions
    @Query private var window: [CachedMessage]
    @Query private var tasks: [CachedTask]
    @Query private var artifacts: [CachedArtifact]
    @Query private var projects: [CachedProject]
    private let limit: Int

    @State private var distanceFromBottom: CGFloat = 0
    @State private var scrolledTo: String?
    @FocusState private var typing: Bool
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    init(model: ChatModel, conversationId: String, limit: Int, offline: String?, actions: ChatActions) {
        self.model = model
        self.actions = actions
        self.conversationId = conversationId
        self.offline = offline
        self.limit = limit
        _window = Query(FeedInput.messageWindow(conversationId: conversationId, limit: limit))
    }

    var body: some View {
        let input = FeedInput.decode(newestMessages: window, limit: limit, tasks: tasks, artifacts: artifacts,
                                     projects: projects)
        let items = ChatFeed.items(input, local: model.local)
        let header = ChatHeaderState(tasks: input.tasks.values, offline: offline)
        // Big giraffe while you sit at the latest messages; small once you scroll back or start typing.
        let collapsed = distanceFromBottom > 24 || typing
        @Bindable var model = model
        ChatLayout(header: header, collapsed: collapsed, problem: model.problem, composer: {
            Composer(text: $model.draft,
                     placeholder: Composer.placeholder(questionWaiting: ChatFeed.hasOpenQuestion(items)),
                     focused: $typing) {
                Task { await model.send() }
            }
        }) {
            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: FeedMetrics.spacing) {
                        if input.hasEarlier {
                            EarlierMarker()
                                .onAppear {
                                    scrolledTo = items.first?.id
                                    model.loadEarlier(shown: window.count)
                                }
                        }
                        FeedStack(items: items, actions: actions)
                    }
                    .padding(FeedMetrics.inset)
                    .scrollTargetLayout()
                }
                .defaultScrollAnchor(.bottom)
                .scrollPosition(id: $scrolledTo, anchor: .top)
                .onScrollGeometryChange(for: CGFloat.self) { geometry in
                    geometry.contentSize.height + geometry.contentInsets.bottom
                        - geometry.contentOffset.y - geometry.containerSize.height
                } action: { _, distance in
                    distanceFromBottom = max(0, distance)
                }
                .onChange(of: items.last?.id) { _, last in
                    // New messages follow you only when you are already at the bottom.
                    guard let last, distanceFromBottom < 80 else { return }
                    withAnimation(reduceMotion ? nil : .default) { proxy.scrollTo(last, anchor: .bottom) }
                }
                .overlay(alignment: .bottomTrailing) {
                    if distanceFromBottom > 200, let last = items.last?.id {
                        JumpToLatest {
                            withAnimation(reduceMotion ? nil : .default) { proxy.scrollTo(last, anchor: .bottom) }
                        }
                        .padding(FeedMetrics.inset)
                    }
                }
            }
        }
        .animation(reduceMotion ? nil : .snappy(duration: 0.25), value: collapsed)
        // Approvals are not in the cache: fetch them when one shows up or its task moves on.
        .task(id: Self.approvalKey(input)) { await model.reconcile(input) }
        // Your messages that came back over the stream show from the cache now.
        .onChange(of: window.count, initial: true) { model.prune(input) }
        // Items on screen get their first lines, once per version.
        .task(id: Self.itemKeys(input)) {
            for artifact in Self.itemArtifacts(input) { await model.loadPreview(artifact) }
        }
    }
}

extension LiveFeed {
    /// Changes when an approval message arrives or a task's open approvals change.
    static func approvalKey(_ input: FeedInput) -> String {
        let shown = input.messages.compactMap { $0.kind == .approval ? $0.approvalId : nil }
        let open = input.tasks.values.flatMap { $0.approvalIds ?? [] }.sorted()
        return (shown + ["|"] + open).joined(separator: ",")
    }

    static func itemArtifacts(_ input: FeedInput) -> [Artifact] {
        input.messages.compactMap { $0.kind == .artifact ? $0.artifactId.flatMap { input.artifacts[$0] } : nil }
    }

    static func itemKeys(_ input: FeedInput) -> String {
        itemArtifacts(input).map(ArtifactPreview.key).joined(separator: ",")
    }
}

/// Header on top, the feed in the middle, a problem line under it. The composer and the tab bar go under this.
/// The feed's spacing and margins (the wireframes' 8 and 10 px on a 290-pt phone, scaled to 390 pt).
enum FeedMetrics {
    static let spacing: CGFloat = PixelMetrics.steps(6)
    static let inset: CGFloat = PixelMetrics.steps(7)
}

struct ChatLayout<Feed: View, ComposerView: View>: View {
    var header: ChatHeaderState
    var collapsed: Bool
    var problem: String?
    var composer: ComposerView
    var feed: Feed

    @Environment(\.colorScheme) private var scheme

    init(header: ChatHeaderState, collapsed: Bool, problem: String?, @ViewBuilder composer: () -> ComposerView,
         @ViewBuilder feed: () -> Feed) {
        self.header = header
        self.collapsed = collapsed
        self.problem = problem
        self.composer = composer()
        self.feed = feed()
    }

    var body: some View {
        let theme = PixelTheme(scheme)
        VStack(spacing: 0) {
            ChatHeader(state: header, collapsed: collapsed)
            feed
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .bottom)
            if let problem {
                PixelText(problem, role: .detail)
                    .foregroundStyle(theme.accent.color)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, FeedMetrics.inset)
                    .padding(.bottom, PixelMetrics.steps(2))
                    .accessibilityLabel("Problem: \(problem)")
            }
            composer
        }
        .background(theme.background.color)
    }
}

/// The top of the loaded window; scrolling to it loads the next older page from the cache.
struct EarlierMarker: View {
    @Environment(\.colorScheme) private var scheme

    var body: some View {
        PixelText("Earlier messages", role: .smallLabel)
            .foregroundStyle(PixelTheme(scheme).muted.color)
            .frame(maxWidth: .infinity)
            .padding(.vertical, PixelMetrics.steps(2))
            .accessibilityLabel("Loading earlier messages")
    }
}

/// "Latest ▼": back to the newest message after scrolling up.
struct JumpToLatest: View {
    var action: () -> Void
    @Environment(\.colorScheme) private var scheme

    var body: some View {
        let theme = PixelTheme(scheme)
        Button(action: action) {
            HStack(spacing: PixelMetrics.steps(3)) {
                PixelText("Latest", role: .smallLabel)
                PixelGlyph(.more, color: theme.text)
            }
            .foregroundStyle(theme.text.color)
            .padding(.horizontal, PixelMetrics.steps(5))
            .frame(minHeight: PixelMetrics.minTapTarget)
            .pixelBox(fill: theme.dialog, line: theme.line, shadow: theme.shadow, border: PixelMetrics.thinBorder,
                      offset: PixelMetrics.smallShadow)
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Jump to the latest message")
    }
}
