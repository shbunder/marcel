import MarcelItems
import MarcelKit
import PixelKit
import SwiftData
import SwiftUI

/// What the side chat's frame says, worked out from the model, the task behind the thread (if any) and its messages.
/// A pure value, so the tests check the words the sheet shows.
struct SideChatLook: Equatable {
    /// Under "Side chat": "Sees the main chat", or which quest replies go to.
    var detail: String
    var placeholder: String
    /// The thread is a task's: "Open quest" shows.
    var taskId: String?
    /// The task has stopped or failed: "This task has stopped; start a new one?".
    var stopped = false
    /// There is an answer to send to the main chat.
    var canSendToMain = false
    var sentToMain = false
    var problem: String?
    var opening = false

    static let stoppedText = SideChatError.taskFinished.message

    @MainActor
    static func of(_ model: SideChatModel, task: HubTask?, messages: [Message]) -> SideChatLook {
        var look = SideChatLook(detail: "Sees the main chat", placeholder: "Reply in side chat…")
        if let taskId = model.taskId {
            look.taskId = taskId
            look.detail = "Replies go to \(task?.title ?? "the quest")"
            switch task?.state {
            case .done: look.placeholder = "Ask a follow-up…"
            default: look.placeholder = "Talk to this quest…"
            }
            look.stopped = model.taskFinished || task?.state == .stopped || task?.state == .failed
        }
        look.canSendToMain = model.conversationId != nil && SideChatModel.result(in: messages) != nil
        look.sentToMain = model.sentToMain != nil
        look.problem = model.problem == stoppedText && look.stopped ? nil : model.problem
        look.opening = model.opening && model.conversationId == nil
        return look
    }
}

/// What the sheet's buttons do.
struct SideChatActions {
    var close: () -> Void = {}
    var openQuest: (String) -> Void = { _ in }
    var sendToMain: () -> Void = {}
    var startOver: () -> Void = {}
    var send: () -> Void = {}
}

/// The side chat sheet (wireframe 2B): "Side chat · sees the main chat", the message it is on (dimmed), the thread,
/// "Send result to main chat", and a composer that replies in the thread only. The live sheet and the snapshots
/// both draw this; the live one puts `feed` in a scroll view.
struct SideChatLayout<Feed: View, ComposerView: View>: View {
    var parent: SideChatParent
    var look: SideChatLook
    var actions: SideChatActions
    var feed: Feed
    var composer: ComposerView

    @Environment(\.colorScheme) private var scheme

    init(parent: SideChatParent, look: SideChatLook, actions: SideChatActions, @ViewBuilder composer: () -> ComposerView,
         @ViewBuilder feed: () -> Feed) {
        self.parent = parent
        self.look = look
        self.actions = actions
        self.composer = composer()
        self.feed = feed()
    }

    var body: some View {
        let theme = PixelTheme(scheme)
        VStack(spacing: 0) {
            SideChatTop(look: look, actions: actions)
            VStack(alignment: .leading, spacing: FeedMetrics.spacing) {
                ChatQuote(speaker: "On: \(parent.speaker)", text: parent.text)
                    .accessibilityElement(children: .ignore)
                    .accessibilityLabel("Side chat on what \(parent.speaker) said: \(parent.text)")
                    .padding(.horizontal, FeedMetrics.inset)
                    .padding(.top, FeedMetrics.inset)
                feed
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            if look.stopped {
                SideChatNotice(startOver: actions.startOver)
                    .padding(.horizontal, FeedMetrics.inset)
                    .padding(.bottom, PixelMetrics.steps(2))
            }
            VStack(alignment: .leading, spacing: PixelMetrics.steps(1)) {
                Button(action: actions.sendToMain) {
                    SideChatChip(title: "Send result to main chat",
                                 ink: (look.canSendToMain ? ProjectChip.Tone.teal.color(in: theme) : theme.muted))
                }
                .buttonStyle(.plain)
                .disabled(!look.canSendToMain)
                .accessibilityHint(look.canSendToMain ? "Asks first, and shows what will be sent"
                                                      : "No answer in this side chat yet")
                if look.sentToMain {
                    PixelText("Sent to the main chat.", role: .detail)
                        .foregroundStyle(theme.muted.color)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, FeedMetrics.inset)
            .padding(.top, PixelMetrics.steps(2))
            if let problem = look.problem {
                PixelText(problem, role: .detail)
                    .foregroundStyle(theme.accent.color)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.horizontal, FeedMetrics.inset)
                    .padding(.top, PixelMetrics.steps(2))
                    .accessibilityLabel("Problem: \(problem)")
            }
            composer
        }
        .background(theme.background.color)
    }
}

/// "Side chat" and what it sees, with "Open quest" for a task's thread and a close button.
struct SideChatTop: View {
    var look: SideChatLook
    var actions: SideChatActions

