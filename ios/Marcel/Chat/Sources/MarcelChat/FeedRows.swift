import AvatarKit
import PixelKit
import SwiftUI

/// What the feed's rows can ask the screen to do.
public struct ChatActions {
    public var openQuest: (QuestLine) -> Void = { _ in }
    public var openItem: (ItemLine) -> Void = { _ in }
    public var answer: (_ approvalId: String, _ approve: Bool) -> Void = { _, _ in }
    public var toggleDetails: (_ approvalId: String) -> Void = { _ in }
    public var retry: (_ clientId: String) -> Void = { _ in }

    public init() {}
}

/// The rows, top to bottom, with the feed's spacing. The live screen puts this in a scroll view; snapshots draw it
/// as it is.
struct FeedStack: View {
    var items: [FeedItem]
    var actions: ChatActions

    var body: some View {
        ForEach(items) { item in
            FeedRow(item: item, actions: actions)
                .id(item.id)
        }
    }
}

struct FeedRow: View {
    var item: FeedItem
    var actions: ChatActions
    @Environment(\.dynamicTypeSize) private var dynamicType

    var body: some View {
        switch item {
        case .user(let line):
            if line.status == .failed, let clientId = line.clientId {
                Button { actions.retry(clientId) } label: { UserBubble(line: line) }
                    .buttonStyle(.plain)
                    .accessibilityLabel(line.accessibilityText)
            } else {
                UserBubble(line: line)
            }
        case .marcel(let line):
            VStack(alignment: .leading, spacing: PixelMetrics.steps(4)) {
                MarcelSays(body: line.body, actions: actions)
                if let quest = line.quest {
                    // The thread Marcel routed this to, live: the same card as where it started.
                    QuestRow(line: quest, actions: actions)
                        .padding(.leading, dynamicType.isAccessibilitySize ? 0 : MarcelSays<EmptyView>.indent)
                }
            }
        case .quest(let line):
            QuestRow(line: line, actions: actions)
        case .approval(let line):
            ApprovalRow(line: line, actions: actions)
        case .item(let line):
            ItemRow(line: line, actions: actions)
        }
    }
}

/// Your message: a teal box on the right, at most four fifths of the width. One on its way says "Sending…"
/// under it; one that did not get through says so, and tapping it sends it again.
struct UserBubble: View {
    var line: UserLine

    @Environment(\.colorScheme) private var scheme

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
                Spacer(minLength: 56)
                PixelText(line.text, role: .body)
                    .foregroundStyle(UserBubble.ink(scheme).color)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.horizontal, PixelMetrics.padding)
                    .padding(.vertical, PixelMetrics.steps(4))
                    .pixelBox(fill: .teal, line: theme.line, shadow: theme.shadow, border: PixelMetrics.thinBorder,
                              offset: PixelMetrics.smallShadow)
            }
            switch line.status {
            case .sent: EmptyView()
            case .sending:
                PixelText("Sending…", role: .smallLabel).foregroundStyle(theme.muted.color)
            case .failed:
                PixelText("Not sent · tap to try again", role: .smallLabel).foregroundStyle(theme.accent.color)
            }
        }
        .frame(maxWidth: .infinity, alignment: .trailing)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(line.accessibilityText)
    }

    /// White on teal in light (5.9:1), cream in dark (5.3:1): both pass AA for the small pixel font.
    static func ink(_ scheme: ColorScheme) -> PixelColor {
        scheme == .dark ? .cream : PixelColor(0xFFFFFF)
    }
}

/// Marcel speaking: the 32-pt giraffe beside a dialogue box. Links in the Markdown become buttons under the text.
struct MarcelSays<Accessory: View>: View {
    var text: String
    var links: [ChatMarkdown.Link]
    var accessory: Accessory

    @Environment(\.chatStillAvatars) private var still
    @Environment(\.dynamicTypeSize) private var dynamicType

    /// How far the box sits from the left edge: the 32-pt giraffe and the gap after it.
    static var indent: CGFloat { AvatarSize.small.points + PixelMetrics.steps(3) }

    init(text: String, links: [ChatMarkdown.Link] = [], @ViewBuilder accessory: () -> Accessory) {
        self.text = text
        self.links = links
        self.accessory = accessory()
    }

    var body: some View {
        HStack(alignment: .top, spacing: PixelMetrics.steps(3)) {
            // At accessibility text sizes the text gets the whole width.
            if !dynamicType.isAccessibilitySize {
                ChatAvatar(state: .idle, size: .small, still: still)
                    .padding(.top, PixelMetrics.steps(1))
                    .accessibilityHidden(true)
            }
            DialogBox(speaker: "Marcel", text: text) {
                if !links.isEmpty || Accessory.self != EmptyView.self {
                    VStack(alignment: .leading, spacing: PixelMetrics.steps(2)) {
                        ForEach(links, id: \.url) { link in LinkButton(link: link) }
                        accessory
                    }
                }
            }
        }
    }
}

extension MarcelSays where Accessory == EmptyView {
    init(body: ChatMarkdown.Rendered, actions: ChatActions) {
        self.init(text: body.text, links: body.links) { EmptyView() }
    }
}

/// A link from Marcel's text, as a tappable line in the accent colour.
struct LinkButton: View {
    var link: ChatMarkdown.Link
    @Environment(\.colorScheme) private var scheme
    @Environment(\.openURL) private var openURL

    var body: some View {
        let theme = PixelTheme(scheme)
        Button { openURL(link.url) } label: {
            HStack(spacing: PixelMetrics.steps(3)) {
                PixelGlyph(.pointer, color: theme.accent)
                PixelText(link.title, role: .detail)
                    .underline()
                    .foregroundStyle(theme.accent.color)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .frame(minHeight: PixelMetrics.minTapTarget, alignment: .leading)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Open link: \(link.title)")
    }
}
