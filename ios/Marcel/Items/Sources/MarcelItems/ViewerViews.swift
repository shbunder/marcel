import MarcelKit
import PixelKit
import SwiftUI

extension EnvironmentValues {
    /// Snapshot tests: draw a web view's words, QuickLook's card and the question box's text, which `ImageRenderer`
    /// cannot draw.
    @Entry var itemsStill = false
    /// Snapshot tests: a fixed "now" and time zone for times shown relative to now ("checked 3 h ago"). Nil on the
    /// screen, where they follow the clock.
    @Entry var itemsClock: ItemsClock?
}

/// A fixed "now" for the views that say how long ago something happened.
struct ItemsClock: Equatable {
    var now: Date
    var timeZone: TimeZone
}

enum ItemsMetrics {
    static let inset = ChatScreenMetrics.content
    static let gap: CGFloat = ChatScreenMetrics.gap
}

/// The viewer's top strip in the chat's language: "‹" back, the item's name, and its version line, on white.
struct ViewerHeader: View {
    var title: String
    var line: VersionLine?
    var back: () -> Void

    var body: some View {
        // The name (a heading) and the version line are two items for VoiceOver, the line in full words.
        ChatHeader(title, subtitle: line?.text, subtitleSpoken: line?.spoken ?? "", back: back)
    }
}

/// A pull request: its number and repository, a state badge, checks and review, and "Open in GitHub".
struct PRCardView: View {
    var card: PRCard

    @Environment(\.colorScheme) private var scheme
    @Environment(\.itemsClock) private var clock
    @Environment(\.openURL) private var openURL

    /// The PR's state as the chat colours it: rose ready for review, teal merged, red closed, gold for a draft or an
    /// unknown state (it still needs looking at).
    var kind: ChatCardKind {
        switch card.state {
        case .open?: .done
        case .draft?: .needsYou
        case .merged?: .started
        case .closed?: .failed
        case nil: .needsYou
        }
    }

    var body: some View {
        if let clock {
            content(now: clock.now, timeZone: clock.timeZone)
        } else {
            // "Checked 2 min ago" moves on once a minute.
            TimelineView(.periodic(from: .now, by: 60)) { timeline in
                content(now: timeline.date, timeZone: .current)
            }
        }
    }

