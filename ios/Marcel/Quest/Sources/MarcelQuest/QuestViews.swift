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

/// The quest's top strip (wireframe Q2): ‹ back, the quest's name in bold and "Stop" on the right while it can be
/// stopped, on white with a thin line under it. Where it runs is the body's first line (`QuestMetaLine`).
struct QuestHeader: View {
    var title: String
    /// Asks to stop the quest; nil once it can't be stopped.
    var stop: (() -> Void)?
    var back: () -> Void

    var body: some View {
        ChatHeader(title, back: back) {
            if let stop {
                StopButton(stop: stop)
            }
        }
    }
}

/// "Stop" in the header: muted words, at least a tap target in size.
struct StopButton: View {
    var stop: () -> Void

    @Environment(\.colorScheme) private var scheme

    var body: some View {
        Button(action: stop) {
            Text("Stop")
                .font(.subheadline)
                .foregroundStyle(ChatPalette(scheme).muted.color)
                .frame(minWidth: PixelMetrics.minTapTarget, minHeight: PixelMetrics.minTapTarget)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Stop this quest")
    }
}

/// The body's first line, in small pixel capitals (wireframe Q2): "HOME ENERGY · NUC · SONNET", with the state's word
/// in front once the quest no longer runs ("DONE · HOME ENERGY · …"). A heading for VoiceOver.
struct QuestMetaLine: View {
    var text: String

    @Environment(\.colorScheme) private var scheme

    static func text(project: String?, place: String?, state: StateBadge.Kind?) -> String {
        let running: Set<StateBadge.Kind> = [.starting, .working]
        let word = state.flatMap { running.contains($0) ? nil : $0.spokenTitle }
        return [word, project, place].compactMap { $0 }.joined(separator: " · ")
    }

    var body: some View {
        PixelText(text, role: .smallLabel)
            .foregroundStyle(ChatPalette(scheme).muted.color)
            .fixedSize(horizontal: false, vertical: true)
            .frame(maxWidth: .infinity, alignment: .leading)
            .accessibilityLabel(text.replacingOccurrences(of: " · ", with: ", "))
            .accessibilityAddTraits(.isHeader)
    }
}

/// A section's small muted title, or a button that folds the section ("Live detail ▼").
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

/// The steps checklist (wireframe Q2): ✓ done in teal, ▶ now in rose and bold, · not started muted, × where it
/// stopped in red. Body text, like the chat.
struct StepsList: View {
    var steps: [QuestStep]

    @Environment(\.colorScheme) private var scheme

