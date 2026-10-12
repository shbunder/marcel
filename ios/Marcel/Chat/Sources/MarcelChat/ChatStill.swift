import PixelKit
import SwiftUI

/// The chat screen without scrolling: header, the rows, and the tab bar, laid out top to bottom. What the snapshot
/// tests and the previews draw; `ImageRenderer` cannot draw a scroll view's content.
struct ChatStill: View {
    var header: ChatHeaderState
    var items: [FeedItem]
    var collapsed = false
    var actions = ChatActions()

    var body: some View {
        VStack(spacing: 0) {
            ChatHeader(state: header, collapsed: collapsed)
            VStack(alignment: .leading, spacing: FeedMetrics.spacing) {
                FeedStack(items: items, actions: actions)
            }
            .padding(FeedMetrics.inset)
            .frame(maxWidth: .infinity, alignment: .leading)
            PixelTabBar(selection: .constant("chat"))
        }
    }
}