    private func content(now: Date, timeZone: TimeZone) -> some View {
        let theme = ChatPalette(scheme)
        let checked = card.checked(now: now, timeZone: timeZone)
        return VStack(alignment: .leading, spacing: ItemsMetrics.gap) {
            VStack(alignment: .leading, spacing: PixelMetrics.steps(4)) {
                HStack(alignment: .center, spacing: PixelMetrics.steps(4)) {
                    ChatBadge(card.stateText, kind: kind)
                    if let number = card.number {
                        Text("#\(number)").font(.body.weight(.semibold)).foregroundStyle(theme.ink.color)
                    }
                    Spacer(minLength: 0)
                }
                Text(card.title).font(.body)
                    .foregroundStyle(theme.ink.color)
                    .fixedSize(horizontal: false, vertical: true)
                if let repository = card.repository {
                    Text(repository).font(.footnote).foregroundStyle(theme.muted.color)
                }
                ForEach([card.checks, card.review].compactMap { $0 }, id: \.self) { line in
                    Text(line).font(.footnote)
                        .foregroundStyle(theme.ink.color)
                }
                if let note = card.note {
                    Text(note).font(.footnote)
                        .foregroundStyle(theme.muted.color)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if let checked {
                    Text(checked.short).font(.footnote)
                        .foregroundStyle(theme.muted.color)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if card.selfChange {
                    Text("This changes Marcel itself. It is merged only when you approve it.")
                        .font(.footnote)
                        .foregroundStyle(theme.link.color)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(PixelMetrics.padding)
            .chatSurface()
            .accessibilityElement(children: .ignore)
            .accessibilityLabel([card.spoken, checked?.spoken].compactMap { $0 }.joined(separator: ". "))
            if let github = card.github {
                ActionChip(title: "Open in GitHub", tone: .blue) { openURL(github) }
                    .accessibilityHint("Opens the pull request in Safari")
            } else {
                Text("This pull request has no GitHub address the app can open.").font(.footnote)
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
        let theme = ChatPalette(scheme)
        VStack(alignment: .leading, spacing: ItemsMetrics.gap) {
            VStack(alignment: .leading, spacing: PixelMetrics.steps(3)) {
                Text(card.isBranch ? "A branch, with no pull request yet" : "A link").font(.footnote)
                    .foregroundStyle(theme.muted.color)
                Text(card.title).font(.body)
                    .foregroundStyle(theme.ink.color)
                    .fixedSize(horizontal: false, vertical: true)
                if let host = card.host {
                    Text(host).font(.footnote).foregroundStyle(theme.muted.color)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(PixelMetrics.padding)
            .chatSurface()
            .accessibilityElement(children: .combine)
            if let url = card.url {
                ActionChip(title: card.buttonTitle, tone: .blue) { openURL(url) }
            } else {
                Text("This link can't be opened from the app.").font(.footnote)
                    .foregroundStyle(theme.muted.color)
            }
        }
    }
}

/// An outlined rounded button ("Ask about this", "Open in GitHub"), at least 44 pt tall, in the colour of its tone.
struct ActionChip: View {
    var title: String
    var tone: ProjectChip.Tone
    var action: () -> Void

    /// The chat colour for each tone the viewer uses.
    var kind: ChatCardKind {
        switch tone {
        case .teal, .blue, .green: .started
        case .gold: .needsYou
        case .rose: .done
        }
    }

    var body: some View {
        ChatPillButton(title, kind: kind, action: action)
    }
}

/// Everything but web pages and files: drawn natively in the scroll view.
struct NativeBody: View {
    var body_: ItemBody
    var title: String

    @Environment(\.colorScheme) private var scheme
    @Environment(\.itemsStill) private var still

    var body: some View {
        let theme = ChatPalette(scheme)
        switch body_ {
        case .loading:
            Text("Opening…").font(.body).foregroundStyle(theme.muted.color)
        case .failed(let problem), .brokenDashboard(let problem):
            Text(problem).font(.body)
                .foregroundStyle(theme.ink.color)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(PixelMetrics.padding)
                .chatSurface()
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
                    Text("QuickLook shows the file here.").font(.footnote).foregroundStyle(theme.muted.color)
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
        Text(trusted ? "Made by the hub. Its scripts run."
                  : "Scripts are off on this page, and links open in Safari.").font(.footnote)
            .foregroundStyle(ChatPalette(scheme).muted.color)
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
        let theme = ChatPalette(scheme)
        VStack(alignment: .leading, spacing: PixelMetrics.steps(3)) {
            if let problem {
                Text(problem).font(.footnote)
                    .foregroundStyle(theme.link.color)
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityLabel("Problem: \(problem)")
            }
            if let sent {
                Text(sent).font(.footnote)
                    .foregroundStyle(theme.muted.color)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if asking { askBox(theme) }
            // As many chips per row as fit, the rest on the next row (three no longer fit one row on a phone).
            ChipFlow(spacing: PixelMetrics.steps(4), lineSpacing: PixelMetrics.steps(3)) { chips }
        }
        .padding(.horizontal, ItemsMetrics.inset.leading)
        .padding(.top, 10)
        .padding(.bottom, 12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(theme.card.color)
        .overlay(alignment: .top) {
            Rectangle().fill(theme.line.color).frame(height: 1)
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

    private func askBox(_ theme: ChatPalette) -> some View {
        let canSend = !draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && !sending
        return HStack(alignment: .center, spacing: PixelMetrics.steps(3)) {
            if still {
                Text(draft.isEmpty ? "Ask \(agentName) about this…" : draft).font(.body)
                    .foregroundStyle((draft.isEmpty ? theme.muted : theme.ink).color)
                    .frame(maxWidth: .infinity, alignment: .leading)
            } else {
                TextField(text: $draft, axis: .vertical) {
                    Text("Ask \(agentName) about this…").foregroundStyle(theme.muted.color)
                }
                .lineLimit(1...4)
                .font(.body)
                .foregroundStyle(theme.ink.color)
                .tint(theme.link.color)
                .accessibilityLabel("Question for \(agentName) about this item")
            }
            Button(action: send) {
                PixelGlyph(.pointer, color: canSend ? theme.link : theme.muted, pixel: 3)
                    .frame(width: PixelMetrics.minTapTarget, height: PixelMetrics.minTapTarget)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .disabled(!canSend)
            .accessibilityLabel("Send")
            Button(action: toggleAsk) {
                Text("X").font(.body.weight(.semibold))
                    .foregroundStyle(theme.muted.color)
                    .frame(width: PixelMetrics.minTapTarget, height: PixelMetrics.minTapTarget)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Close the question")
        }
        .padding(.leading, 14)
        .padding(.vertical, 2)
        .chatSurface(.well, radius: 22)
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
        let theme = ChatPalette(scheme)
        VStack(alignment: .leading, spacing: PixelMetrics.steps(4)) {
            Text("History").font(.body.weight(.semibold))
                .foregroundStyle(theme.ink.color)
                .accessibilityAddTraits(.isHeader)
            switch state {
            case .failed(let problem)?:
                Text(problem).font(.footnote).foregroundStyle(theme.muted.color)
            case .loading?, nil:
                Text("Loading the history…").font(.footnote).foregroundStyle(theme.muted.color)
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
        let theme = ChatPalette(scheme)
        HStack(alignment: .firstTextBaseline, spacing: PixelMetrics.steps(5)) {
            Text("v\(row.version)").font(.body.weight(.semibold))
                .foregroundStyle(theme.ink.color)
                .frame(minWidth: 36, alignment: .leading)
            VStack(alignment: .leading, spacing: PixelMetrics.steps(1)) {
                Text(row.summary).font(.body)
                    .foregroundStyle(theme.ink.color)
                    .fixedSize(horizontal: false, vertical: true)
                Text(row.isCurrent ? "Current · \(row.detail)" : row.detail).font(.footnote)
                    .foregroundStyle(theme.muted.color)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 0)
        }
        .padding(ChatScreenMetrics.padding)
        .frame(minHeight: PixelMetrics.minTapTarget)
        .chatSurface(selected ? .framed(.done) : .plain)
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
    /// "Now" for the times the body shows relative to now (a PR's "checked 3 h ago").
    var clock: ItemsClock?

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
        .background(ChatPalette(scheme).well.color)
        .environment(\.itemsStill, true)
        .environment(\.itemsClock, clock)
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
