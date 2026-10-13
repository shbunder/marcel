import AvatarKit
import MarcelItems
import MarcelKit
import PixelKit
import SwiftUI

/// What the feed's rows can ask the screen to do.
public struct ChatActions {
    public var openQuest: (QuestLine) -> Void = { _ in }
    public var openItem: (ItemLine) -> Void = { _ in }
    public var answer: (_ approvalId: String, _ approve: Bool) -> Void = { _, _ in }
    public var toggleDetails: (_ approvalId: String) -> Void = { _ in }
    public var retry: (_ clientId: String) -> Void = { _ in }
    /// "Reply in thread" (long-press, or the VoiceOver action) on a stored message; nil where there are no threads.
    public var reply: ((FeedItem) -> Void)?
    /// "n replies" under each message that has a side thread, by message id (`SideChats.summaries`).
    public var threads: [String: ThreadSummary] = [:]
    /// A "re:" line was tapped: scroll to that message of yours (B-41).
    public var jump: (_ messageId: String) -> Void = { _ in }
    /// A mention chip on a sent message was tapped: open that quest, project room or item (B-42).
    public var openMention: (Mention) -> Void = { _ in }

    public init() {}
}

/// The rows, top to bottom, with the feed's spacing. The live screen puts this in a scroll view; snapshots draw it
/// as it is.
struct FeedStack: View {
    var items: [FeedItem]
    var actions: ChatActions

    var body: some View {
        ForEach(items) { item in
            ThreadedRow(item: item, actions: actions, first: item.id == items.first?.id)
                .id(item.id)
        }
    }
}

/// A row, with "Reply in thread" on a long-press (and as a VoiceOver action) and "n replies" under it once its side
/// chat has replies (B-08). Your messages still on their way, and time lines, have no thread.
struct ThreadedRow: View {
    var item: FeedItem
    var actions: ChatActions
    var first = false

    private var isStored: Bool {
        switch item {
        case .user(let line): line.clientId == nil
        case .time: false
        default: true
        }
    }

    var body: some View {
        if let reply = actions.reply, isStored {
            VStack(alignment: isMine ? .trailing : .leading, spacing: PixelMetrics.steps(1)) {
                FeedRow(item: item, actions: actions, first: first)
                    .contentShape(Rectangle())
                    .contextMenu {
                        Button("Reply in thread") { reply(item) }
                    }
                    .accessibilityAction(named: "Reply in thread") { reply(item) }
                if let summary = actions.threads[item.id] {
                    RepliesButton(summary: summary) { reply(item) }
                }
            }
            .frame(maxWidth: .infinity, alignment: isMine ? .trailing : .leading)
        } else {
            FeedRow(item: item, actions: actions, first: first)
        }
    }

    private var isMine: Bool {
        if case .user = item { true } else { false }
    }
}

struct FeedRow: View {
    var item: FeedItem
    var actions: ChatActions
    var first = false

    @Environment(\.chatNow) private var now
    @Environment(\.calendar) private var calendar
    @Environment(\.timeZone) private var timeZone
    @Environment(\.locale) private var locale

    var body: some View {
        if item.speaker != nil || item.quote != nil {
            VStack(alignment: .leading, spacing: 2) {
                if let speaker = item.speaker { SpeakerLine(speaker: speaker) }
                if let quote = item.quote { ReplyQuoteLine(quote: quote, jump: actions.jump) }
                row
            }
        } else {
            row
        }
    }

    @ViewBuilder private var row: some View {
        switch item {
        case .user(let line):
            VStack(alignment: .trailing, spacing: 0) {
                if line.status == .failed, let clientId = line.clientId {
                    Button { actions.retry(clientId) } label: { UserBubble(line: line) }
                        .buttonStyle(.plain)
                        .accessibilityLabel(line.accessibilityText)
                } else {
                    UserBubble(line: line)
                }
                if !line.mentions.isEmpty {
                    HStack(spacing: 6) {
                        ForEach(line.mentions, id: \.id) { mention in
                            MentionChip(mention: mention, open: { actions.openMention(mention) })
                        }
                    }
                }
            }
            .frame(maxWidth: .infinity, alignment: .trailing)
        case .marcel(let line):
            MarcelReply(line: line, actions: actions)
        case .quest(let line):
            QuestRow(line: line, actions: actions)
        case .approval(let line):
            ApprovalRow(line: line, actions: actions)
        case .item(let line):
            ItemRow(line: line, actions: actions)
        case .time(let line):
            ChatTimeLine(line.text(now: now ?? Date(), first: first, calendar: zoned, locale: locale))
        }
    }
}

extension FeedRow {
    /// The environment's calendar in the environment's time zone (snapshots pin both).
    var zoned: Calendar {
        var calendar = calendar
        calendar.timeZone = timeZone
        return calendar
    }
}