    @Environment(\.colorScheme) private var scheme
    @Environment(\.dynamicTypeSize) private var dynamicType

    var body: some View {
        let theme = PixelTheme(scheme)
        let layout = dynamicType.isAccessibilitySize
            ? AnyLayout(VStackLayout(alignment: .leading, spacing: PixelMetrics.steps(2)))
            : AnyLayout(HStackLayout(spacing: PixelMetrics.steps(3)))
        layout {
            VStack(alignment: .leading, spacing: PixelMetrics.steps(1)) {
                PixelText("Side chat", role: .label)
                    .foregroundStyle(theme.text.color)
                PixelText(look.detail, role: .detail)
                    .foregroundStyle(theme.muted.color)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .accessibilityElement(children: .combine)
            .accessibilityAddTraits(.isHeader)
            Spacer(minLength: 0)
            HStack(spacing: PixelMetrics.steps(3)) {
                if let taskId = look.taskId {
                    Button { actions.openQuest(taskId) } label: {
                        SideChatChip(title: "Open quest", ink: ProjectChip.Tone.blue.color(in: theme))
                    }
                    .buttonStyle(.plain)
                    .accessibilityHint("Opens the quest this side chat talks to")
                }
                Button(action: actions.close) {
                    PixelText("X", role: .label)
                        .foregroundStyle(theme.muted.color)
                        .frame(width: PixelMetrics.minTapTarget, height: PixelMetrics.minTapTarget)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Close the side chat")
            }
        }
        .padding(.leading, FeedMetrics.inset)
        .padding(.trailing, PixelMetrics.steps(2))
        .padding(.vertical, PixelMetrics.steps(2))
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(theme.bar.color)
        .overlay(alignment: .bottom) {
            Rectangle().fill(theme.line.color).frame(height: PixelMetrics.thinBorder)
        }
    }
}

/// "This task has stopped; start a new one?" with the button that does it.
struct SideChatNotice: View {
    var startOver: () -> Void

    @Environment(\.colorScheme) private var scheme

    var body: some View {
        let theme = PixelTheme(scheme)
        VStack(alignment: .leading, spacing: PixelMetrics.steps(2)) {
            PixelText(SideChatLook.stoppedText, role: .body)
                .foregroundStyle(theme.text.color)
                .fixedSize(horizontal: false, vertical: true)
            Button(action: startOver) {
                SideChatChip(title: "Start a new one", ink: ProjectChip.Tone.rose.color(in: theme))
            }
            .buttonStyle(.plain)
            .accessibilityHint("Closes the side chat and puts what you wrote in the main chat")
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(PixelMetrics.steps(4))
        .pixelBox(fill: theme.dialog, line: theme.line, shadow: nil, border: PixelMetrics.thinBorder, offset: 0)
    }
}

/// An outlined rounded label in the chat's language, at least a tap target tall (the quest view's chips).
struct SideChatChip: View {
    var title: String
    var ink: PixelColor

    @Environment(\.colorScheme) private var scheme

    var body: some View {
        Text(title)
            .font(.subheadline.weight(.semibold))
            .foregroundStyle(ink.color)
            .fixedSize(horizontal: false, vertical: true)
            .padding(.horizontal, 14)
            .padding(.vertical, 6)
            .frame(minHeight: PixelMetrics.minTapTarget)
            .background(ChatPalette(scheme).card.color, in: Capsule())
            .overlay(Capsule().strokeBorder(ink.color, lineWidth: 1.5))
            .contentShape(Capsule())
    }
}

/// "n replies" under a message in the main chat: opens its side chat.
struct RepliesButton: View {
    var summary: ThreadSummary
    var open: () -> Void

    @Environment(\.colorScheme) private var scheme

    var body: some View {
        let theme = PixelTheme(scheme)
        Button(action: open) {
            HStack(spacing: PixelMetrics.steps(2)) {
                PixelText(summary.label, role: .smallLabel)
                PixelGlyph(.pointer, color: theme.accent)
            }
            .foregroundStyle(theme.accent.color)
            .frame(minHeight: PixelMetrics.minTapTarget)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel(summary.accessibilityLabel)
        .accessibilityHint("Opens the side chat")
    }
}

// MARK: The live sheet

/// The side chat as a sheet over the main chat. Reads the thread from the cache (`.modelContainer` comes from the
/// chat), which the live stream keeps current.
public struct SideChatSheet: View {
    let model: SideChatModel
    /// What the thread's rows can do (answer an approval, open an item): the main chat's own actions.
    var rowActions: ChatActions
    /// Approvals as the main chat holds them, so a card in the thread shows the same state.
    var approvals: ChatLocalState
    var openQuest: (String) -> Void
    /// "Start a new one": the words for the main composer.
    var startOver: (String) -> Void

    @Environment(\.dismiss) private var dismiss
    @Environment(\.modelContext) private var context

    init(model: SideChatModel, rowActions: ChatActions, approvals: ChatLocalState,
         openQuest: @escaping (String) -> Void, startOver: @escaping (String) -> Void) {
        self.model = model
        self.rowActions = rowActions
        self.approvals = approvals
        self.openQuest = openQuest
        self.startOver = startOver
    }

    public var body: some View {
        Group {
            if let conversationId = model.conversationId {
                SideChatLive(model: model, conversationId: conversationId, rowActions: rowActions,
                             approvals: approvals, actions: actions)
            } else {
                SideChatLayout(parent: model.parent, look: .of(model, task: nil, messages: []), actions: actions,
                               composer: { SideComposer(model: model, placeholder: "Reply in side chat…") }) {
                    PixelText(model.problem == nil ? "Opening the side chat…" : "Couldn't open the side chat.",
                              role: .detail)
                        .frame(maxWidth: .infinity)
                        .padding(FeedMetrics.inset)
                }
            }
        }
        .task {
            if model.cache == nil { model.cache = MarcelCache(container: context.container) }
            await model.open()
        }
    }

    private var actions: SideChatActions {
        SideChatActions(
            close: { dismiss() },
            openQuest: { id in
                dismiss()
                openQuest(id)
            },
            sendToMain: {},
            startOver: {
                let words = model.startOver()
                dismiss()
                startOver(words)
            },
            send: { Task { await model.send() } })
    }
}

/// The sheet once the thread is known: queries its messages and lays them out.
struct SideChatLive: View {
    let model: SideChatModel
    let conversationId: String
    var rowActions: ChatActions
    var approvals: ChatLocalState
    var actions: SideChatActions
    @Query private var window: [CachedMessage]
    @Query private var tasks: [CachedTask]
    @Query private var artifacts: [CachedArtifact]
    @Query private var projects: [CachedProject]
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    static let limit = 200

    init(model: SideChatModel, conversationId: String, rowActions: ChatActions, approvals: ChatLocalState,
         actions: SideChatActions) {
        self.model = model
        self.conversationId = conversationId
        self.rowActions = rowActions
        self.approvals = approvals
        self.actions = actions
        _window = Query(FeedInput.messageWindow(conversationId: conversationId, limit: Self.limit))
    }

    var body: some View {
        let input = FeedInput.decode(newestMessages: window, limit: Self.limit, tasks: tasks, artifacts: artifacts,
                                     projects: projects)
        let items = SideChatFeed.items(input, model: model, approvals: approvals)
        let task = model.taskId.flatMap { input.tasks[$0] }
        let look = SideChatLook.of(model, task: task, messages: input.messages)
        var actions = actions
        actions.sendToMain = { model.proposeSendToMain(input.messages) }
        var rows = rowActions
        rows.retry = { id in Task { await model.retry(clientId: id) } }
        rows.reply = nil  // no thread inside a thread
        // A card in the thread opens over the main chat, so the sheet closes first.
        rows.openQuest = { [actions] line in actions.openQuest(line.taskId) }
        rows.openItem = { [actions, rowActions] line in
            actions.close()
            rowActions.openItem(line)
        }
        return SideChatLayout(parent: model.parent, look: look, actions: actions,
                              composer: { SideComposer(model: model, placeholder: look.placeholder) }) {
            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: FeedMetrics.spacing) {
                        FeedStack(items: items, actions: rows)
                    }
                    .padding(.horizontal, FeedMetrics.inset)
                }
                .defaultScrollAnchor(.bottom)
                .onChange(of: items.last?.id) { _, last in
                    guard let last else { return }
                    withAnimation(reduceMotion ? nil : .default) { proxy.scrollTo(last, anchor: .bottom) }
                }
            }
        }
        .onChange(of: input.messages.compactMap(\.clientId), initial: true) { model.prune(input.messages) }
        .confirmationDialog("Send this to the main chat?", isPresented: Binding(
            get: { model.proposal != nil }, set: { if !$0 { model.cancelSendToMain() } }),
                            titleVisibility: .visible) {
            Button("Send to main chat") { model.confirmSendToMain() }
            Button("Cancel", role: .cancel) { model.cancelSendToMain() }
        } message: {
            Text(SideChatFeed.confirmation(model.proposal))
        }
    }
}

/// The composer, replying in the thread only. Its text is the thread's own draft.
struct SideComposer: View {
    let model: SideChatModel
    var placeholder: String

