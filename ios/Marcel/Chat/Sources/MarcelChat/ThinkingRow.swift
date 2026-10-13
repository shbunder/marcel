import AvatarKit
import PixelKit
import SwiftUI

/// At the end of the chat while the agent thinks or works (`agent.state`): the small avatar playing its loop and
/// "Marcel is working…". With Reduce Motion the avatar holds its first frame (AvatarKit), and in snapshots it is still.
struct ThinkingRow: View {
    var state: AvatarState
    var name: String

    @Environment(\.colorScheme) private var scheme
    @Environment(\.chatStillAvatars) private var still

    /// Shown only while the agent is busy.
    static func shows(_ state: AvatarState) -> Bool {
        switch state {
        case .thinking, .working: true
        default: false
        }
    }

    var text: String { "\(name) is working…" }

    var body: some View {
        HStack(spacing: 8) {
            ChatAvatar(state: state, box: 28, still: still, name: name)
                .accessibilityHidden(true)
            Text(text)
                .font(.subheadline)
                .foregroundStyle(ChatPalette(scheme).muted.color)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(text)
    }
}
