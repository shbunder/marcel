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

    public var body: some View {
        Group {
            if let conversationId = model.conversationId {
                // A new `limit` re-creates the query with the bigger window; the scroll state stays.
                LiveFeed(model: model, conversationId: conversationId, limit: model.limit, offline: offline)
            } else {
                ChatLayout(header: ChatHeaderState(offline: offline), collapsed: false, problem: model.problem) {
                    PixelText(model.problem == nil ? "Opening the conversation…" : "Nothing saved on this phone yet.",
                              role: .detail)
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                }
            }
        }
        .task { await model.start() }
    }
}

/// The screen once the conversation is known: queries the cache and lays the feed out.
struct LiveFeed: View {
    let model: ChatModel
    let conversationId: String
    let offline: String?
    @Query private var window: [CachedMessage]
    @Query private var tasks: [CachedTask]
    @Query private var artifacts: [CachedArtifact]
    @Query private var projects: [CachedProject]
    private let limit: Int

    @State private var distanceFromBottom: CGFloat = 0
    @State private var scrolledTo: String?
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    init(model: ChatModel, conversationId: String, limit: Int, offline: String?) {
        self.model = model
        self.conversationId = conversationId
        self.offline = offline
        self.limit = limit
        _window = Query(FeedInput.messageWindow(conversationId: conversationId, limit: limit))
    }

    var body: some View {
        let input = FeedInput.decode(newestMessages: window, limit: limit, tasks: tasks, artifacts: artifacts,
                                     projects: projects)
        let items = ChatFeed.items(input)
        let header = ChatHeaderState(tasks: input.tasks.values, offline: offline)
        // Big giraffe while you sit at the latest messages; small once you scroll back.
        let collapsed = distanceFromBottom > 24
        ChatLayout(header: header, collapsed: collapsed, problem: model.problem) {
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
                        FeedStack(items: items, actions: ChatActions())
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
    }
}

/// Header on top, the feed in the middle, a problem line under it. The composer and the tab bar go under this.
/// The feed's spacing and margins (the wireframes' 8 and 10 px on a 290-pt phone, scaled to 390 pt).
enum FeedMetrics {
    static let spacing: CGFloat = PixelMetrics.steps(6)
    static let inset: CGFloat = PixelMetrics.steps(7)
}

struct ChatLayout<Feed: View>: View {
    var header: ChatHeaderState
    var collapsed: Bool
    var problem: String?
    var feed: Feed

    @Environment(\.colorScheme) private var scheme

    init(header: ChatHeaderState, collapsed: Bool, problem: String?, @ViewBuilder feed: () -> Feed) {
        self.header = header
        self.collapsed = collapsed
        self.problem = problem
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
