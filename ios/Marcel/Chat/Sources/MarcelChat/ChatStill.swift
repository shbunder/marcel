import MarcelKit
import MarcelItems
import PixelKit
import SwiftUI

/// The chat screen without scrolling: header, the rows, and the tab bar, laid out top to bottom. What the snapshot
/// tests and the previews draw; `ImageRenderer` cannot draw a scroll view's content.
struct ChatStill: View {
    var header: ChatHeaderState
    var items: [FeedItem]
    var actions = ChatActions()
    var draft = ""
    /// An item going with the next message, above the composer.
    var attachment: String?
    /// Mention chips going with the next message, above the composer.
    var mentions: [Mention] = []
    /// The bow tiling behind the messages, and how many bows are coloured; nil draws none.
    var tiling: BowTiling.Accents? = nil
    /// How far the messages have scrolled up under the header, in points (0: the first message sits just below it).
    var scrolled: CGFloat = 0
    /// How strongly messages blur and fade under the header (design comparison).
    var under: UnderHeader = .soft

    var body: some View {
        VStack(spacing: 0) {
            ZStack(alignment: .top) {
                VStack(alignment: .leading, spacing: FeedMetrics.spacing) {
                    FeedStack(items: items, actions: actions)
                }
                .padding(FeedMetrics.inset)
                .padding(.top, AvatarHeader.height - scrolled)
                .frame(maxWidth: .infinity, alignment: .leading)
                .mask { under.mask }
                AvatarHeader(state: header)
            }
            .coordinateSpace(.named(UnderHeader.space))
            .environment(\.underHeader, under)
            .clipped()
            .background { if let tiling { BowTiling(accents: tiling).ignoresSafeArea(edges: .top) } }
            Composer(text: .constant(draft),
                     placeholder: Composer.placeholder(questionWaiting: ChatFeed.hasOpenQuestion(items),
                                                      name: header.name),
                     focused: nil, send: {}) {
                ComposerExtras(mentions: mentions, attachment: attachment, unpick: { _ in }, detach: {})
            }
            PixelTabBar(selection: .constant("chat"))
        }
        .environment(\.agentName, header.name)
    }
}
