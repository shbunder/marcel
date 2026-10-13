import PixelKit
import SwiftUI

/// The top strip (S-18.4, the chat's language): a plain "Quests" title and the project filter, on white with a thin
/// line under it.
struct QuestLogHeader<Filter: View>: View {
    var filter: Filter

    init(@ViewBuilder filter: () -> Filter) {
        self.filter = filter()
    }

    @Environment(\.colorScheme) private var scheme
    @Environment(\.dynamicTypeSize) private var dynamicType

    var body: some View {
        let palette = ChatPalette(scheme)
        // At accessibility text sizes the filter goes under the title instead of squeezing it.
        let layout = dynamicType.isAccessibilitySize
            ? AnyLayout(VStackLayout(alignment: .leading, spacing: 4))
            : AnyLayout(HStackLayout(alignment: .center, spacing: 12))
        layout {
            Text("Quests")
                .font(.title3.weight(.bold))
                .foregroundStyle(palette.ink.color)
                .accessibilityAddTraits(.isHeader)
            if !dynamicType.isAccessibilitySize { Spacer(minLength: 0) }
            filter
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 8)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(palette.card.color)
        .overlay(alignment: .bottom) {
            Rectangle().fill(palette.line.color).frame(height: 1)
        }
    }
}

/// What the project filter shows: "All projects ⌄", "Home energy ⌄".
struct FilterLabel: View {
    var title: String

    @Environment(\.colorScheme) private var scheme

    var body: some View {
        let palette = ChatPalette(scheme)
        HStack(spacing: 4) {
            Text(title)
                .font(.subheadline)
                .lineLimit(1)
            Image(systemName: "chevron.down")
                .font(.caption.weight(.semibold))
                .accessibilityHidden(true)
        }
        .foregroundStyle(palette.muted.color)
        .frame(minHeight: PixelMetrics.minTapTarget)
        .contentShape(Rectangle())
    }
}

/// The state filter (S-18.4): All · Waiting on you · Ready for review · Working · Done · Archived, as chips that wrap.
struct StatusChips: View {
    var selected: QuestStatusFilter
    var select: (QuestStatusFilter) -> Void

    @Environment(\.colorScheme) private var scheme

    var body: some View {
        let palette = ChatPalette(scheme)
        ChipFlow(spacing: 8) {
            ForEach(QuestStatusFilter.allCases, id: \.self) { status in
                let on = status == selected
                Button { select(status) } label: {
                    Text(status.title)
                        .font(.subheadline.weight(on ? .semibold : .regular))
                        .foregroundStyle((on ? palette.card : palette.ink).color)
                        .padding(.horizontal, 12)
                        .frame(minHeight: 34)
                        .background {
                            if on {
                                Capsule().fill(palette.ink.color)
                            } else {
                                Capsule().strokeBorder(palette.line.color, lineWidth: 1)
                                    .background(palette.card.color, in: Capsule())
                            }
                        }
                        .padding(.vertical, 5)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityAddTraits(on ? .isSelected : [])
                .accessibilityHint("Shows \(status == .all ? "every quest" : status.title.lowercased() + " quests")")
            }
        }
    }
}

/// Lays its children out left to right, wrapping to the next line when the width runs out.
struct ChipFlow: Layout {
    var spacing: CGFloat

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let rows = arrange(width: proposal.width ?? .infinity, subviews: subviews)
        let height = rows.reduce(0) { $0 + $1.height } + CGFloat(max(0, rows.count - 1)) * spacing
        let width = rows.map(\.width).max() ?? 0
        return CGSize(width: proposal.width ?? width, height: height)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        var y = bounds.minY
        for row in arrange(width: bounds.width, subviews: subviews) {
            var x = bounds.minX
            for index in row.items {
                let size = subviews[index].sizeThatFits(.unspecified)
                subviews[index].place(at: CGPoint(x: x, y: y), proposal: ProposedViewSize(size))
                x += size.width + spacing
            }
            y += row.height + spacing
        }
    }

    private struct Row {
        var items: [Int] = []
        var width: CGFloat = 0
        var height: CGFloat = 0
    }

    private func arrange(width: CGFloat, subviews: Subviews) -> [Row] {
        var rows: [Row] = []
        var row = Row()
        for index in subviews.indices {
            let size = subviews[index].sizeThatFits(.unspecified)
            if !row.items.isEmpty, row.width + spacing + size.width > width {
                rows.append(row)
                row = Row()
            }
            row.width += (row.items.isEmpty ? 0 : spacing) + size.width
            row.height = max(row.height, size.height)
            row.items.append(index)
        }
        if !row.items.isEmpty { rows.append(row) }
        return rows
    }
}

