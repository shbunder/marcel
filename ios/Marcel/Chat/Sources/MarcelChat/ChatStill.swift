import MarcelItems
import PixelKit
import SwiftUI

/// The chat screen without scrolling: header, the rows, and the tab bar, laid out top to bottom. What the snapshot
/// tests and the previews draw; `ImageRenderer` cannot draw a scroll view's content.
struct ChatStill: View {
    var header: ChatHeaderState
    var items: [FeedItem]
    var collapsed = false
    var actions = ChatActions()
    var draft = ""
    /// An item going with the next message, above the composer.
    var attachment: String?

    var body: some View {
        VStack(spacing: 0) {
            AvatarHeader(state: header, collapsed: collapsed)
            VStack(alignment: .leading, spacing: FeedMetrics.spacing) {
                FeedStack(items: items, actions: actions)
                if ThinkingRow.shows(header.avatar) {
                    ThinkingRow(state: header.avatar, name: header.name)
                }
            }
            .padding(FeedMetrics.inset)
            .frame(maxWidth: .infinity, alignment: .leading)
            if let attachment {
                AttachedItem(title: attachment, remove: {})
                    .padding(.horizontal, FeedMetrics.inset)
                    .padding(.top, PixelMetrics.steps(2))
            }
            Composer(text: .constant(draft),
                     placeholder: Composer.placeholder(questionWaiting: ChatFeed.hasOpenQuestion(items),
                                                      name: header.name),
                     focused: nil, send: {})
            PixelTabBar(selection: .constant("chat"))
        }
        .environment(\.agentName, header.name)
    }
}
