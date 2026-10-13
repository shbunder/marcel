import MarcelChat
import MarcelItems
import MarcelKit
import MarcelQuests
import PixelKit
import SwiftUI

/// The room's top strip (wireframe 5B): ◀ back to the map, the project's name and its goal.
struct RoomHeader: View {
    var room: ProjectRoom
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
            .accessibilityLabel("Back to the map")
            VStack(alignment: .leading, spacing: PixelMetrics.steps(1)) {
                PixelText(room.name, role: .title)
                    .foregroundStyle(theme.text.color)
                    .fixedSize(horizontal: false, vertical: true)
                if let goal = room.goal {
                    PixelText(goal, role: .detail)
                        .foregroundStyle(theme.muted.color)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(room.spokenTitle)
            .accessibilityAddTraits(.isHeader)
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

/// The pinned board: PixelKit's `ArtifactCard` with the board's first entries. Tapping it opens the board.
struct BoardView: View {
    var board: BoardCard
    var state: BoardState?

    var rows: [PreviewRow] {
        switch state {
        case .rows(let rows): rows
        case .failed(let problem): [PreviewRow(problem)]
        case .loading?, nil: [PreviewRow("Loading the board…")]
        }
    }

    var spoken: String {
        let lines = rows.map { row in [row.label, row.value].compactMap { $0 }.joined(separator: ", ") }
        return ([board.title, board.spokenBadge] + lines).joined(separator: ". ")
    }

    var body: some View {
        ArtifactCard(title: board.title, badge: board.badge) {
            ForEach(rows, id: \.self) { ArtifactRow($0.label, value: $0.value, emphasised: $0.emphasised) }
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(spoken)
    }
}

/// A group title in the room ("Waiting on you", "Items"), small and muted like the wireframe's `.sec`.
struct RoomSectionTitle: View {
    var title: String
    var spoken: String

    init(_ title: String, spoken: String? = nil) {
        self.title = title
        self.spoken = spoken ?? title
    }

    @Environment(\.colorScheme) private var scheme

    var body: some View {
        PixelText(title, role: .smallLabel)
            .foregroundStyle(PixelTheme(scheme).muted.color)
            .fixedSize(horizontal: false, vertical: true)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.top, PixelMetrics.steps(3))
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(spoken)
            .accessibilityAddTraits(.isHeader)
    }
}

/// One quest in the room: the quest log's card without the project chip (every quest here is in this project).
struct RoomQuest: View {
    var row: QuestRow
    var moving: Bool

    var spoken: String {
        // The room is the project; "Project: …" would only repeat it.
        row.spoken.replacingOccurrences(of: ". Project: \(row.project ?? "")", with: "")
    }

    var body: some View {
        QuestCard(title: row.title, state: row.state, stateText: row.stateText, project: nil, tone: row.tone,
                  detail: row.detail)
            .opacity(moving ? 0.6 : 1)
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(spoken)
            .accessibilityValue(moving ? "Moving" : "")
    }
}

/// The project's notes, read-only: the first lines of `memory/projects/<slug>.md`.
struct NotesView: View {
    var state: NotesState?
    @Environment(\.agentName) private var agentName

    static let maxLines = 6

    var lines: [String] {
        switch state {
        case .text(let markdown):
            let all = ItemMarkdown.plainText(ItemMarkdown.parse(markdown)).split(separator: "\n").map {
                $0.trimmingCharacters(in: .whitespaces)
            }.filter { !$0.isEmpty }
            guard !all.isEmpty else { return ["No notes yet."] }
            let more = all.count - Self.maxLines
            return Array(all.prefix(Self.maxLines)) + (more > 0 ? ["…and \(more) more \(more == 1 ? "line" : "lines")."] : [])
        case .empty?: return ["No notes yet. \(agentName) writes down what every quest here should know."]
        case .failed(let problem): return [problem]
        case .loading?, nil: return ["Loading the notes…"]
        }
    }

    @Environment(\.colorScheme) private var scheme

    var body: some View {
        let theme = PixelTheme(scheme)
        VStack(alignment: .leading, spacing: PixelMetrics.steps(2)) {
            ForEach(Array(lines.enumerated()), id: \.offset) { _, line in
                PixelText(line, role: .detail)
                    .foregroundStyle(theme.text.color)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(PixelMetrics.padding)
        .pixelBox(fill: theme.card, line: theme.line, shadow: nil, border: PixelMetrics.thinBorder)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Notes: " + lines.joined(separator: " "))
    }
}

/// A line under the composer: a sent message's confirmation, in the muted colour.
struct SentLine: View {
    var text: String

    @Environment(\.colorScheme) private var scheme

    var body: some View {
        PixelText(text, role: .detail)
            .foregroundStyle(PixelTheme(scheme).muted.color)
            .fixedSize(horizontal: false, vertical: true)
            .frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// Lays chips out left to right, wrapping onto the next line.
struct FlowLayout: Layout {
    var spacing: CGFloat

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let width = proposal.width ?? .infinity
        var x: CGFloat = 0, y: CGFloat = 0, line: CGFloat = 0, widest: CGFloat = 0
        for subview in subviews {
            let size = subview.sizeThatFits(.unspecified)
            if x > 0, x + size.width > width {
                x = 0
                y += line + spacing
                line = 0
            }
            x += size.width + spacing
            line = max(line, size.height)
            widest = max(widest, x - spacing)
        }
        return CGSize(width: proposal.width ?? widest, height: y + line)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        var x = bounds.minX, y = bounds.minY, line: CGFloat = 0
        for subview in subviews {
            let size = subview.sizeThatFits(.unspecified)
            if x > bounds.minX, x + size.width > bounds.maxX {
                x = bounds.minX
                y += line + spacing
                line = 0
            }
            subview.place(at: CGPoint(x: x, y: y), proposal: ProposedViewSize(size))
            x += size.width + spacing
            line = max(line, size.height)
        }
    }
}

/// The room's body under its header: the board, the quests by state, the items and the notes. The screen passes
/// tappable pieces; the snapshot the bare ones.
struct RoomContent<Board: View, Quest: View, Item: View>: View {
    var room: ProjectRoom
    var notes: NotesState?
    var problem: String?
    var dismiss: () -> Void
    @ViewBuilder var board: () -> Board
    @ViewBuilder var quest: (QuestRow) -> Quest
    @ViewBuilder var item: (RoomItem) -> Item

    @Environment(\.colorScheme) private var scheme

    var body: some View {
        VStack(alignment: .leading, spacing: MapMetrics.gap) {
            if let problem { ProblemLine(text: problem, dismiss: dismiss) }
            board()
            if room.quests.isEmpty {
                RoomSectionTitle("Quests")
                PixelText("No quests in this project right now.", role: .detail)
                    .foregroundStyle(PixelTheme(scheme).muted.color)
            }
            ForEach(room.quests.sections) { section in
                RoomSectionTitle(section.group.title, spoken: section.group.spokenTitle)
                ForEach(section.rows) { quest($0) }
            }
            RoomSectionTitle("Items")
            if room.items.isEmpty {
                PixelText("No items yet.", role: .detail)
                    .foregroundStyle(PixelTheme(scheme).muted.color)
            } else {
                FlowLayout(spacing: PixelMetrics.steps(3)) {
                    ForEach(room.items) { item($0) }
                }
            }
            RoomSectionTitle("Notes")
            NotesView(state: notes)
        }
        .padding(MapMetrics.inset)
    }
}

/// An item's chip, in the project's colour.
struct ItemChip: View {
    var item: RoomItem
    var tone: ProjectChip.Tone

    var body: some View {
        ProjectChip(item.title, tone: tone)
            .frame(minHeight: PixelMetrics.minTapTarget)
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(item.spoken)
    }
}

/// The room laid out in a plain stack, composer included, for the snapshot tests (`ImageRenderer` draws nothing in
/// a `ScrollView` and a yellow box for a `TextField`; `chatStillAvatars` makes the composer draw its text).
struct RoomStill: View {
    var room: ProjectRoom
    var board: BoardState?
    var notes: NotesState?
    var draft = ""
    var problem: String?
    var sent: String?

    @Environment(\.colorScheme) private var scheme

    var body: some View {
        VStack(spacing: 0) {
            RoomHeader(room: room, back: {})
            RoomContent(room: room, notes: notes, problem: problem, dismiss: {},
                        board: { BoardView(board: room.board, state: board) },
                        quest: { RoomQuest(row: $0, moving: false) },
                        item: { ItemChip(item: $0, tone: room.tone) })
            RoomComposer(room: room, draft: .constant(draft), sent: sent, send: {})
                .environment(\.chatStillAvatars, true)
        }
        .background(PixelTheme(scheme).background.color)
    }
}

/// The chat's composer, saying what a message here does, with the last message's confirmation over it.
struct RoomComposer: View {
    var room: ProjectRoom
    @Binding var draft: String
    var sent: String?
    var focused: FocusState<Bool>.Binding?
    var send: () -> Void

    @Environment(\.agentName) private var agentName

    static func placeholder(name: String) -> String { "Tell \(name) about this project…" }

    var body: some View {
        VStack(spacing: 0) {
            if let sent {
                SentLine(text: sent)
                    .padding(.horizontal, MapMetrics.inset.leading)
                    .padding(.top, PixelMetrics.steps(2))
            }
            Composer(text: $draft, placeholder: Self.placeholder(name: agentName), focused: focused, send: send)
        }
    }
}
