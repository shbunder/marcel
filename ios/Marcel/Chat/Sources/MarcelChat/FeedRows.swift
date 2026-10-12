import AvatarKit
import PixelKit
import SwiftUI

/// What the feed's rows can ask the screen to do.
public struct ChatActions {
    public var openLink: (URL) -> Void = { _ in }

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

    var body: some View {
        switch item {
        case .user(let line):
            UserBubble(text: line.text)
        case .marcel(let line):
            MarcelSays(body: line.body, actions: actions)
        }
    }
}

/// Your message: a teal box on the right, at most four fifths of the width.
struct UserBubble: View {
    var text: String

    @Environment(\.colorScheme) private var scheme

    var body: some View {
        let theme = PixelTheme(scheme)
        HStack {
            Spacer(minLength: 56)
            PixelText(text, role: .body)
                .foregroundStyle(UserBubble.ink(scheme).color)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.horizontal, PixelMetrics.padding)
                .padding(.vertical, PixelMetrics.steps(4))
                .pixelBox(fill: .teal, line: theme.line, shadow: theme.shadow, border: PixelMetrics.thinBorder,
                          offset: PixelMetrics.smallShadow)
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("You said: \(text)")
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

    init(text: String, links: [ChatMarkdown.Link] = [], @ViewBuilder accessory: () -> Accessory) {
        self.text = text
        self.links = links
        self.accessory = accessory()
    }

    var body: some View {
        HStack(alignment: .top, spacing: PixelMetrics.steps(3)) {
            ChatAvatar(state: .idle, size: .small, still: still)
                .padding(.top, PixelMetrics.steps(1))
                .accessibilityHidden(true)
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
