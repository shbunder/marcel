import MarcelKit
import PixelKit
import SwiftUI

extension EnvironmentValues {
    /// Snapshot tests: draw a web view's words, QuickLook's card and the question box's text, which `ImageRenderer`
    /// cannot draw.
    @Entry var itemsStill = false
}

enum ItemsMetrics {
    static let inset = EdgeInsets(top: PixelMetrics.steps(5), leading: 12, bottom: 16, trailing: 12)
    static let gap: CGFloat = PixelMetrics.steps(5)
}

/// The viewer's top strip (wireframe 4B): ◀ back, the item's name, and its version line, on the artifact gold.
struct ViewerHeader: View {
    var title: String
    var line: VersionLine?
    var back: () -> Void

    @Environment(\.colorScheme) private var scheme

    var body: some View {
        let theme = PixelTheme(scheme)
        HStack(alignment: .center, spacing: PixelMetrics.steps(3)) {
            Button(action: back) {
                PixelGlyph(.pointer, color: theme.onHeader, pixel: 3)
                    .scaleEffect(x: -1)
                    .frame(width: PixelMetrics.minTapTarget, height: PixelMetrics.minTapTarget)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Back")
            VStack(alignment: .leading, spacing: PixelMetrics.steps(1)) {
                PixelText(title, role: .title)
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityAddTraits(.isHeader)
                if let line {
                    PixelText(line.text, role: .detail)
                        .fixedSize(horizontal: false, vertical: true)
                        .accessibilityLabel(line.spoken)
                }
            }
            .foregroundStyle(theme.onHeader.color)
            Spacer(minLength: 0)
        }
        .padding(.leading, PixelMetrics.steps(1))
        .padding(.trailing, PixelMetrics.padding)
        .padding(.vertical, PixelMetrics.steps(3))
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(theme.header.color)
        .overlay(alignment: .bottom) {
            Rectangle().fill(theme.line.color).frame(height: PixelMetrics.thickBorder)
        }
    }
}

/// A Markdown document, natively: headings in the label font, body text in the body font, lists, quotes, code,
/// tables, and each block's web links as buttons under it. Public so Marcel's notebook (MarcelNotebook) previews a
/// note with the same rules: a link opens only when its button is tapped, and only an http(s) one; HTML stays words.
public struct MarkdownView: View {
    var parts: [ItemMarkdown.Part]

    @Environment(\.colorScheme) private var scheme
    @Environment(\.openURL) private var openURL

    public init(parts: [ItemMarkdown.Part]) {
        self.parts = parts
    }

