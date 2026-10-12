import MarcelKit
import PixelKit
import SwiftUI

extension EnvironmentValues {
    /// Snapshot tests: draw the composer's text as `PixelText`, since `ImageRenderer` cannot draw a text field.
    @Entry var questStill = false
}

enum QuestMetrics {
    static let inset = EdgeInsets(top: PixelMetrics.steps(5), leading: 12, bottom: 16, trailing: 12)
    static let rowGap: CGFloat = PixelMetrics.steps(4)
    /// Lines of a tool's answer shown when its row is open.
    static let resultLines = 30
    /// Lines of Claude's text or a note shown before "…and N more lines".
    static let textLines = 80
}

/// The quest's top strip (wireframe 2A): ◀ back, the quest's name, and "project · NUC · sonnet" with its state.
struct QuestHeader: View {
    var title: String
    var project: String?
    var tone: ProjectChip.Tone
    var place: String?
    var state: StateBadge.Kind?
    var back: () -> Void

    @Environment(\.colorScheme) private var scheme

    var body: some View {
        let theme = PixelTheme(scheme)
        HStack(alignment: .center, spacing: PixelMetrics.steps(3)) {
            Button(action: back) {
                PixelGlyph(.pointer, color: theme.text, pixel: 3)
                    .scaleEffect(x: -1)
                    .frame(width: PixelMetrics.minTapTarget, height: PixelMetrics.minTapTarget)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Back")
            VStack(alignment: .leading, spacing: PixelMetrics.steps(2)) {
                PixelText(title, role: .title)
                    .foregroundStyle(theme.text.color)
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityAddTraits(.isHeader)
                FlowLayout(spacing: PixelMetrics.steps(3)) {
                    if let state { StateBadge(state) }
                    if let project { ProjectChip(project, tone: tone) }
                    if let place {
                        PixelText(place, role: .detail)
                            .foregroundStyle(theme.muted.color)
                            .accessibilityLabel(place.replacingOccurrences(of: " · ", with: ", "))
                    }
                }
            }
            Spacer(minLength: 0)
        }
        .padding(.leading, PixelMetrics.steps(1))
        .padding(.trailing, PixelMetrics.padding)
        .padding(.vertical, PixelMetrics.steps(3))
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(theme.bar.color)
        .overlay(alignment: .bottom) {
            Rectangle().fill(theme.line.color).frame(height: PixelMetrics.thickBorder)
        }
    }
}

/// A section's small muted title ("Progress"), or a button that folds the section ("Live detail ▼").
struct QuestSectionTitle: View {
    var title: String
    var open: Bool = true
    var toggle: (() -> Void)?

    @Environment(\.colorScheme) private var scheme

    var body: some View {
        let theme = PixelTheme(scheme)
        let label = HStack(spacing: PixelMetrics.steps(3)) {
            PixelText(title, role: .smallLabel)
                .foregroundStyle(theme.muted.color)
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
                .accessibilityLabel(title)
                .accessibilityValue(open ? "Shown" : "Hidden")
                .accessibilityHint(open ? "Hides it" : "Shows it")
                .accessibilityAddTraits(.isHeader)
        } else {
            label
                .accessibilityElement(children: .ignore)
                .accessibilityLabel(title)
                .accessibilityAddTraits(.isHeader)
        }
    }
}

/// The steps checklist: ✓ done, ▶ now, · not started, × where it stopped.
struct StepsList: View {
    var steps: [QuestStep]

    @Environment(\.colorScheme) private var scheme

