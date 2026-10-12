import PixelKit
import SwiftUI

/// Where a quest card leads until the quest view exists: the card as it stands and what comes next.
struct QuestPlaceholder: View {
    var line: QuestLine
    @Environment(\.colorScheme) private var scheme

    var body: some View {
        let theme = PixelTheme(scheme)
        VStack(alignment: .leading, spacing: PixelMetrics.steps(6)) {
            QuestCard(title: line.title, state: line.state, project: line.project, tone: line.tone,
                      detail: line.detail)
            PixelText("The full quest view, with its steps and a way to talk to it, comes in a later update.",
                      role: .body)
                .foregroundStyle(theme.muted.color)
                .fixedSize(horizontal: false, vertical: true)
            Spacer()
        }
        .padding(FeedMetrics.inset)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(theme.background.color)
    }
}

/// Where an item card leads until the item viewer exists: its preview, and its link when it has one.
struct ItemPlaceholder: View {
    var line: ItemLine
    @Environment(\.colorScheme) private var scheme

    var body: some View {
        let theme = PixelTheme(scheme)
        VStack(alignment: .leading, spacing: PixelMetrics.steps(6)) {
            ArtifactCard(title: line.title, badge: line.badge) {
                ForEach(line.rows, id: \.self) { ArtifactRow($0.label, value: $0.value, emphasised: $0.emphasised) }
            }
            if let url = line.url {
                LinkButton(link: .init(title: "Open \(line.title)", url: url))
            }
            PixelText("The full item viewer comes in a later update.", role: .body)
                .foregroundStyle(theme.muted.color)
                .fixedSize(horizontal: false, vertical: true)
            Spacer()
        }
        .padding(FeedMetrics.inset)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(theme.background.color)
    }
}