    var body: some View {
        let palette = ChatPalette(scheme)
        VStack(alignment: .leading, spacing: 6) {
            ForEach(steps) { step in
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Text(step.mark.symbol)
                        .font(.body.weight(.bold))
                        .foregroundStyle(color(step.mark, palette).color)
                        .frame(minWidth: 22, alignment: .center)
                    Text(step.text)
                        .font(.body.weight(step.mark == .current ? .bold : .regular))
                        .foregroundStyle(color(step.mark, palette).color)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .accessibilityElement(children: .ignore)
                .accessibilityLabel("\(step.mark.spoken): \(step.text)")
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func color(_ mark: QuestStep.Mark, _ palette: ChatPalette) -> PixelColor {
        switch mark {
        case .done: ChatCardKind.started.label(in: palette)
        case .current: ChatCardKind.done.label(in: palette)
        case .stopped: ChatCardKind.failed.label(in: palette)
        case .next: palette.muted
        }
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
            // Claude's text reads like the chat: plain formatted text with the shared Markdown blocks.
            let capped = CappedText(text, maxLines: QuestMetrics.textLines)
            VStack(alignment: .leading, spacing: PixelMetrics.steps(1)) {
                if fromUser {
                    PixelText("Prompt", role: .smallLabel).foregroundStyle(theme.muted.color)
                }
                MarkdownView(parts: ItemMarkdown.parse(capped.text), style: .chat)
                if let more = capped.moreLine {
                    Text(more).font(.footnote).foregroundStyle(theme.muted.color)
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
            QuestNoteView(speaker: speaker, text: capped.text + (capped.moreLine.map { "\n\($0)" } ?? ""),
                          fromUser: fromUser)
                .accessibilityElement(children: .ignore)
                .accessibilityLabel("\(spoken): \(capped.text)")
        case .workflow(let flow):
            WorkflowRow(flow: flow, expanded: expanded) { actions.toggle(row.id) }
        case .line(let kind, let text):
            let capped = CappedText(text, maxLines: 6)
            Text(capped.text + (capped.moreLine.map { " \($0)" } ?? ""))
                .font(.footnote)
                .foregroundStyle((kind == .error ? theme.accent : theme.muted).color)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}

/// A workflow (app API 0.7.3): folded to one row with what it does, "7 of 10 done" and how it ended; open, each
/// agent by its label, grouped by phase, ✓ and what it gave back once done, ▶ while it runs. All of it untrusted
/// plain text, capped.
struct WorkflowRow: View {
    var flow: DetailRow.Workflow
    var expanded: Bool
    var toggle: () -> Void

    /// Lines of an agent's summary shown when the row is open; the workflow's own summary gets twice as many.
    static let summaryLines = 3

    @Environment(\.colorScheme) private var scheme

    /// The mark after the count: "Finished", "Failed", "Stopped"; nothing while it runs.
    var mark: (text: String, kind: ChatCardKind)? {
        switch flow.status {
        case .running: nil
        case .completed: ("Finished", .started)  // the same colour as a done agent's ✓
        case .failed: ("Failed", .failed)
        case .stopped: ("Stopped", .failed)
        case .unknown: ("Ended", .started)
        }
    }

    var body: some View {
        let palette = ChatPalette(scheme)
        let title = CappedText(flow.title, maxLines: 2).text
        VStack(alignment: .leading, spacing: 0) {
            Button(action: toggle) {
                HStack(alignment: .center, spacing: PixelMetrics.steps(3)) {
                    PixelGlyph(expanded ? .more : .pointer, color: palette.muted)
                        .frame(width: 14)  // the same width open or folded: the title never shifts
                        .accessibilityHidden(true)
                    VStack(alignment: .leading, spacing: 2) {
                        PixelText("Workflow", role: .smallLabel)
                            .foregroundStyle(ChatCardKind.started.label(in: palette).color)
                        Text(verbatim: title)
                            .font(.body.weight(.semibold))
                            .foregroundStyle(palette.ink.color)
                            .lineLimit(expanded ? nil : 2)
                        HStack(spacing: 6) {
                            Text(flow.progress)
                                .font(.footnote)
                                .foregroundStyle(palette.muted.color)
                            if let mark {
                                Text("· \(mark.text)")
                                    .font(.footnote.weight(.bold))
                                    .foregroundStyle(mark.kind.label(in: palette).color)
                            }
                        }
                    }
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
                .padding(PixelMetrics.steps(4))
                .frame(minHeight: PixelMetrics.minTapTarget)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Workflow: \(title), \(flow.progress)\(mark.map { ", \($0.text.lowercased())" } ?? "")")
            .accessibilityValue(expanded ? "Open" : "Folded")
            .accessibilityHint(expanded ? "Folds its agents away" : "Shows each agent")
            if expanded {
                VStack(alignment: .leading, spacing: PixelMetrics.steps(3)) {
                    ForEach(Array(flow.groups.enumerated()), id: \.offset) { _, group in
                        if let phase = group.phase {
                            PixelText(CappedText(phase, maxLines: 1).text, role: .smallLabel)
                                .foregroundStyle(palette.muted.color)
                                .accessibilityAddTraits(.isHeader)
                        }
                        ForEach(group.agents, id: \.id) { agent in
                            AgentLine(agent: agent, palette: palette)
                        }
                    }
                    if let summary = flow.summary {
                        let capped = CappedText(summary, maxLines: Self.summaryLines * 2)
                        Text(verbatim: capped.text + (capped.moreLine.map { "\n\($0)" } ?? ""))
                            .font(.footnote)
                            .foregroundStyle(palette.ink.color)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, PixelMetrics.steps(4))
                .padding(.bottom, PixelMetrics.steps(4))
            }
        }
        .fixedSize(horizontal: false, vertical: true)
        .background(palette.card.color, in: RoundedRectangle(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(
            (flow.status == .failed ? ChatCardKind.failed.frame(in: palette) : palette.line).color,
            lineWidth: flow.status == .failed ? 2 : 1))
    }

    /// One agent: ✓ or ▶, its label, and under it what it gave back once done.
    struct AgentLine: View {
        var agent: DetailRow.Workflow.Agent
        var palette: ChatPalette

        var body: some View {
            let label = CappedText(agent.label, maxLines: 1).text
            let summary = agent.summary.map { CappedText($0, maxLines: WorkflowRow.summaryLines) }
            let tint = agent.done ? ChatCardKind.started.label(in: palette) : ChatCardKind.done.label(in: palette)
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text(agent.done ? "✓" : "▶")
                    .font(.footnote.weight(.bold))
                    .foregroundStyle(tint.color)
                    .frame(minWidth: 16, alignment: .center)
                VStack(alignment: .leading, spacing: 2) {
                    Text(verbatim: label)
                        .font(.footnote.weight(agent.done ? .regular : .bold))
                        .foregroundStyle(palette.ink.color)
                    if let summary {
                        Text(verbatim: summary.text + (summary.moreLine.map { "\n\($0)" } ?? ""))
                            .font(.footnote)
                            .foregroundStyle(palette.muted.color)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
            .accessibilityElement(children: .ignore)
            .accessibilityLabel("\(agent.done ? "Done" : "Working"): \(label)\(summary.map { ". \($0.text)" } ?? "")")
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
            .accessibilityLabel("\(tool == "Bash" ? "Command" : tool): \(CappedText(summary, maxLines: 2).text), \(outcome)")
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
        .background(ChatPalette(scheme).well.color, in: RoundedRectangle(cornerRadius: 10))
        .overlay(RoundedRectangle(cornerRadius: 10).strokeBorder(ChatPalette(scheme).line.color, lineWidth: 1))
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
        .background(ChatPalette(scheme).well.color, in: RoundedRectangle(cornerRadius: 10))
        .overlay(RoundedRectangle(cornerRadius: 10).strokeBorder(ChatPalette(scheme).line.color, lineWidth: 1))
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

/// An inline approval prompt (B-12), as the chat's "needs you" card: what the quest wants to do, Approve / Not now,
/// and once answered, the answer.
struct ApprovalPrompt: View {
    var tool: String
    var summary: String
    var status: ApprovalStatus
    var answer: (Bool) -> Void

    @Environment(\.colorScheme) private var scheme

    var body: some View {
        let palette = ChatPalette(scheme)
        ChatCard(.needsYou, label: "Needs you", meta: tool) {
            Text(summary.isEmpty ? "Wants to use \(tool)." : summary)
                .font(.body)
                .foregroundStyle(palette.ink.color)
                .fixedSize(horizontal: false, vertical: true)
            if status.isOpen {
                HStack(spacing: 8) {
                    ChatCardButton("Approve", kind: .needsYou, filled: true) { answer(true) }
                    ChatCardButton("Not now", kind: .needsYou) { answer(false) }
                }
            } else if let words = status.words {
                Text(words)
                    .font(.subheadline)
                    .foregroundStyle(palette.muted.color)
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityLabel("Answer: \(words)")
            }
        }
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Asks to use \(tool)")
    }
}

/// A message you sent that the transcript does not show yet: your bubble, with "Sending…" or "Sent" under it.
struct PendingNoteView: View {
    var note: PendingNote

    var body: some View {
        QuestNoteView(speaker: note.sent ? "Sent to this quest" : "Sending…", text: note.text, fromUser: true)
            .accessibilityElement(children: .ignore)
            .accessibilityLabel("You, to this quest, \(note.sent ? "sent" : "sending"): \(note.text)")
    }
}

/// A note in the transcript, like the chat: yours in your bubble on the right, Marcel's as plain text, each with who
/// it went to in small muted words.
struct QuestNoteView: View {
    var speaker: String
    var text: String
    var fromUser: Bool

    @Environment(\.colorScheme) private var scheme

    var body: some View {
        let palette = ChatPalette(scheme)
        VStack(alignment: fromUser ? .trailing : .leading, spacing: 4) {
            if fromUser {
                HStack {
                    Spacer(minLength: 64)
                    ChatBubble(text)
                }
            } else {
                Text(text)
                    .font(.body)
                    .foregroundStyle(palette.ink.color)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Text(speaker)
                .font(.caption)
                .foregroundStyle(palette.muted.color)
        }
        .frame(maxWidth: .infinity, alignment: fromUser ? .trailing : .leading)
    }
}

/// The quest's other actions: hand back to Marcel, move to project, open in Claude ("Stop" is in the header).
struct QuestToolbar<Move: View>: View {
    var busy: Bool
    var claudeURL: URL?
    var handBack: () -> Void
    var move: Move
    var open: (URL) -> Void

    init(busy: Bool, claudeURL: URL?, handBack: @escaping () -> Void, open: @escaping (URL) -> Void,
         @ViewBuilder move: () -> Move) {
        self.busy = busy
        self.claudeURL = claudeURL
        self.handBack = handBack
        self.open = open
        self.move = move()
    }

    @Environment(\.colorScheme) private var scheme

    var body: some View {
        let theme = PixelTheme(scheme)
        FlowLayout(spacing: PixelMetrics.steps(3)) {
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

/// An action under the quest, outlined like the chat's card buttons: rounded, at least a tap target tall.
struct QuestChip: View {
    var title: String
    var ink: PixelColor

    var body: some View {
        Text(title)
            .font(.subheadline.weight(.semibold))
            .lineLimit(1)
            .foregroundStyle(ink.color)
            .padding(.horizontal, 12)
            .frame(minHeight: 34)
            .overlay(RoundedRectangle(cornerRadius: 10).strokeBorder(ink.color, lineWidth: 1.5))
            .fixedSize()
            .padding(.vertical, 5)
            .contentShape(Rectangle())
    }
}

/// "Talk to this quest…": the chat's composer (a white strip with the rounded field and the round send button). Only
/// an empty message cannot be sent; it goes to this quest only, not to Marcel.
struct QuestComposer: View {
    @Binding var text: String
    var send: () -> Void

    @Environment(\.questStill) private var still

    static let placeholder = "Talk to this quest…"

    var body: some View {
        ChatComposerField(text: $text, placeholder: Self.placeholder, fieldLabel: "Message to this quest only",
                          still: still, sendLabel: "Send to this quest", send: send)
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
            Text(text)
                .font(.subheadline)
                .foregroundStyle(ChatCardKind.failed.label(in: ChatPalette(scheme)).color)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(12)
                .background(ChatPalette(scheme).card.color, in: RoundedRectangle(cornerRadius: 12))
                .overlay(RoundedRectangle(cornerRadius: 12)
                    .strokeBorder(ChatCardKind.failed.frame(in: ChatPalette(scheme)).color, lineWidth: 2))
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

/// How the quest view words a task: its state badge and where it runs.
enum QuestTone {
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