    var body: some View {
        let theme = PixelTheme(scheme)
        VStack(alignment: .leading, spacing: PixelMetrics.steps(3)) {
            ForEach(steps) { step in
                HStack(alignment: .firstTextBaseline, spacing: PixelMetrics.steps(4)) {
                    PixelText(step.mark.symbol, role: .body)
                        .foregroundStyle((step.mark == .current ? theme.accent : step.mark == .done ? theme.text
                                          : theme.muted).color)
                        .frame(minWidth: 22, alignment: .center)
                    PixelText(step.text, role: .body)
                        .foregroundStyle((step.mark == .next ? theme.muted : theme.text).color)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .accessibilityElement(children: .ignore)
                .accessibilityLabel("\(step.mark.spoken): \(step.text)")
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// What a row can ask the screen to do.
struct DetailActions {
    var toggle: (String) -> Void = { _ in }
    var answer: (String, Bool) -> Void = { _, _ in }
}

/// One row of the live detail.
struct DetailRowView: View {
    var row: DetailRow
    var expanded: Bool
    var actions: DetailActions

    @Environment(\.colorScheme) private var scheme

    var body: some View {
        let theme = PixelTheme(scheme)
        switch row.kind {
        case .text(let fromUser, let text):
            let capped = CappedText(text, maxLines: QuestMetrics.textLines)
            VStack(alignment: .leading, spacing: PixelMetrics.steps(1)) {
                if fromUser {
                    PixelText("Prompt", role: .smallLabel).foregroundStyle(theme.muted.color)
                }
                PixelText(capped.text, role: .body)
                    .foregroundStyle(theme.text.color)
                    .fixedSize(horizontal: false, vertical: true)
                if let more = capped.moreLine {
                    PixelText(more, role: .detail).foregroundStyle(theme.muted.color)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(fromUser ? "Prompt: \(capped.text)" : "Claude: \(capped.text)")
        case .tool(let tool, let summary, let result):
            ToolRow(tool: tool, summary: summary, result: result, expanded: expanded) { actions.toggle(row.id) }
        case .diff(let filePath, let hunks):
            DiffRow(filePath: filePath, diff: DiffLines(hunks), expanded: expanded) { actions.toggle(row.id) }
        case .approval(let approvalId, let tool, let summary, let status):
            ApprovalPrompt(tool: tool, summary: summary, status: status) { approve in
                actions.answer(approvalId, approve)
            }
        case .note(let fromUser, let text, let handback, let prompt):
            let capped = CappedText(text, maxLines: QuestMetrics.textLines)
            let speaker = fromUser ? (handback ? "You → Marcel, handing back" : "You → this quest")
                : (prompt ? "Marcel → this quest · the task" : "Marcel → this quest")
            let spoken = fromUser ? (handback ? "You, handing back to Marcel" : "You, to this quest")
                : (prompt ? "Marcel's task for this quest" : "Marcel, to this quest")
            DialogBox(speaker: speaker, text: capped.text + (capped.moreLine.map { "\n\($0)" } ?? ""))
                .accessibilityElement(children: .ignore)
                .accessibilityLabel("\(spoken): \(capped.text)")
        case .line(let kind, let text):
            let capped = CappedText(text, maxLines: 6)
            PixelText(capped.text + (capped.moreLine.map { " \($0)" } ?? ""), role: .detail)
                .foregroundStyle((kind == .error ? theme.accent : theme.muted).color)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}

/// A tool call, folded to one line ("$ python3 -m pytest -q"); open, it shows what came back.
struct ToolRow: View {
    var tool: String
    var summary: String
    var result: DetailRow.ToolResult?
    var expanded: Bool
    var toggle: () -> Void

    @Environment(\.colorScheme) private var scheme

    private var outcome: String {
        guard let result else { return "running" }
        if result.denied { return "denied" }
        return result.isError ? "failed" : "done"
    }

    var body: some View {
        let theme = PixelTheme(scheme)
        let line = tool == "Bash" ? "$ \(summary)" : "\(tool): \(summary)"
        VStack(alignment: .leading, spacing: 0) {
            Button(action: toggle) {
                HStack(alignment: .firstTextBaseline, spacing: PixelMetrics.steps(3)) {
                    PixelGlyph(expanded ? .more : .pointer, color: theme.muted)
                        .accessibilityHidden(true)
                    Text(verbatim: CappedText(line, maxLines: 2).text)
                        .font(.system(.footnote, design: .monospaced))
                        .foregroundStyle(theme.text.color)
                        .lineLimit(expanded ? nil : 2)
                        .frame(maxWidth: .infinity, alignment: .leading)
                    if result?.isError == true || result?.denied == true {
                        PixelText(outcome, role: .smallLabel).foregroundStyle(theme.accent.color)
                    }
                }
                .padding(PixelMetrics.steps(4))
                .frame(minHeight: PixelMetrics.minTapTarget)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel("\(tool == "Bash" ? "Command" : tool): \(summary), \(outcome)")
            .accessibilityValue(expanded ? "Open" : "Folded")
            .accessibilityHint(expanded ? "Folds the answer away" : "Shows what came back")
            if expanded {
                let capped = CappedText(result?.text ?? "Waiting for it to finish…", maxLines: QuestMetrics.resultLines)
                VStack(alignment: .leading, spacing: PixelMetrics.steps(2)) {
                    Text(verbatim: capped.text.isEmpty ? "(no output)" : capped.text)
                        .font(.system(.footnote, design: .monospaced))
                        .foregroundStyle((result?.isError == true ? theme.accent : theme.text).color)
                        .fixedSize(horizontal: false, vertical: true)
                    if let more = capped.moreLine ?? (result?.truncated == true ? "…cut short by the runner" : nil) {
                        PixelText(more, role: .detail).foregroundStyle(theme.muted.color)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, PixelMetrics.steps(4))
                .padding(.bottom, PixelMetrics.steps(4))
                .accessibilityElement(children: .combine)
            }
        }
        .background(theme.bar.color)
        .overlay(Rectangle().strokeBorder(theme.line.color.opacity(0.35), lineWidth: PixelMetrics.thinBorder))
    }
}

/// A diff: folded to "calc.py +1 −1"; open, its lines with + and − in the margin and the code coloured.
struct DiffRow: View {
    var filePath: String
    var diff: DiffLines
    var expanded: Bool
    var toggle: () -> Void

    @Environment(\.colorScheme) private var scheme

    var body: some View {
        let theme = PixelTheme(scheme)
        let language = SyntaxColours.language(for: filePath)
        VStack(alignment: .leading, spacing: 0) {
            Button(action: toggle) {
                HStack(alignment: .firstTextBaseline, spacing: PixelMetrics.steps(3)) {
                    PixelGlyph(expanded ? .more : .pointer, color: theme.muted)
                        .accessibilityHidden(true)
                    Text(verbatim: filePath)
                        .font(.system(.footnote, design: .monospaced))
                        .foregroundStyle(theme.text.color)
                        .lineLimit(2)
                        .truncationMode(.head)
                        .frame(maxWidth: .infinity, alignment: .leading)
                    HStack(spacing: PixelMetrics.steps(2)) {
                        PixelText("+\(diff.added)", role: .smallLabel)
                            .foregroundStyle(ProjectChip.Tone.green.color(in: theme).color)
                        PixelText("−\(diff.removed)", role: .smallLabel)
                            .foregroundStyle(ProjectChip.Tone.rose.color(in: theme).color)
                    }
                    .fixedSize()
                }
                .padding(PixelMetrics.steps(4))
                .frame(minHeight: PixelMetrics.minTapTarget)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Change to \(filePath): \(diff.spokenCounts)")
            .accessibilityValue(expanded ? "Open" : "Folded")
            .accessibilityHint(expanded ? "Folds the change away" : "Shows the changed lines")
            if expanded {
                VStack(alignment: .leading, spacing: 0) {
                    ForEach(Array(diff.lines.enumerated()), id: \.offset) { _, line in
                        DiffLineView(line: line, language: language)
                    }
                    if let more = diff.moreLine {
                        PixelText(more, role: .detail)
                            .foregroundStyle(theme.muted.color)
                            .padding(.top, PixelMetrics.steps(2))
                    }
                }
                .padding(.horizontal, PixelMetrics.steps(4))
                .padding(.bottom, PixelMetrics.steps(4))
                .accessibilityElement(children: .ignore)
                .accessibilityLabel(diff.lines.filter { $0.kind != .hunk }.prefix(20).map { line in
                    "\(line.kind == .added ? "Added" : line.kind == .removed ? "Removed" : "Unchanged"): \(line.text)"
                }.joined(separator: "\n"))
            }
        }
        .background(theme.bar.color)
        .overlay(Rectangle().strokeBorder(theme.line.color.opacity(0.35), lineWidth: PixelMetrics.thinBorder))
    }
}

/// One diff line: a coloured bar and sign in the margin, the code in syntax colours. Every colour is one PixelKit
/// already checks for contrast on the bar colour behind it.
struct DiffLineView: View {
    var line: DiffLines.Line
    var language: SyntaxColours.Language?

    @Environment(\.colorScheme) private var scheme

    var body: some View {
        let theme = PixelTheme(scheme)
        let sign: String, mark: PixelColor?
        switch line.kind {
        case .added: (sign, mark) = ("+", ProjectChip.Tone.green.color(in: theme))
        case .removed: (sign, mark) = ("−", ProjectChip.Tone.rose.color(in: theme))
        case .context: (sign, mark) = (" ", nil)
        case .hunk: (sign, mark) = (" ", nil)
        }
        return HStack(alignment: .firstTextBaseline, spacing: PixelMetrics.steps(2)) {
            Text(verbatim: sign)
                .font(.system(.footnote, design: .monospaced))
                .foregroundStyle((mark ?? theme.muted).color)
            Text(Self.coloured(line, language: language, theme: theme))
                .font(.system(.footnote, design: .monospaced))
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
        .padding(.leading, PixelMetrics.steps(2))
        .overlay(alignment: .leading) {
            if let mark { Rectangle().fill(mark.color).frame(width: PixelMetrics.thinBorder) }
        }
        .padding(.vertical, 1)
    }

    static func coloured(_ line: DiffLines.Line, language: SyntaxColours.Language?, theme: PixelTheme)
        -> AttributedString
    {
        if line.kind == .hunk {
            var hunk = AttributedString(line.text)
            hunk.foregroundColor = ProjectChip.Tone.blue.color(in: theme).color
            return hunk
        }
        var text = AttributedString()
        for (run, token) in SyntaxColours.runs(line.text, language: language) {
            var piece = AttributedString(run)
            piece.foregroundColor = switch token {
            case .plain: theme.text.color
            case .keyword: ProjectChip.Tone.blue.color(in: theme).color
            case .string: ProjectChip.Tone.gold.color(in: theme).color
            case .comment: theme.muted.color
            case .number: ProjectChip.Tone.teal.color(in: theme).color
            }
            text.append(piece)
        }
        return text
    }
}

/// An inline approval prompt (B-12): what the quest wants to do, Approve / Deny, and once answered, the answer.
struct ApprovalPrompt: View {
    var tool: String
    var summary: String
    var status: ApprovalStatus
    var answer: (Bool) -> Void

    @Environment(\.colorScheme) private var scheme

    var body: some View {
        let theme = PixelTheme(scheme)
        DialogBox(speaker: "Asks to use \(tool)", text: summary.isEmpty ? "Wants to use \(tool)." : summary) {
            if status.isOpen {
                ChoicePrompt([.init(id: "approve", title: "Approve"), .init(id: "deny", title: "Deny")]) { choice in
                    answer(choice.id == "approve")
                }
            } else if let words = status.words {
                PixelText(words, role: .smallLabel)
                    .foregroundStyle(theme.muted.color)
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityLabel("Answer: \(words)")
            }
        }
    }
}

/// A message you sent that the transcript does not show yet.
struct PendingNoteView: View {
    var note: PendingNote

    var body: some View {
        DialogBox(speaker: note.sent ? "You → this quest · sent" : "You → this quest · sending…", text: note.text)
            .accessibilityElement(children: .ignore)
            .accessibilityLabel("You, to this quest, \(note.sent ? "sent" : "sending"): \(note.text)")
    }
}

/// The quest's actions (wireframe 2A's chips): stop, hand back to Marcel, move to project, open in Claude.
struct QuestToolbar<Move: View>: View {
    var canStop: Bool
    var busy: Bool
    var claudeURL: URL?
    var stop: () -> Void
    var handBack: () -> Void
    var move: Move
    var open: (URL) -> Void

    init(canStop: Bool, busy: Bool, claudeURL: URL?, stop: @escaping () -> Void, handBack: @escaping () -> Void,
         open: @escaping (URL) -> Void, @ViewBuilder move: () -> Move) {
        self.canStop = canStop
        self.busy = busy
        self.claudeURL = claudeURL
        self.stop = stop
        self.handBack = handBack
        self.open = open
        self.move = move()
    }

    @Environment(\.colorScheme) private var scheme

    var body: some View {
        let theme = PixelTheme(scheme)
        FlowLayout(spacing: PixelMetrics.steps(3)) {
            if canStop {
                chip("Stop", tone: ProjectChip.Tone.rose.color(in: theme), action: stop)
                    .accessibilityLabel("Stop this quest")
            }
            chip("Hand back to Marcel", tone: ProjectChip.Tone.teal.color(in: theme), action: handBack)
            move
            if let claudeURL {
                chip("Open in Claude", tone: ProjectChip.Tone.blue.color(in: theme)) { open(claudeURL) }
                    .accessibilityLabel("Open in Claude, in Safari")
            }
        }
        .disabled(busy)
        .opacity(busy ? 0.6 : 1)
        .padding(.horizontal, QuestMetrics.inset.leading)
        .padding(.top, PixelMetrics.steps(2))
    }

    private func chip(_ title: String, tone: PixelColor, action: @escaping () -> Void) -> some View {
        Button(action: action) { QuestChip(title: title, ink: tone) }
            .buttonStyle(.plain)
    }
}

/// A chip-styled action: an outlined label at least a tap target tall.
struct QuestChip: View {
    var title: String
    var ink: PixelColor

    var body: some View {
        PixelText(title, role: .smallLabel)
            .lineLimit(1)
            .foregroundStyle(ink.color)
            .padding(.horizontal, PixelMetrics.steps(4))
            .padding(.vertical, PixelMetrics.steps(2) + 1)
            .overlay(Rectangle().strokeBorder(ink.color, lineWidth: PixelMetrics.thinBorder))
            .fixedSize()
            .frame(minHeight: PixelMetrics.minTapTarget)
            .contentShape(Rectangle())
    }
}

/// "Talk to this quest…": a pixel box with the text field and a ▶ send button. Only an empty message cannot be sent.
struct QuestComposer: View {
    @Binding var text: String
    var send: () -> Void

    @Environment(\.colorScheme) private var scheme
    @Environment(\.questStill) private var still

    static let placeholder = "Talk to this quest…"

    private var canSend: Bool { !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }

    var body: some View {
        let theme = PixelTheme(scheme)
        HStack(alignment: .center, spacing: PixelMetrics.steps(3)) {
            if still {
                PixelText(text.isEmpty ? Self.placeholder : text, role: .body)
                    .foregroundStyle((text.isEmpty ? theme.muted : theme.text).color)
                    .frame(maxWidth: .infinity, alignment: .leading)
            } else {
                TextField(text: $text, axis: .vertical) {
                    Text(Self.placeholder).foregroundStyle(theme.muted.color)
                }
                .lineLimit(1...5)
                .pixelText(.body)
                .foregroundStyle(theme.text.color)
                .tint(theme.accent.color)
                .accessibilityLabel("Message to this quest")
                .accessibilityHint("Goes to this quest only, not to Marcel")
            }
            Button(action: send) {
                PixelGlyph(.pointer, color: canSend ? theme.accent : theme.muted, pixel: 3)
                    .frame(width: PixelMetrics.minTapTarget, height: PixelMetrics.minTapTarget)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .disabled(!canSend)
            .accessibilityLabel("Send to this quest")
        }
        .padding(.leading, PixelMetrics.padding)
        .padding(.vertical, PixelMetrics.steps(1))
        .pixelBox(fill: theme.dialog, line: theme.line, shadow: nil)
        .padding(.horizontal, QuestMetrics.inset.leading)
        .padding(.top, PixelMetrics.steps(2))
        .padding(.bottom, PixelMetrics.steps(4))
    }
}

/// A problem in plain words, with a way to dismiss it.
struct QuestProblem: View {
    var text: String
    var dismiss: () -> Void

    @Environment(\.colorScheme) private var scheme

    var body: some View {
        let theme = PixelTheme(scheme)
        Button(action: dismiss) {
            PixelText(text, role: .detail)
                .foregroundStyle(theme.accent.color)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(PixelMetrics.steps(4))
                .pixelBox(fill: theme.dialog, line: theme.accent, shadow: nil, border: PixelMetrics.thinBorder)
        }
        .buttonStyle(.plain)
        .accessibilityLabel(text)
        .accessibilityHint("Dismisses this message")
    }
}

/// Lays its children out left to right and wraps to a new line when the row is full.
struct FlowLayout: Layout {
    var spacing: CGFloat

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let width = proposal.width ?? .infinity
        let rows = arrange(subviews, width: width)
        let height = rows.map(\.height).reduce(0, +) + spacing * CGFloat(max(rows.count - 1, 0))
        let used = rows.map(\.width).max() ?? 0
        return CGSize(width: proposal.width ?? used, height: height)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        var y = bounds.minY
        for row in arrange(subviews, width: bounds.width) {
            var x = bounds.minX
            for index in row.indices {
                let size = subviews[index].sizeThatFits(.init(width: bounds.width, height: nil))
                subviews[index].place(at: CGPoint(x: x, y: y + (row.height - size.height) / 2),
                                      proposal: .init(width: min(size.width, bounds.width), height: size.height))
                x += size.width + spacing
            }
            y += row.height + spacing
        }
    }

    private struct Row {
        var indices: [Int] = []
        var width: CGFloat = 0
        var height: CGFloat = 0
    }

    private func arrange(_ subviews: Subviews, width: CGFloat) -> [Row] {
        var rows: [Row] = []
        var row = Row()
        for index in subviews.indices {
            let size = subviews[index].sizeThatFits(.init(width: width, height: nil))
            let needed = row.indices.isEmpty ? size.width : row.width + spacing + size.width
            if needed > width, !row.indices.isEmpty {
                rows.append(row)
                row = Row()
            }
            row.width = row.indices.isEmpty ? min(size.width, width) : row.width + spacing + size.width
            row.height = max(row.height, size.height)
            row.indices.append(index)
        }
        if !row.indices.isEmpty { rows.append(row) }
        return rows
    }
}

/// A project's colour, from its id: the same hash as the Chat and Quests tabs, so a project keeps its colour.
enum QuestTone {
    static func tone(for projectId: String) -> ProjectChip.Tone {
        var hash: UInt32 = 2_166_136_261
        for byte in projectId.utf8 {
            hash = (hash ^ UInt32(byte)) &* 16_777_619
        }
        let tones = ProjectChip.Tone.allCases
        return tones[Int(hash % UInt32(tones.count))]
    }

    static func badge(_ state: Components.Schemas.TaskState) -> StateBadge.Kind {
        switch state {
        case .queued: .queued
        case .starting: .starting
        case .working: .working
        case .needsYou: .needsYou
        case .done: .done
        case .failed: .failed
        case .stopped: .stopped
        case .silent: .silent
        case .unknown: .unknown
        }
    }

    /// "NUC · sonnet".
    static func place(_ task: HubTask) -> String {
        "\(task.location == .nuc ? "NUC" : "Cloud") · \(task.model)"
    }
}
