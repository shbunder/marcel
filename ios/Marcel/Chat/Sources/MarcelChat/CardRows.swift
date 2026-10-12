import PixelKit
import SwiftUI

/// A thread's quest card. Tapping it opens the quest.
struct QuestRow: View {
    var line: QuestLine
    var actions: ChatActions

    var body: some View {
        Button { actions.openQuest(line) } label: {
            QuestCard(title: line.title, state: line.state, project: line.project, tone: line.tone,
                      detail: line.detail)
        }
        .buttonStyle(.plain)
        .accessibilityHint("Opens the quest")
    }
}

/// A thread asking for your OK, Zelda style: the question in Marcel's box and "▶ Approve  Deny  Details" under it.
/// Once answered (here, in a terminal, or never) the choices grey out and the outcome is said underneath.
struct ApprovalRow: View {
    static let approve = ChoicePrompt.Choice(id: "approve", title: "Approve")
    static let deny = ChoicePrompt.Choice(id: "deny", title: "Deny")
    static let details = ChoicePrompt.Choice(id: "details", title: "Details")

    var line: ApprovalLine
    var actions: ChatActions

    @Environment(\.colorScheme) private var scheme

    var choices: [ChoicePrompt.Choice] {
        line.detail == nil ? [Self.approve, Self.deny] : [Self.approve, Self.deny, Self.details]
    }

    /// Where the ▶ rests: on the answer given, once there is one.
    var marked: ChoicePrompt.Choice.ID? {
        switch line.status {
        case .denied: Self.deny.id
        default: Self.approve.id
        }
    }

    var body: some View {
        let theme = PixelTheme(scheme)
        MarcelSays(text: line.question) {
            ChoicePrompt(choices, marked: marked) { choice in
                switch choice.id {
                case Self.approve.id: actions.answer(line.approvalId, true)
                case Self.deny.id: actions.answer(line.approvalId, false)
                default: actions.toggleDetails(line.approvalId)
                }
            }
            .disabled(!line.status.isOpen)
            if line.showsDetail, let detail = line.detail {
                PixelText(detail, role: .detail)
                    .foregroundStyle(theme.muted.color)
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityLabel("Details: \(detail)")
            }
            if let outcome = line.outcome {
                PixelText(outcome, role: .detail)
                    .foregroundStyle(theme.muted.color)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}

/// An item a thread made: "You got: …!" the first time, then its preview card. The morning board shows as its card
/// alone. Tapping the card opens the item.
struct ItemRow: View {
    var line: ItemLine
    var actions: ChatActions

    var body: some View {
        VStack(alignment: .leading, spacing: PixelMetrics.steps(5)) {
            if line.showsBanner {
                ItemGetBanner(item: line.title, savedTo: line.savedTo)
            }
            Button { actions.openItem(line) } label: {
                ArtifactCard(title: line.title, badge: line.badge) {
                    ForEach(line.rows, id: \.self) { row in
                        ArtifactRow(row.label, value: row.value, emphasised: row.emphasised)
                    }
                }
            }
            .buttonStyle(.plain)
            .accessibilityHint(line.isDigest ? "Opens the board" : "Opens the item")
        }
    }
}