/// Your message: a rounded bubble on the right in your helper's colour, with the notch (bottom-right corner squared
/// off). One on its way says "Sending…" under it; one that did not get through says so, and tapping it sends it again.
struct UserBubble: View {
    var line: UserLine

    @Environment(\.colorScheme) private var scheme
    @Environment(\.chatBubbleColor) private var color
    @Environment(\.chatHighlight) private var highlight
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    init(line: UserLine) {
        self.line = line
    }

    init(text: String) {
        self.init(line: UserLine(id: "", text: text))
    }

    var body: some View {
        let theme = PixelTheme(scheme)
        VStack(alignment: .trailing, spacing: PixelMetrics.steps(2)) {
            HStack {
                Spacer(minLength: 64)
                ChatBubble(line.text, color: color)
                    .overlay {
                        if highlight == line.id {
                            ChatBubbleShape().stroke(ChatPalette(scheme).link.color, lineWidth: 3)
                        }
                    }
                    .animation(reduceMotion ? nil : .easeOut(duration: 0.3), value: highlight == line.id)
            }
            if let project = line.project {
                ProjectChip(project, tone: line.tone)
            }
            if let attachment = line.attachment {
                AttachedItem(title: attachment)
            }
            switch line.status {
            case .sent: EmptyView()
            case .sending:
                PixelText("Sending…", role: .smallLabel).foregroundStyle(theme.muted.color)
            case .failed:
                PixelText(line.itemGone ? "Item gone · tap to send without it" : "Not sent · tap to try again",
                          role: .smallLabel)
                    .foregroundStyle(theme.accent.color)
            }
        }
        .frame(maxWidth: .infinity, alignment: .trailing)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(line.accessibilityText)
    }
}

/// Marcel's reply (ADR-007): plain formatted text across the full width, no bubble and no avatar, drawn by PixelKit's
/// shared Markdown view in its chat style. A follow-up Marcel routed to a quest carries that quest's live card.
struct MarcelReply: View {
    var line: MarcelLine
    var actions: ChatActions

    @Environment(\.openURL) private var openURL

    /// The reply's web links, which VoiceOver offers as actions on the reply ("Open the offers").
    var links: [ItemMarkdown.Link] { line.parts.flatMap(\.links) }

    var body: some View {
        VStack(alignment: .leading, spacing: PixelMetrics.steps(5)) {
            // One element, so "Reply in thread" is on the whole reply; its links stay reachable as named actions.
            MarkdownView(parts: line.parts, style: .chat)
                .accessibilityElement(children: .combine)
                .modifier(LinkActions(links: links, open: { openURL($0) }))
            if let quest = line.quest {
                // The thread Marcel routed this to, live: the same card as where it started.
                QuestRow(line: quest, actions: actions)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// The item that went with a message ("Ask about this"), under your bubble and above the composer: a gold outline
/// with the item's title.
struct AttachedItem: View {
    var title: String
    /// Above the composer: an X takes the item off the message.
    var remove: (() -> Void)?

    @Environment(\.colorScheme) private var scheme

    var body: some View {
        let theme = PixelTheme(scheme)
        let ink = ProjectChip.Tone.gold.color(in: theme).color
        HStack(spacing: PixelMetrics.steps(3)) {
            PixelText("Item", role: .smallLabel).foregroundStyle(ink)
            PixelText(title, role: .detail)
                .foregroundStyle(theme.text.color)
                .lineLimit(1)
            if let remove {
                Spacer(minLength: 0)
                Button(action: remove) {
                    PixelText("X", role: .label)
                        .foregroundStyle(theme.muted.color)
                        .frame(width: PixelMetrics.minTapTarget, height: PixelMetrics.minTapTarget)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Remove the item from the message")
            }
        }
        .padding(.horizontal, PixelMetrics.steps(4))
        .frame(minHeight: remove == nil ? nil : PixelMetrics.minTapTarget)
        .padding(.vertical, remove == nil ? PixelMetrics.steps(2) : 0)
        .overlay(Rectangle().strokeBorder(ink, lineWidth: PixelMetrics.thinBorder))
        .accessibilityElement(children: remove == nil ? .ignore : .contain)
        .accessibilityLabel("Item: \(title)")
    }
}

/// Each web link of a reply as a VoiceOver action ("Open the offers"), opened through `openURL` like a tap.
struct LinkActions: ViewModifier {
    var links: [ItemMarkdown.Link]
    var open: (URL) -> Void

    func body(content: Content) -> some View {
        links.reduce(AnyView(content)) { view, link in
            AnyView(view.accessibilityAction(named: "Open \(link.title)") { open(link.url) })
        }
    }
}
