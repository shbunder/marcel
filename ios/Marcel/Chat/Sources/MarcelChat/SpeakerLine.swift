import AvatarKit
import PixelKit
import SwiftUI

/// Above a reply when several helpers speak in one chat and the speaker changes (B-44, ADR-007): the helper's
/// 16-pt avatar and name. With one helper it never shows; the header carries the avatar.
struct SpeakerLine: View {
    var speaker: Speaker

    @Environment(\.colorScheme) private var scheme
    @Environment(\.chatStillAvatars) private var still

    var body: some View {
        HStack(spacing: 6) {
            ChatAvatar(state: .idle, box: 16, still: still, name: speaker.name, agentId: speaker.agentId)
                .accessibilityHidden(true)
            Text(speaker.name)
                .font(.footnote.weight(.semibold))
                .foregroundStyle(ChatPalette(scheme).muted.color)
                .lineLimit(1)
        }
        .padding(.top, 4)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(speaker.name) says")
        .accessibilityAddTraits(.isHeader)
    }
}