    var body: some View {
        @Bindable var model = model
        Composer(text: $model.draft, placeholder: placeholder, fieldLabel: "Reply in the side chat") {
            Task { await model.send() }
        }
    }
}

enum SideChatFeed {
    /// The thread's rows, as the main chat draws them, plus your replies on their way.
    @MainActor
    static func items(_ input: FeedInput, model: SideChatModel, approvals: ChatLocalState) -> [FeedItem] {
        var local = approvals
        local.pending = model.pending
        return ChatFeed.items(input, local: local)
    }

    /// The confirmation's words: exactly what will be quoted, cut short if it is long.
    static func confirmation(_ proposal: SendToMainProposal?) -> String {
        guard let proposal else { return "" }
        let text = proposal.preview.count > 280 ? String(proposal.preview.prefix(280)) + "…" : proposal.preview
        return "The main chat will show this as a quote:\n\n“\(text)”"
    }
}

/// The side chat without scrolling or a text field: what the snapshot tests draw.
struct SideChatStill: View {
    var parent: SideChatParent
    var look: SideChatLook
    var items: [FeedItem]
    var draft = ""

    var body: some View {
        SideChatLayout(parent: parent, look: look, actions: SideChatActions(), composer: {
            Composer(text: .constant(draft), placeholder: look.placeholder, send: {})
        }) {
            VStack(alignment: .leading, spacing: FeedMetrics.spacing) {
                FeedStack(items: items, actions: ChatActions())
            }
            .padding(.horizontal, FeedMetrics.inset)
        }
    }
}
