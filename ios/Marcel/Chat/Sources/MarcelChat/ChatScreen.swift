import MarcelItems
import MarcelKit
import MarcelQuest
import PixelKit
import SwiftData
import SwiftUI

/// The Chat tab: the avatar header, the one conversation with Marcel, and the composer. Reads everything from the
/// cache (`.modelContainer` must be set), which the live event stream keeps current.
public struct ChatScreen: View {
    let model: ChatModel
    /// What the live stream says when it is down ("Lost the connection…"); nil while live.
    var offline: String?
    /// What a tap on the avatar header opens: Marcel's notebook and Settings (it asks which when there are both).
    var avatar: AvatarLinks

    @Environment(\.colorScheme) private var scheme

    public init(model: ChatModel, offline: String? = nil, openSettings: (() -> Void)? = nil,
                openNotebook: (() -> Void)? = nil) {
        self.model = model
        self.offline = offline
        self.avatar = AvatarLinks(notebook: openNotebook, settings: openSettings)
    }

    @State private var path: [ChatRoute] = []

    public var body: some View {
        NavigationStack(path: $path) {
            content
                .toolbar(.hidden, for: .navigationBar)
                .navigationDestination(for: ChatRoute.self) { route in
                    switch route {
                    case .quest(let line): QuestView(taskId: line.taskId)
                    case .item(let line): ArtifactViewer(artifactId: line.artifactId, conversationId: model.conversationId)
                    }
                }
        }
        // Every screen the chat opens says the agent's name as the hub last said it.
        .environment(\.agentName, model.agentName ?? AgentName.fallback)
        // "Ask about this" in a viewer opened from the chat: back to the feed, with the item on the next message.
        .environment(\.askInChat) { item in
            model.attach(item)
            path.removeAll()
        }
        .task { await model.start() }
        // Reply in thread (B-08): the side chat as a sheet over the feed.
        .sheet(item: Binding(get: { model.sideChat }, set: { model.sideChat = $0 })) { side in
            SideChatSheet(
                model: side, rowActions: actions, approvals: model.local,
                openQuest: { taskId in path.append(.quest(Self.questLine(taskId))) },
                startOver: { words in if !words.isEmpty { model.propose(words) } })
                .environment(\.agentName, model.agentName ?? AgentName.fallback)
        }
    }

    /// The quest a side chat talks to, for the quest view (which reads the rest from the cache).
    static func questLine(_ taskId: String) -> QuestLine {
        QuestLine(id: "side-\(taskId)", taskId: taskId, title: "", state: .working, project: nil, tone: .teal,
                  detail: nil)
    }

    private var actions: ChatActions {
        var actions = ChatActions()
        actions.openQuest = { path.append(.quest($0)) }
        actions.openItem = { path.append(.item($0)) }
        actions.answer = { id, approve in Task { await model.answer(approvalId: id, approve: approve) } }
        actions.toggleDetails = { model.toggleDetails(approvalId: $0) }
        actions.retry = { id in Task { await model.retry(clientId: id) } }
        // A mention chip (B-42): a quest or an item opens here; a project room is the Map's (LiveFeed handles it).
        actions.openMention = { mention in
            switch mention.kind {
            case .task: path.append(.quest(Self.questLine(mention.id)))
            case .artifact:
                path.append(.item(ItemLine(id: "mention-\(mention.id)", artifactId: mention.id, title: mention.title,
                                           savedTo: nil, badge: "", rows: [], showsBanner: false, isDigest: false,
                                           url: nil)))
            case .project: break
            }
        }
        return actions
    }