    public var body: some View {
        let theme = PixelTheme(scheme)
        VStack(alignment: .leading, spacing: PixelMetrics.steps(5)) {
            if parts.isEmpty {
                PixelText("This document is empty.", role: .body).foregroundStyle(theme.muted.color)
            }
            ForEach(Array(parts.enumerated()), id: \.offset) { _, part in
                block(part.block, theme: theme)
                ForEach(Array(part.links.enumerated()), id: \.offset) { _, link in
                    Button { openURL(link.url) } label: {
                        PixelText("Open \(link.title) →", role: .smallLabel)
                            .foregroundStyle(ProjectChip.Tone.blue.color(in: theme).color)
                            .fixedSize(horizontal: false, vertical: true)
                            .frame(minHeight: PixelMetrics.minTapTarget, alignment: .leading)
                            .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel("Open \(link.title) in Safari")
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    @ViewBuilder private func block(_ block: ItemMarkdown.Block, theme: PixelTheme) -> some View {
        switch block {
        case .heading(let level, let text):
            PixelText(text, role: level <= 1 ? .title : level == 2 ? .label : .smallLabel)
                .foregroundStyle(theme.text.color)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.top, level <= 2 ? PixelMetrics.steps(2) : 0)
                .accessibilityAddTraits(.isHeader)
        case .paragraph(let text):
            PixelText(text, role: .body)
                .foregroundStyle(theme.text.color)
                .fixedSize(horizontal: false, vertical: true)
        case .listItem(let marker, let text, let depth):
            HStack(alignment: .firstTextBaseline, spacing: PixelMetrics.steps(3)) {
                PixelText(marker.trimmingCharacters(in: .whitespaces), role: .body)
                    .foregroundStyle(theme.muted.color)
                    .accessibilityHidden(marker.hasPrefix("•"))
                PixelText(text, role: .body)
                    .foregroundStyle(theme.text.color)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .padding(.leading, CGFloat(depth) * PixelMetrics.steps(8))
            .accessibilityElement(children: .combine)
        case .quote(let text):
            PixelText(text, role: .body)
                .foregroundStyle(theme.muted.color)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.leading, PixelMetrics.steps(5))
                .overlay(alignment: .leading) {
                    Rectangle().fill(theme.muted.color).frame(width: PixelMetrics.thinBorder)
                }
        case .code(let text):
            Text(text)
                .font(.system(.footnote, design: .monospaced))
                .foregroundStyle(theme.text.color)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(PixelMetrics.steps(4))
                .background(theme.bar.color)
        case .table(let header, let rows):
            TableView(title: nil, columns: header, rows: rows, spokenRows: rows)
        case .rule:
            Rectangle().fill(theme.line.color).frame(height: PixelMetrics.thinBorder)
                .accessibilityHidden(true)
        }
    }
}

/// A pull request: its number and repository, a state badge, checks and review, and "Open in GitHub".
struct PRCardView: View {
    var card: PRCard

    @Environment(\.colorScheme) private var scheme
    @Environment(\.openURL) private var openURL

    var badge: StateBadge.Kind {
        switch card.state {
        case .open?: .ready
        case .draft?: .queued
        case .merged?: .done
        case .closed?: .stopped
        case nil: .unknown
        }
    }

    var body: some View {
        let theme = PixelTheme(scheme)
        VStack(alignment: .leading, spacing: ItemsMetrics.gap) {
            VStack(alignment: .leading, spacing: PixelMetrics.steps(4)) {
                HStack(alignment: .center, spacing: PixelMetrics.steps(4)) {
                    StateBadge(badge, text: card.stateText)
                    if let number = card.number {
                        PixelText("#\(number)", role: .label).foregroundStyle(theme.text.color)
                    }
                    Spacer(minLength: 0)
                }
                PixelText(card.title, role: .body)
                    .foregroundStyle(theme.text.color)
                    .fixedSize(horizontal: false, vertical: true)
                if let repository = card.repository {
                    PixelText(repository, role: .detail).foregroundStyle(theme.muted.color)
                }
                ForEach([card.checks, card.review].compactMap { $0 }, id: \.self) { line in
                    PixelText(line, role: .detail)
                        .foregroundStyle(theme.text.color)
                }
                if let note = card.note {
                    PixelText(note, role: .detail)
                        .foregroundStyle(theme.muted.color)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if card.selfChange {
                    PixelText("This changes Marcel itself. It is merged only when you approve it.",
                              role: .detail)
                        .foregroundStyle(theme.accent.color)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(PixelMetrics.padding)
            .pixelBox(fill: theme.card, line: theme.line, shadow: theme.shadow)
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(card.spoken)
            if let github = card.github {
                ActionChip(title: "Open in GitHub", tone: .blue) { openURL(github) }
                    .accessibilityHint("Opens the pull request in Safari")
            } else {
                PixelText("This pull request has no GitHub address the app can open.", role: .detail)
                    .foregroundStyle(theme.muted.color)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}

/// A link or a branch: where it goes, and a button only for a web address.
struct LinkCardView: View {
    var card: LinkCard

    @Environment(\.colorScheme) private var scheme
    @Environment(\.openURL) private var openURL

    var body: some View {
        let theme = PixelTheme(scheme)
        VStack(alignment: .leading, spacing: ItemsMetrics.gap) {
            VStack(alignment: .leading, spacing: PixelMetrics.steps(3)) {
                PixelText(card.isBranch ? "A branch, with no pull request yet" : "A link", role: .detail)
                    .foregroundStyle(theme.muted.color)
                PixelText(card.title, role: .body)
                    .foregroundStyle(theme.text.color)
                    .fixedSize(horizontal: false, vertical: true)
                if let host = card.host {
                    PixelText(host, role: .detail).foregroundStyle(theme.muted.color)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(PixelMetrics.padding)
            .pixelBox(fill: theme.card, line: theme.line, shadow: theme.shadow)
            .accessibilityElement(children: .combine)
            if let url = card.url {
                ActionChip(title: card.buttonTitle, tone: .blue) { openURL(url) }
            } else {
                PixelText("This link can't be opened from the app.", role: .detail)
                    .foregroundStyle(theme.muted.color)
            }
        }
    }
}

/// An outlined chip button ("Ask about this", "Open in GitHub"), at least 44 pt tall.
struct ActionChip: View {
    var title: String
    var tone: ProjectChip.Tone
    var action: () -> Void

    @Environment(\.colorScheme) private var scheme

    var body: some View {
        let ink = tone.color(in: PixelTheme(scheme)).color
        Button(action: action) {
            PixelText(title, role: .smallLabel)
                .foregroundStyle(ink)
                .lineLimit(1)
                .padding(.horizontal, PixelMetrics.steps(5))
                .frame(minHeight: PixelMetrics.minTapTarget)
                .overlay(Rectangle().strokeBorder(ink, lineWidth: PixelMetrics.thinBorder))
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .fixedSize()
        .accessibilityLabel(title)
    }
}

/// Everything but web pages and files: drawn natively in the scroll view.
struct NativeBody: View {
    var body_: ItemBody
    var title: String

    @Environment(\.colorScheme) private var scheme
    @Environment(\.itemsStill) private var still

    var body: some View {
        let theme = PixelTheme(scheme)
        switch body_ {
        case .loading:
            PixelText("Opening…", role: .body).foregroundStyle(theme.muted.color)
        case .failed(let problem), .brokenDashboard(let problem):
            PixelText(problem, role: .body)
                .foregroundStyle(theme.text.color)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(PixelMetrics.padding)
                .pixelBox(fill: theme.dialog, line: theme.line, shadow: nil, border: PixelMetrics.thinBorder)
        case .markdown(let parts): MarkdownView(parts: parts)
        case .dashboard(let dashboard): DashboardView(dashboard: dashboard, itemTitle: title)
        case .pr(let card): PRCardView(card: card)
        case .link(let card): LinkCardView(card: card)
        case .html(let html, let trusted):
            // Only snapshots get here; the screen shows a web view.
            VStack(alignment: .leading, spacing: PixelMetrics.steps(3)) {
                ScriptsLine(trusted: trusted)
                HTMLStill(html: html)
            }
        case .file(let file):
            VStack(alignment: .leading, spacing: PixelMetrics.steps(3)) {
                FileCard(file: file)
                if still {
                    PixelText("QuickLook shows the file here.", role: .detail).foregroundStyle(theme.muted.color)
                }
            }
        }
    }
}

/// Under a web page: whether its scripts run, in plain words.
struct ScriptsLine: View {
    var trusted: Bool

    @Environment(\.colorScheme) private var scheme

    var body: some View {
        PixelText(trusted ? "Made by the hub. Its scripts run."
                  : "Scripts are off on this page, and links open in Safari.", role: .detail)
            .foregroundStyle(PixelTheme(scheme).muted.color)
            .fixedSize(horizontal: false, vertical: true)
            .frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// The bottom of the viewer (wireframe 4B): "Ask about this", the history, and the question box once open.
struct ViewerActions: View {
    @Binding var draft: String
    var asking: Bool
    var sending: Bool
    var sent: String?
    var problem: String?
    var historyCount: Int?
    var showingOld: Bool
    var toggleAsk: () -> Void
    /// "Pin to project"; nil hides it (a project's board stays with its project).
    var pin: (() -> Void)?
    var send: () -> Void
    var history: () -> Void
    var latest: () -> Void

    @Environment(\.colorScheme) private var scheme
    @Environment(\.itemsStill) private var still
    @Environment(\.agentName) private var agentName

    var body: some View {
        let theme = PixelTheme(scheme)
        VStack(alignment: .leading, spacing: PixelMetrics.steps(3)) {
            if let problem {
                PixelText(problem, role: .detail)
                    .foregroundStyle(theme.accent.color)
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityLabel("Problem: \(problem)")
            }
            if let sent {
                PixelText(sent, role: .detail)
                    .foregroundStyle(theme.muted.color)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if asking { askBox(theme) }
            // As many chips per row as fit, the rest on the next row (three no longer fit one row on a phone).
            ChipFlow(spacing: PixelMetrics.steps(4), lineSpacing: PixelMetrics.steps(3)) { chips }
        }
        .padding(.horizontal, ItemsMetrics.inset.leading)
        .padding(.top, PixelMetrics.steps(4))
        .padding(.bottom, PixelMetrics.steps(5))
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(theme.background.color)
        .overlay(alignment: .top) {
            Rectangle().fill(theme.line.color).frame(height: PixelMetrics.thinBorder)
        }
    }

    @ViewBuilder private var chips: some View {
        if !asking {
            ActionChip(title: "Ask about this", tone: .teal, action: toggleAsk)
                .accessibilityHint("Asks \(agentName) a question about this item in Chat")
        }
        if let pin {
            ActionChip(title: "Pin to project", tone: .gold, action: pin)
                .accessibilityHint("Moves this item to another project")
        }
        if showingOld {
            ActionChip(title: "Back to the latest", tone: .rose, action: latest)
        } else if let historyCount, historyCount > 1 {
            ActionChip(title: "History · \(historyCount)", tone: .rose, action: history)
                .accessibilityLabel("History, \(historyCount) versions")
        }
    }

    private func askBox(_ theme: PixelTheme) -> some View {
        let canSend = !draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && !sending
        return HStack(alignment: .center, spacing: PixelMetrics.steps(3)) {
            if still {
                PixelText(draft.isEmpty ? "Ask \(agentName) about this…" : draft, role: .body)
                    .foregroundStyle((draft.isEmpty ? theme.muted : theme.text).color)
                    .frame(maxWidth: .infinity, alignment: .leading)
            } else {
                TextField(text: $draft, axis: .vertical) {
                    Text("Ask \(agentName) about this…").foregroundStyle(theme.muted.color)
                }
                .lineLimit(1...4)
                .pixelText(.body)
                .foregroundStyle(theme.text.color)
                .tint(theme.accent.color)
                .accessibilityLabel("Question for \(agentName) about this item")
            }
            Button(action: send) {
                PixelGlyph(.pointer, color: canSend ? theme.accent : theme.muted, pixel: 3)
                    .frame(width: PixelMetrics.minTapTarget, height: PixelMetrics.minTapTarget)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .disabled(!canSend)
            .accessibilityLabel("Send")
            Button(action: toggleAsk) {
                PixelText("X", role: .label)
                    .foregroundStyle(theme.muted.color)
                    .frame(width: PixelMetrics.minTapTarget, height: PixelMetrics.minTapTarget)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Close the question")
        }
        .padding(.leading, PixelMetrics.padding)
        .padding(.vertical, PixelMetrics.steps(1))
        .pixelBox(fill: theme.dialog, line: theme.line, shadow: nil)
    }
}

/// The history (B-34): every version, newest first. Tapping an older one opens it read-only.
struct HistoryList: View {
    var rows: [VersionRow]
    var state: VersionsState?
    var shown: Int
    var open: (Int) -> Void

    @Environment(\.colorScheme) private var scheme

    var body: some View {
        let theme = PixelTheme(scheme)
        VStack(alignment: .leading, spacing: PixelMetrics.steps(4)) {
            PixelText("History", role: .label)
                .foregroundStyle(theme.text.color)
                .accessibilityAddTraits(.isHeader)
            switch state {
            case .failed(let problem)?:
                PixelText(problem, role: .detail).foregroundStyle(theme.muted.color)
            case .loading?, nil:
                PixelText("Loading the history…", role: .detail).foregroundStyle(theme.muted.color)
            case .loaded?:
                ForEach(rows) { row in
                    Button { open(row.version) } label: { VersionRowView(row: row, selected: row.version == shown) }
                        .buttonStyle(.plain)
                        .accessibilityHint(row.isCurrent ? "Opens the current version" : "Opens it read-only")
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

struct VersionRowView: View {
    var row: VersionRow
    var selected: Bool

    @Environment(\.colorScheme) private var scheme

    var body: some View {
        let theme = PixelTheme(scheme)
        HStack(alignment: .firstTextBaseline, spacing: PixelMetrics.steps(5)) {
            PixelText("v\(row.version)", role: .label)
                .foregroundStyle(theme.text.color)
                .frame(minWidth: 36, alignment: .leading)
            VStack(alignment: .leading, spacing: PixelMetrics.steps(1)) {
                PixelText(row.summary, role: .body)
                    .foregroundStyle(theme.text.color)
                    .fixedSize(horizontal: false, vertical: true)
                PixelText(row.isCurrent ? "Current · \(row.detail)" : row.detail, role: .detail)
                    .foregroundStyle(theme.muted.color)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 0)
        }
        .padding(PixelMetrics.padding)
        .frame(minHeight: PixelMetrics.minTapTarget)
        .pixelBox(fill: selected ? theme.dialog : theme.card, line: theme.line, shadow: nil,
                  border: selected ? PixelMetrics.thickBorder : PixelMetrics.thinBorder)
        .contentShape(Rectangle())
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(row.spoken + (selected ? ". Showing" : ""))
    }
}

/// The viewer laid out in a plain stack, for the snapshot tests (`ImageRenderer` draws nothing in a `ScrollView`, a
/// web view or QuickLook): header, body, history when open, and the bottom actions.
struct ViewerStill: View {
    var title: String
    var line: VersionLine?
    var body_: ItemBody
    var history: [VersionRow]?
    var shown: Int = 1
    var historyCount: Int?
    var asking = false
    var draft = ""
    var sent: String?
    var problem: String?
    /// "Pin to project" is offered for every kind but a board, as the screen does.
    var pinnable = true

    @Environment(\.colorScheme) private var scheme

    var body: some View {
        VStack(spacing: 0) {
            ViewerHeader(title: title, line: line, back: {})
            VStack(alignment: .leading, spacing: ItemsMetrics.gap) {
                NativeBody(body_: body_, title: title)
                if let history {
                    HistoryList(rows: history, state: .loaded([]), shown: shown, open: { _ in })
                }
            }
            .padding(ItemsMetrics.inset)
            ViewerActions(draft: .constant(draft), asking: asking, sending: false, sent: sent, problem: problem,
                          historyCount: historyCount, showingOld: line?.isOld == true, toggleAsk: {},
                          pin: pinnable ? {} : nil, send: {}, history: {}, latest: {})
        }
        .background(PixelTheme(scheme).background.color)
        .environment(\.itemsStill, true)
    }
}

/// Lays its views out left to right and starts a new row when the next one does not fit: the viewer's action chips.
struct ChipFlow: Layout {
    var spacing: CGFloat
    var lineSpacing: CGFloat

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let rows = rows(width: proposal.width ?? .infinity, subviews: subviews)
        let width = rows.map(\.width).max() ?? 0
        let height = rows.map(\.height).reduce(0, +) + lineSpacing * CGFloat(max(0, rows.count - 1))
        return CGSize(width: width, height: height)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        var y = bounds.minY
        for row in rows(width: bounds.width, subviews: subviews) {
            var x = bounds.minX
            for index in row.indices {
                let size = subviews[index].sizeThatFits(.unspecified)
                subviews[index].place(at: CGPoint(x: x, y: y + (row.height - size.height) / 2), proposal: .init(size))
                x += size.width + spacing
            }
            y += row.height + lineSpacing
        }
    }

    private struct Row {
        var indices: [Int] = []
        var width: CGFloat = 0
        var height: CGFloat = 0
    }

    private func rows(width: CGFloat, subviews: Subviews) -> [Row] {
        var rows: [Row] = []
        var row = Row()
        for index in subviews.indices {
            let size = subviews[index].sizeThatFits(.unspecified)
            let needed = row.indices.isEmpty ? size.width : row.width + spacing + size.width
            if !row.indices.isEmpty, needed > width {
                rows.append(row)
                row = Row()
            }
            row.width = row.indices.isEmpty ? size.width : row.width + spacing + size.width
            row.height = max(row.height, size.height)
            row.indices.append(index)
        }
        if !row.indices.isEmpty { rows.append(row) }
        return rows
    }
}
