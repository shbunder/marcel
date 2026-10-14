import PixelKit
import SwiftUI

// The special messages (ADR-007): the only boxes in the chat, each a coloured frame with a short pixel-font label.
// Started (teal), needs you (gold, tinted), done (rose, with the item), failed (red, with what went wrong).

extension EnvironmentValues {
    /// Your bubble's colour: your helper's look colour (as drawn in light mode); nil for Marcel's rose.
    @Entry public var chatBubbleColor: PixelColor?
    /// "Now", for the time lines ("Today 08:12"); nil means the real clock. Snapshots pin it.
    @Entry public var chatNow: Date?
    /// Your message a "re:" line just jumped to: it lights up for a moment (B-41).
    @Entry public var chatHighlight: String?
}

/// A thread's quest card, following its task: started while it runs, needs you, done, or failed with the reason.
/// Tapping it opens the quest.
struct QuestRow: View {
    var line: QuestLine
    var actions: ChatActions

    @Environment(\.colorScheme) private var scheme

    /// The card's colour for the task's state.
    static func kind(_ state: StateBadge.Kind) -> ChatCardKind {
        switch state {
        case .needsYou, .silent: .needsYou
        case .ready, .done: .done
        case .failed, .stopped: .failed
        case .queued, .starting, .working, .unknown: .started
        }
    }

    /// The card's label.
    static func label(_ state: StateBadge.Kind) -> String {
        switch state {
        case .queued: "Queued"
        case .starting, .working, .unknown: "Started"
        case .needsYou: "Needs you"
        case .silent: "Gone quiet"
        case .ready: "Ready"
        case .done: "Done"
        case .failed: "Failed"
        case .stopped: "Stopped"
        }
    }

    /// What the card's link says.
    static func open(_ state: StateBadge.Kind) -> String {
        switch state {
        case .failed: "See why ›"
        case .needsYou: "Answer ›"
        case .done, .ready, .stopped: "Open ›"
        default: "Watch ›"
        }
    }

    var meta: String? {
        line.location.map { $0 == .nuc ? "NUC" : "Cloud" }
    }

    var body: some View {
        let palette = ChatPalette(scheme)
        let kind = Self.kind(line.state)
        Button { actions.openQuest(line) } label: {
            ChatCard(kind, label: Self.label(line.state), meta: meta) {
                Text(line.title)
                    .font(.body.weight(.bold))
                    .foregroundStyle(palette.ink.color)
                    .fixedSize(horizontal: false, vertical: true)
                // Where it runs is on the label line (wireframe Q1: "▶ STARTED · NUC"), so it is not said twice.
                if let detail = line.detail, detail != line.location.map(ChatFeed.location) {
                    Text(detail)
                        .font(.subheadline)
                        .foregroundStyle(palette.muted.color)
                        .fixedSize(horizontal: false, vertical: true)
                }
                // The project tag, then the link, on one row from the left (wireframe Q1).
                HStack(alignment: .center, spacing: 8) {
                    if let project = line.project {
                        ChatTag(project)
                    }
                    Text(Self.open(line.state))
                        .font(.subheadline.weight(.bold))
                        .foregroundStyle(kind.label(in: palette).color)
                }
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(accessibilityText)
        .accessibilityHint("Opens the quest")
        .accessibilityAddTraits(.isButton)
    }

    /// "Started: Energy contracts. On the NUC. In Home energy."
    var accessibilityText: String {
        [Self.label(line.state) + ": " + line.title, line.detail, line.project.map { "In \($0)" }]
            .compactMap { $0 }
            .joined(separator: ". ")
    }
}

/// A thread asking for your OK: the gold, tinted card with the question and Approve / Not now / Details. Once answered
/// (here, in a terminal, or never) the buttons grey out and the outcome is said underneath.
struct ApprovalRow: View {
    var line: ApprovalLine
    var actions: ChatActions

    @Environment(\.colorScheme) private var scheme
    @Environment(\.dynamicTypeSize) private var dynamicType

    var body: some View {
        let palette = ChatPalette(scheme)
        ChatCard(.needsYou, label: "Needs you") {
            Text(line.question)
                .font(.body)
                .foregroundStyle(palette.ink.color)
                .fixedSize(horizontal: false, vertical: true)
            buttons
                .disabled(!line.status.isOpen)
            if line.showsDetail, let detail = line.detail {
                Text(detail)
                    .font(.subheadline)
                    .foregroundStyle(palette.muted.color)
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityLabel("Details: \(detail)")
            }
            if let outcome = line.outcome {
                Text(outcome)
                    .font(.subheadline)
                    .foregroundStyle(palette.muted.color)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    @ViewBuilder private var buttons: some View {
        let layout = dynamicType.isAccessibilitySize
            ? AnyLayout(VStackLayout(alignment: .leading, spacing: 8)) : AnyLayout(HStackLayout(spacing: 8))
        layout {
            ChatCardButton("Approve", kind: .needsYou, filled: true) { actions.answer(line.approvalId, true) }
            ChatCardButton("Not now", kind: .needsYou) { actions.answer(line.approvalId, false) }
            if line.detail != nil {
                ChatCardButton(line.showsDetail ? "Hide details" : "Details", kind: .needsYou) {
                    actions.toggleDetails(line.approvalId)
                }
            }
        }
    }
}

/// An item a thread made: the rose "done" card with the item's title and first lines (it replaces the "YOU GOT"
/// banner). The morning board shows the same way, labelled "Board". Tapping the card opens the item.
struct ItemRow: View {
    var line: ItemLine
    var actions: ChatActions

    var label: String {
        if line.isDigest { return "Board" }
        return line.showsBanner ? "Done · you got an item" : line.badge
    }

    /// The preview's lines: "Per year: €1,284", or the line alone.
    var lines: [String] {
        line.rows.prefix(3).map { row in row.value.map { "\(row.label): \($0)" } ?? row.label }
    }

    var body: some View {
        Button { actions.openItem(line) } label: {
            ChatCard(.done, label: label, meta: line.savedTo) {
                ChatItemPreview(title: line.title, lines: lines)
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(([label + ": " + line.title] + lines).joined(separator: ". "))
        .accessibilityHint(line.isDigest ? "Opens the board" : "Opens the item")
        .accessibilityAddTraits(.isButton)
    }
}