    private var content: some View {
        Group {
            if let conversationId = model.conversationId {
                // A new `limit` re-creates the query with the bigger window; the scroll state stays.
                LiveFeed(model: model, conversationId: conversationId, limit: model.limit, offline: offline,
                         actions: actions, avatar: avatar)
            } else {
                ChatLayout(header: ChatHeaderState(tasks: [HubTask](), offline: offline, name: model.agentName,
                                                   live: model.agentState, agentId: model.agentId),
                           problem: model.problem,
                           avatar: avatar, composer: { EmptyView() }) {
                    Text(model.problem == nil ? "Opening the conversation…" : "Nothing saved on this phone yet.")
                        .font(.footnote)
                        .foregroundStyle(ChatPalette(scheme).muted.color)
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                }
                .environment(\.chatAvatars, model.avatars)
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
    var avatar: AvatarLinks
    @Query private var window: [CachedMessage]
    @Query private var tasks: [CachedTask]
    @Query private var artifacts: [CachedArtifact]
    @Query private var projects: [CachedProject]
    @Query private var agents: [CachedAgent]
    private let limit: Int
    @State private var observer: UUID?
    @Environment(\.liveEvents) private var live
    @Environment(\.modelContext) private var context

    @State private var distanceFromBottom: CGFloat = 0
    /// The message a "re:" line jumped to, lit up for a moment (B-41).
    @Environment(\.openURL) private var openURL
    @State private var scrolledTo: String?
    @FocusState private var typing: Bool
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    init(model: ChatModel, conversationId: String, limit: Int, offline: String?, actions: ChatActions,
         avatar: AvatarLinks = AvatarLinks()) {
        self.model = model
        self.actions = actions
        self.avatar = avatar
        self.conversationId = conversationId
        self.offline = offline
        self.limit = limit
        _window = Query(FeedInput.messageWindow(conversationId: conversationId, limit: limit))
    }

    /// The feed's actions plus "Reply in thread" and "n replies", when the hub does side chats.
    private func threaded(_ input: FeedInput, name: String) -> ChatActions {
        var actions = actions
        // "re: your 08:12 message": scroll to it (loading older pages if needed) and light it up.
        actions.jump = { [model] id in model.jump(to: id) }
        // A #project chip opens its room on the Map, through the app's own link.
        let open = actions.openMention
        actions.openMention = { [openURL] mention in
            if mention.kind == .project, let slug = input.projects[mention.id]?.slug,
               let url = URL(string: "marcel://project/\(slug)") {
                openURL(url)
            } else {
                open(mention)
            }
        }
        guard model.canReplyInThread else { return actions }
        let messages = Dictionary(input.messages.map { ($0.id, $0) }, uniquingKeysWith: { a, _ in a })
        actions.reply = { [model] item in
            if let parent = SideChatParent.of(item, message: messages[item.id], agentName: name) {
                model.openSideChat(parent)
            }
        }
        actions.threads = model.sideChats.summaries(for: input.messages)
        return actions
    }

    var body: some View {
        let input = FeedInput.decode(newestMessages: window, limit: limit, tasks: tasks, artifacts: artifacts,
                                     projects: projects, agents: agents, agentId: model.agentId)
        let items = ChatFeed.items(input, local: model.local)
        let header = ChatHeaderState(tasks: input.tasks.values, offline: offline, name: model.agentName,
                                     live: model.agentState, agentId: model.agentId)
        @Bindable var model = model
        ChatLayout(header: header, problem: model.problem, avatar: avatar, composer: {
            if let trigger = model.mentionTrigger {
                MentionSuggestions(trigger: trigger, found: model.mentionSuggestions,
                                   unreachable: model.mentionsUnreachable) { model.pick($0) }
                    .padding(.horizontal, FeedMetrics.inset)
                    .padding(.top, PixelMetrics.steps(2))
            }
            Composer(text: $model.draft,
                     placeholder: Composer.placeholder(questionWaiting: ChatFeed.hasOpenQuestion(items),
                                                      name: header.name),
                     focused: $typing, send: { Task { await model.send() } }) {
                ComposerExtras(mentions: model.mentions, attachment: model.attachment?.title,
                               unpick: { model.unpick($0) }, detach: { model.attachment = nil })
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
                        FeedStack(items: items, actions: threaded(input, name: header.name))
                        if ThinkingRow.shows(header.avatar) {
                            ThinkingRow(state: header.avatar, name: header.name, agentId: header.agentId)
                        }
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
                // A message link: scroll to it once it is in the window, loading older pages until it is.
                .onChange(of: FocusKey(focus: model.focus, ids: items.map(\.id)), initial: true) { _, key in
                    guard let focus = key.focus else { return }
                    if key.ids.contains(focus) {
                        model.focus = nil
                        withAnimation(reduceMotion ? nil : .default) { proxy.scrollTo(focus, anchor: .center) }
                    } else if input.hasEarlier, model.limit < ChatModel.focusDepth {
                        model.loadEarlier(shown: window.count)
                    } else {
                        // Not on this phone (or far back): stay at the latest messages.
                        model.focus = nil
                    }
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
        .environment(\.agentName, header.name)
        .environment(\.chatBubbleColor, model.bubbleColor)
        .environment(\.chatHighlight, model.highlighted)
        .environment(\.chatAvatars, model.avatars)
        // `@` and `#` in the draft ask the hub what matches (B-42).
        .onChange(of: model.draft) { Task { await model.draftChanged() } }
        // Every helper with a speaker line plays its own avatar, not only the ones whose look just changed.
        .task(id: Set(items.compactMap(\.speaker?.agentId))) {
            await model.loadAvatars(for: Set(items.compactMap(\.speaker?.agentId)))
        }
        // Approvals are not in the cache: fetch them when one shows up or its task moves on.
        .task(id: Self.approvalKey(input)) { await model.reconcile(input) }
        // Your messages that came back over the stream show from the cache now.
        // Keyed on the client ids in the window, not its size: a full window stays at 50 rows.
        .onChange(of: Self.arrivalKey(input), initial: true) { model.prune(input) }
        // Back online: answers may have come in while the stream was down.
        .onChange(of: offline) { old, new in
            if old != nil, new == nil { Task { await model.reconnected(input) } }
        }
        .task {
            if let live, observer == nil {
                // The giraffe follows `agent.state`; side threads opened on another device show "n replies".
                observer = live.observeFrames { [model, sideChats = model.sideChats, context] frame in
                    model.receive(frame)
                    sideChats.receive(frame, context: context)
                }
            }
        }
        // "n replies": the threads on screen are counted when they come into the window, not on every redraw.
        .task(id: model.sideChats.threadIds(of: input.messages)) {
            model.sideChats.recount(model.sideChats.threadIds(of: input.messages), in: context)
        }
        .onDisappear {
            if let observer { live?.stopObservingFrames(observer) }
            observer = nil
        }
        // Items on screen get their first lines, once per version.
        .task(id: Self.itemKeys(input)) {
            for artifact in Self.itemArtifacts(input) { await model.loadPreview(artifact) }
        }
    }
}

extension LiveFeed {
    /// Changes when an approval message arrives or a task's open approvals change.
    /// Changes whenever a message of yours comes back from the hub (its client id), and with the newest message.
    static func arrivalKey(_ input: FeedInput) -> String {
        ([input.messages.last?.id ?? ""] + input.messages.compactMap(\.clientId)).joined(separator: ",")
    }

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

/// What the feed watches to bring a linked message into view.
struct FocusKey: Equatable {
    var focus: String?
    var ids: [String]
}

/// Header on top, the feed in the middle, a problem line under it. The composer and the tab bar go under this.
/// The feed's spacing and margins (the wireframes' 8 and 10 px on a 290-pt phone, scaled to 390 pt).
enum FeedMetrics {
    static let spacing: CGFloat = PixelMetrics.steps(6)
    static let inset: CGFloat = PixelMetrics.steps(7)
}

/// What the avatar header opens (wireframe 6B): Marcel's notebook and Settings. A tap opens the only one, or asks
/// which when there are both; VoiceOver gets each as a named action and a hint that says what the tap does.
public struct AvatarLinks {
    public var notebook: (() -> Void)?
    public var settings: (() -> Void)?
    /// The agent's name, for "<name>'s notebook" (the header sets it).
    public var agentName = AgentName.fallback

    public init(notebook: (() -> Void)? = nil, settings: (() -> Void)? = nil) {
        self.notebook = notebook
        self.settings = settings
    }

    /// The named actions, in menu order.
    public var actions: [(name: String, run: () -> Void)] {
        [notebook.map { ("\(agentName)'s notebook", $0) }, settings.map { ("Settings", $0) }].compactMap { $0 }
    }

    /// "Opens Marcel's notebook or Settings".
    public var hint: String {
        "Opens " + actions.map(\.name).joined(separator: " or ")
    }
}

struct ChatLayout<Feed: View, ComposerView: View>: View {
    /// The header's links, saying the agent's name ("<name>'s notebook").
    private var named: AvatarLinks {
        var links = avatar
        links.agentName = header.name
        return links
    }

    var header: ChatHeaderState
    var problem: String?
    /// What a tap on the header opens.
    var avatar: AvatarLinks
    var composer: ComposerView
    var feed: Feed

    @State private var choosing = false
    @Environment(\.colorScheme) private var scheme

    init(header: ChatHeaderState, problem: String?, avatar: AvatarLinks = AvatarLinks(),
         @ViewBuilder composer: () -> ComposerView, @ViewBuilder feed: () -> Feed) {
        self.header = header
        self.problem = problem
        self.avatar = avatar
        self.composer = composer()
        self.feed = feed()
    }

    var body: some View {
        let palette = ChatPalette(scheme)
        let avatar = named
        let links = avatar.actions
        VStack(spacing: 0) {
            if !links.isEmpty {
                AvatarHeader(state: header)
                    .contentShape(Rectangle())
                    .onTapGesture {
                        if links.count == 1 { links[0].run() } else { choosing = true }
                    }
                    .accessibilityAddTraits(.isButton)
                    .accessibilityHint(avatar.hint)
                    .accessibilityActions {
                        ForEach(Array(links.enumerated()), id: \.offset) { _, link in
                            Button(link.name, action: link.run)
                        }
                    }
                    .confirmationDialog("Open", isPresented: $choosing) {
                        ForEach(Array(links.enumerated()), id: \.offset) { _, link in
                            Button(link.name, action: link.run)
                        }
                        Button("Cancel", role: .cancel) {}
                    }
            } else {
                AvatarHeader(state: header)
            }
            feed
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .bottom)
            if let problem {
                Text(problem)
                    .font(.footnote)
                    .foregroundStyle(ChatCardKind.failed.label(in: palette).color)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, FeedMetrics.inset)
                    .padding(.bottom, PixelMetrics.steps(2))
                    .accessibilityLabel("Problem: \(problem)")
            }
            composer
        }
        // The chat's own ground (wireframe Q1): near-white, the soft ground being the other tabs'.
        .background(palette.screen.color)
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
        let palette = ChatPalette(scheme)
        Button(action: action) {
            HStack(spacing: 6) {
                Text("Latest").font(.subheadline.weight(.semibold))
                Image(systemName: "chevron.down").font(.caption.weight(.bold))
            }
            .foregroundStyle(palette.ink.color)
            .padding(.horizontal, 14)
            .frame(minHeight: PixelMetrics.minTapTarget)
            .background(palette.card.color, in: Capsule())
            .overlay(Capsule().strokeBorder(palette.line.color, lineWidth: 1))
            .shadow(color: .black.opacity(0.12), radius: 4, y: 2)
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Jump to the latest message")
    }
}
