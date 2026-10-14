import MarcelChat
import MarcelItems
import MarcelKit
import MarcelQuests
import PixelKit
import SwiftUI

/// The room's top strip (wireframe Q2): "‹" back to the map, the project's name in bold and its goal, on white.
struct RoomHeader: View {
    var room: ProjectRoom
    var back: () -> Void

    var body: some View {
        ChatHeader(room.name, subtitle: room.goal, spoken: room.spokenTitle, back: back,
                   backLabel: "Back to the map")
    }
}

/// The pinned board as a white card: a small label ("PROJECT BOARD · PINNED · V7") and the board's first entries.
/// Tapping it opens the board.
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

    @Environment(\.colorScheme) private var scheme

    var body: some View {
        let palette = ChatPalette(scheme)
        VStack(alignment: .leading, spacing: 6) {
            PixelText("\(board.title) · \(board.badge)", role: .smallLabel)
                .foregroundStyle(palette.muted.color)
                .fixedSize(horizontal: false, vertical: true)
            ForEach(rows, id: \.self) { row in
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Text(row.label)
                        .font(row.emphasised ? .subheadline.weight(.bold) : .subheadline)
                        .foregroundStyle(palette.ink.color)
                        .fixedSize(horizontal: false, vertical: true)
                    if let value = row.value {
                        Spacer(minLength: 8)
                        Text(value)
                            .font(.subheadline.weight(.semibold))
                            .foregroundStyle(palette.ink.color)
                    }
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(ChatScreenMetrics.padding)
        .chatSurface()
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(spoken)
    }
}

/// A group title in the room ("WAITING ON YOU", "ITEMS"): small pixel capitals, muted.
struct RoomSectionTitle: View {
    var title: String
    var spoken: String

    init(_ title: String, spoken: String? = nil) {
        self.title = title
        self.spoken = spoken ?? title
    }

    var body: some View {
        ChatSectionTitle(title, spoken: spoken)
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

/// Which part of a project's notes the room previews (S-18.6, from S-20.1): a notes file made since ADR-011 opens
/// with the project brief (five headings, "(not known yet)"); the room shows only what quests wrote under its
/// `## Notes` heading. An older file without that heading is shown from the top.
enum NotesPreview {
    /// The line the hub puts under `## Notes` in a new file: not a note, so not shown.
    static let placeholder = "What later threads in this project need to know."

    /// The Markdown under the `Notes` heading (up to the next heading of the same or a higher level), or the whole
    /// file when it has none, without the placeholder and empty lines.
    static func lines(_ markdown: String) -> [String] {
        let all = markdown.components(separatedBy: .newlines)
        var body = all[...]
        if let start = all.firstIndex(where: { level(of: $0, named: "notes") != nil }),
           let depth = level(of: all[start], named: "notes") {
            let rest = all[(start + 1)...]
            let end = rest.firstIndex { line in
                guard let found = headingLevel(line) else { return false }
                return found <= depth
            } ?? all.endIndex
            body = all[(start + 1)..<end]
        }
        return body.map { $0.trimmingCharacters(in: .whitespaces) }
            .filter { !$0.isEmpty && $0 != placeholder }
    }

    /// The heading level of a Markdown heading line (`## Notes` is 2), else nil.
    static func headingLevel(_ line: String) -> Int? {
        let trimmed = line.trimmingCharacters(in: .whitespaces)
        let hashes = trimmed.prefix { $0 == "#" }.count
        guard (1...6).contains(hashes), trimmed.dropFirst(hashes).first == " " else { return nil }
        return hashes
    }

    private static func level(of line: String, named name: String) -> Int? {
        guard let level = headingLevel(line) else { return nil }
        let title = line.trimmingCharacters(in: .whitespaces).dropFirst(level).trimmingCharacters(in: .whitespaces)
        return title.lowercased() == name ? level : nil
    }
}

/// The project's notes, read-only (editing them is the notebook's job): the first lines quests wrote under `## Notes`,
/// in the chat's text style (Marcel's text uses the S-18.3 Markdown blocks), in a white card.
struct NotesView: View {
    var state: NotesState?
    @Environment(\.agentName) private var agentName
    @Environment(\.colorScheme) private var scheme

    static let maxLines = 6

    /// What the card shows: Markdown and how many more lines there are, or a sentence.
    enum Shown: Equatable {
        case notes(markdown: String, more: Int)
        case message(String)
    }

    var shown: Shown {
        switch state {
        case .text(let markdown):
            let kept = NotesPreview.lines(markdown)
            guard !kept.isEmpty else { return .message(emptyText) }
            return .notes(markdown: kept.prefix(Self.maxLines).joined(separator: "\n"),
                          more: max(0, kept.count - Self.maxLines))
        case .empty?: return .message(emptyText)
        case .failed(let problem): return .message(problem)
        case .loading?, nil: return .message("Loading the notes…")
        }
    }

    private var emptyText: String { "No notes yet. \(agentName) writes down what every quest here should know." }

    /// What VoiceOver reads (and the tests check): the shown notes as plain lines, then "…and 3 more lines."
    var lines: [String] {
        switch shown {
        case .message(let text): return [text]
        case .notes(let markdown, let more):
            let plain = ItemMarkdown.plainText(ItemMarkdown.parse(markdown)).split(separator: "\n")
                .map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }
            return plain + (more > 0 ? [Self.moreText(more)] : [])
        }
    }

    static func moreText(_ more: Int) -> String { "…and \(more) more \(more == 1 ? "line" : "lines")." }

    var body: some View {
        let palette = ChatPalette(scheme)
        VStack(alignment: .leading, spacing: 6) {
            switch shown {
            case .message(let text):
                Text(text)
                    .font(.subheadline)
                    .foregroundStyle(palette.muted.color)
                    .fixedSize(horizontal: false, vertical: true)
            case .notes(let markdown, let more):
                MarkdownView(parts: ItemMarkdown.parse(markdown), style: .chat)
                if more > 0 {
                    Text(Self.moreText(more))
                        .font(.footnote)
                        .foregroundStyle(palette.muted.color)
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(ChatScreenMetrics.padding)
        .chatSurface()
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Notes: " + lines.joined(separator: " "))
    }
}

/// A line over the composer: a sent message's confirmation, in the muted colour.
struct SentLine: View {
    var text: String

    @Environment(\.colorScheme) private var scheme

    var body: some View {
        Text(text)
            .font(.footnote)
            .foregroundStyle(ChatPalette(scheme).muted.color)
            .fixedSize(horizontal: false, vertical: true)
            .frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// The room's body under its header (wireframe Q2): "Open tracker" when the project has one (B-46), the board, the
/// quests in their groups with counts, the items as cards and the notes. The screen passes tappable pieces; the snapshot the bare ones.
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
        let palette = ChatPalette(scheme)
        VStack(alignment: .leading, spacing: MapMetrics.gap) {
            if let problem { ProblemLine(text: problem, dismiss: dismiss) }
            if let tracker = room.tracker {
                TrackerButton(url: tracker)
            }
            board()
            if room.quests.isEmpty {
                RoomSectionTitle("Quests")
                Text("No quests in this project right now.")
                    .font(.subheadline)
                    .foregroundStyle(palette.muted.color)
            }
            // Claude Code Projects' groups (ADR-011, S-12.14): needs you, working, ready for review, done, ended.
            ForEach(room.sections) { section in
                RoomSectionTitle("\(section.group.title) · \(section.rows.count)",
                                 spoken: "\(section.group.title), \(section.rows.count == 1 ? "1 quest" : "\(section.rows.count) quests")")
                ForEach(section.rows) { quest($0) }
            }
            RoomSectionTitle("Items")
            if room.items.isEmpty {
                Text("No items yet.")
                    .font(.subheadline)
                    .foregroundStyle(palette.muted.color)
            } else {
                ForEach(room.items) { item($0) }
            }
            RoomSectionTitle("Notes")
            NotesView(state: notes)
        }
        .padding(MapMetrics.inset)
    }
}

/// An item as a white card (wireframe Q2): its title in bold and a pixel meta line ("PULL REQUEST · OPEN").
struct ItemCard: View {
    var item: RoomItem

    @Environment(\.colorScheme) private var scheme

    var body: some View {
        let palette = ChatPalette(scheme)
        VStack(alignment: .leading, spacing: 6) {
            Text(item.title)
                .font(.body.weight(.bold))
                .foregroundStyle(palette.ink.color)
                .fixedSize(horizontal: false, vertical: true)
            PixelText(item.meta, role: .smallLabel)
                .foregroundStyle(palette.muted.color)
                .fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: .infinity, minHeight: PixelMetrics.minTapTarget, alignment: .leading)
        .padding(.horizontal, 12)
        .padding(.vertical, 10)
        .chatSurface()
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(item.spoken)
    }
}

/// The room laid out in a plain stack, composer included, for the snapshot tests (`ImageRenderer` draws nothing in
/// a `ScrollView` and a yellow box for a `TextField`, so the composer draws its words).
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
                        item: { ItemCard(item: $0) })
            RoomComposer(room: room, draft: .constant(draft), sent: sent, still: true, send: {})
        }
        .background(ChatPalette(scheme).well.color)
    }
}

/// The room's rounded composer, saying what a message here does, with the last message's confirmation over it. A
/// message goes to the one chat, tagged with the project (B-32).
struct RoomComposer: View {
    var room: ProjectRoom
    @Binding var draft: String
    var sent: String?
    var still = false
    var focused: FocusState<Bool>.Binding?
    var send: () -> Void

    @Environment(\.agentName) private var agentName

    static func placeholder(name: String, project: String) -> String { "Message \(name) about \(project)…" }

    var body: some View {
        VStack(spacing: 0) {
            if let sent {
                SentLine(text: sent)
                    .padding(.horizontal, MapMetrics.inset.leading)
                    .padding(.vertical, 6)
            }
            ChatComposerField(text: $draft, placeholder: Self.placeholder(name: agentName, project: room.name),
                              fieldLabel: "Message to \(agentName) about \(room.name)", still: still,
                              focused: focused, send: send)
        }
    }
}
