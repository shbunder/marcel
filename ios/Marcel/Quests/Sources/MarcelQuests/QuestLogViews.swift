import PixelKit
import SwiftUI

/// The top strip (wireframe 2C): "Quest log" and the filter, on the bar colour with a heavy line under it.
struct QuestLogHeader<Filter: View>: View {
    var filter: Filter

    init(@ViewBuilder filter: () -> Filter) {
        self.filter = filter()
    }

    @Environment(\.colorScheme) private var scheme
    @Environment(\.dynamicTypeSize) private var dynamicType

    var body: some View {
        let theme = PixelTheme(scheme)
        // At accessibility text sizes the filter goes under the title instead of squeezing it.
        let layout = dynamicType.isAccessibilitySize
            ? AnyLayout(VStackLayout(alignment: .leading, spacing: PixelMetrics.steps(3)))
            : AnyLayout(HStackLayout(alignment: .center, spacing: PixelMetrics.gap))
        layout {
            PixelText("Quest log", role: .title)
                .foregroundStyle(theme.text.color)
                .accessibilityAddTraits(.isHeader)
            if !dynamicType.isAccessibilitySize { Spacer(minLength: 0) }
            filter
        }
        .padding(.horizontal, PixelMetrics.padding + PixelMetrics.steps(1))
        .padding(.vertical, PixelMetrics.steps(5))
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(theme.bar.color)
        .overlay(alignment: .bottom) {
            Rectangle().fill(theme.line.color).frame(height: PixelMetrics.thickBorder)
        }
    }
}

/// What the filter button shows: "All projects ▼", "Home energy ▼", "Archived · All projects ▼".
struct FilterLabel: View {
    var title: String

    @Environment(\.colorScheme) private var scheme

    var body: some View {
        let theme = PixelTheme(scheme)
        HStack(spacing: PixelMetrics.steps(2)) {
            PixelText(title, role: .smallLabel)
                .foregroundStyle(theme.muted.color)
                .lineLimit(1)
            PixelGlyph(.more, color: theme.muted)
                .accessibilityHidden(true)
        }
        .frame(minHeight: PixelMetrics.minTapTarget)
        .contentShape(Rectangle())
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
                .accessibilityLabel("\(section.group.title), \(section.rows.count) quests")
                .accessibilityValue(open ? "Shown" : "Hidden")
                .accessibilityHint(open ? "Hides them" : "Shows them")
                .accessibilityAddTraits(.isHeader)
        } else {
            label
                .accessibilityElement(children: .ignore)
                .accessibilityLabel(section.title)
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
            PixelText(text, role: .detail)
                .foregroundStyle(theme.text.color)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, minHeight: PixelMetrics.minTapTarget, alignment: .leading)
                .padding(.horizontal, PixelMetrics.padding)
                .pixelBox(fill: theme.dialog, line: theme.line, shadow: nil, border: PixelMetrics.thinBorder)
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
        PixelText(text, role: .body)
            .foregroundStyle(PixelTheme(scheme).muted.color)
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
            QuestLogHeader { FilterLabel(title: board.filterTitle) }
            VStack(alignment: .leading, spacing: QuestLogMetrics.rowSpacing) {
                if let problem { ProblemLine(text: problem, dismiss: {}) }
                if board.isEmpty { EmptyLog(text: board.emptyText) }
                ForEach(board.sections) { section in
                    let open = !section.group.startsCollapsed || showsFinished
                    SectionTitle(section: section, open: open, toggle: section.group.startsCollapsed ? {} : nil)
                        .padding(.top, QuestLogMetrics.sectionGap)
                    if open {
                        ForEach(section.rows) { QuestRowView(row: $0) }
                    }
                }
            }
            .padding(QuestLogMetrics.inset)
        }
        .background(PixelTheme(scheme).background.color)
    }
}

enum QuestLogMetrics {
    static let inset = EdgeInsets(top: PixelMetrics.steps(3), leading: 12, bottom: 16, trailing: 12)
    static let rowSpacing = PixelMetrics.gap
    static let sectionGap = PixelMetrics.steps(2)
}