/// A group's title ("Waiting on you"), small and muted like the wireframe's `.sec`. "Done · this week (5)" is a
/// button that opens and closes the group.
struct SectionTitle: View {
    var section: QuestSection
    var open: Bool
    var toggle: (() -> Void)?

    @Environment(\.colorScheme) private var scheme

    var body: some View {
        let theme = PixelTheme(scheme)
        let label = HStack(spacing: PixelMetrics.steps(3)) {
            PixelText(section.title, role: .smallLabel)
                .foregroundStyle(theme.muted.color)
                .fixedSize(horizontal: false, vertical: true)
            if toggle != nil {
                PixelGlyph(open ? .more : .pointer, color: theme.muted)
                    .accessibilityHidden(true)
            }
        }
        .frame(maxWidth: .infinity, minHeight: toggle == nil ? nil : PixelMetrics.minTapTarget, alignment: .leading)
        .contentShape(Rectangle())
        if let toggle {
            Button(action: toggle) { label }
                .buttonStyle(.plain)
                .accessibilityLabel("\(section.group.spokenTitle), \(section.rows.count) quests")
                .accessibilityValue(open ? "Shown" : "Hidden")
                .accessibilityHint(open ? "Hides them" : "Shows them")
                .accessibilityAddTraits(.isHeader)
        } else {
            label
                .accessibilityElement(children: .ignore)
                .accessibilityLabel(section.group.spokenTitle)
                .accessibilityAddTraits(.isHeader)
        }
    }
}

/// One quest: PixelKit's `QuestCard`, read by VoiceOver as one sentence in full words (the card's short detail
/// line — "2h old · active 5m ago" — reads badly aloud).
struct QuestRowView: View {
    var row: QuestRow
    var busy: Bool = false

    var body: some View {
        QuestCard(title: row.title, state: row.state, stateText: row.stateText, project: row.project, tone: row.tone,
                  detail: row.detail)
            .opacity(busy ? 0.6 : 1)
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(row.spoken)
            .accessibilityValue(busy ? "Sending" : "")
    }
}

/// A problem from the last swipe, in Marcel's words, with a way to dismiss it.
struct ProblemLine: View {
    var text: String
    var dismiss: () -> Void

    @Environment(\.colorScheme) private var scheme

    var body: some View {
        let theme = PixelTheme(scheme)
        Button(action: dismiss) {
            Text(text)
                .font(.subheadline)
                .foregroundStyle(theme.text.color)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, minHeight: PixelMetrics.minTapTarget, alignment: .leading)
                .padding(.horizontal, 12)
                .background(ChatPalette(scheme).card.color, in: RoundedRectangle(cornerRadius: 12))
                .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(ChatPalette(scheme).line.color, lineWidth: 1))
        }
        .buttonStyle(.plain)
        .accessibilityLabel(text)
        .accessibilityHint("Dismisses this message")
    }
}

/// What an empty log says.
struct EmptyLog: View {
    var text: String

    @Environment(\.colorScheme) private var scheme

    var body: some View {
        Text(text)
            .font(.body)
            .foregroundStyle(ChatPalette(scheme).muted.color)
            .multilineTextAlignment(.center)
            .fixedSize(horizontal: false, vertical: true)
            .frame(maxWidth: .infinity)
            .padding(.vertical, PixelMetrics.steps(16))
    }
}

/// The quest log laid out in a plain stack: what the list shows, without the list. `ImageRenderer` draws nothing
/// inside a `List` or `ScrollView`, so the snapshot tests render this; the screen uses the same pieces.
struct QuestLogStill: View {
    var board: QuestBoard
    var showsFinished: Bool
    var problem: String?

    @Environment(\.colorScheme) private var scheme

    var body: some View {
        VStack(spacing: 0) {
            QuestLogHeader { FilterLabel(title: board.projectTitle) }
            VStack(alignment: .leading, spacing: QuestLogMetrics.rowSpacing) {
                StatusChips(selected: board.filter.status, select: { _ in })
                if let problem { ProblemLine(text: problem, dismiss: {}) }
                if board.isEmpty { EmptyLog(text: board.emptyText) }
                ForEach(board.sections) { section in
                    let collapsible = board.startsCollapsed(section.group)
                    let open = !collapsible || showsFinished
                    SectionTitle(section: section, open: open, toggle: collapsible ? {} : nil)
                        .padding(.top, QuestLogMetrics.sectionGap)
                    if open {
                        ForEach(section.rows) { QuestRowView(row: $0) }
                    }
                }
            }
            .padding(QuestLogMetrics.inset)
        }
        .background(ChatPalette(scheme).well.color)
    }
}

enum QuestLogMetrics {
    static let inset = EdgeInsets(top: PixelMetrics.steps(3), leading: 12, bottom: 16, trailing: 12)
    static let rowSpacing = PixelMetrics.gap
    static let sectionGap = PixelMetrics.steps(2)
}
