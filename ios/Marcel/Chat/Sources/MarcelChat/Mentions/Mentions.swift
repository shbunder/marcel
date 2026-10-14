import MarcelKit
import PixelKit
import SwiftUI

/// `@` and `#` in the composer (B-42): what is being typed after the sign, so the app can ask `GET /mentions`.
public struct MentionTrigger: Equatable, Sendable {
    /// `@` lists quests and items, `#` lists projects.
    public var sign: Character
    /// What follows the sign ("fi" in "run it @fi").
    public var query: String
    /// Where the sign and the query sit in the draft, so a pick can take them out.
    public var range: Range<String.Index>

    /// The kinds the sign asks for.
    public var kinds: [MentionKind] { sign == "#" ? [.project] : [.task, .artifact] }

    /// The `@word` or `#word` at the end of the draft (where you are typing), if any. A sign starts a word: after a
    /// space, a newline or at the very start, so "a@b.be" is not a mention.
    public static func find(in draft: String) -> MentionTrigger? {
        guard let sign = draft.lastIndex(where: { $0 == "@" || $0 == "#" }) else { return nil }
        let query = draft[draft.index(after: sign)...]
        guard !query.contains(where: { $0.isNewline }), query.count <= 40 else { return nil }
        if sign > draft.startIndex {
            let before = draft[draft.index(before: sign)]
            guard before.isWhitespace else { return nil }
        }
        return MentionTrigger(sign: draft[sign], query: String(query), range: sign..<draft.endIndex)
    }
}

extension Mention {
    /// "@Fix flaky login test", "#House".
    public var label: String { (kind == .project ? "#" : "@") + title }

    /// What VoiceOver says for the chip: "Quest: Fix flaky login test".
    public var spoken: String {
        let kind = switch self.kind {
        case .task: "Quest"
        case .project: "Project"
        case .artifact: "Item"
        }
        return "\(kind): \(title)"
    }
}

/// A mention as a chip: under a sent message (tap opens it) or above the composer (X takes it off).
struct MentionChip: View {
    var mention: Mention
    var open: (() -> Void)?
    var remove: (() -> Void)?

    @Environment(\.colorScheme) private var scheme

    var body: some View {
        let palette = ChatPalette(scheme)
        HStack(spacing: 4) {
            Text(mention.label)
                .font(.footnote.weight(.semibold))
                .lineLimit(1)
            if remove != nil {
                Image(systemName: "xmark")
                    .font(.caption2.weight(.bold))
                    .accessibilityHidden(true)
            }
        }
        .foregroundStyle(palette.link.color)
        .padding(.horizontal, 10)
        .frame(minHeight: 28)
        .background(palette.card.color, in: Capsule())
        .overlay(Capsule().strokeBorder(palette.line.color, lineWidth: 1))
        .padding(.vertical, 8)
        .contentShape(Rectangle())
        .onTapGesture { (open ?? remove)?() }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(mention.spoken)
        .accessibilityAddTraits(.isButton)
        .accessibilityHint(remove != nil ? "Takes it off the message" : "Opens it")
    }
}

/// What `@` or `#` found, above the composer: tap one to make it a chip.
struct MentionSuggestions: View {
    var trigger: MentionTrigger
    var found: [Mention]
    var unreachable = false
    var pick: (Mention) -> Void

    @Environment(\.colorScheme) private var scheme

    var body: some View {
        let palette = ChatPalette(scheme)
        VStack(alignment: .leading, spacing: 0) {
            if found.isEmpty {
                Text(unreachable ? "Can't reach the hub to look that up."
                     : trigger.sign == "#" ? "No project matches." : "No quest or item matches.")
                    .font(.subheadline)
                    .foregroundStyle(palette.muted.color)
                    .padding(12)
            }
            ForEach(Array(found.prefix(6).enumerated()), id: \.offset) { index, mention in
                Button { pick(mention) } label: {
                    HStack(spacing: 8) {
                        Text(mention.label)
                            .font(.subheadline.weight(.semibold))
                            .foregroundStyle(palette.ink.color)
                            .lineLimit(1)
                        Spacer(minLength: 8)
                        Text(mention.spoken.components(separatedBy: ":").first ?? "")
                            .font(.caption)
                            .foregroundStyle(palette.muted.color)
                    }
                    .padding(.horizontal, 12)
                    .frame(minHeight: PixelMetrics.minTapTarget)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityLabel(mention.spoken)
                .accessibilityHint("Adds it to the message")
                if index < min(found.count, 6) - 1 {
                    Rectangle().fill(palette.line.color).frame(height: 1)
                }
            }
        }
        .background(palette.card.color, in: RoundedRectangle(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(palette.line.color, lineWidth: 1))
    }
}

/// The chips going with the next message, above the composer.
struct ComposerMentions: View {
    var mentions: [Mention]
    var remove: (Mention) -> Void

    var body: some View {
        HStack(spacing: 6) {
            ForEach(mentions, id: \.id) { mention in
                MentionChip(mention: mention, remove: { remove(mention) })
            }
            Spacer(minLength: 0)
        }
    }
}

/// What goes with the next message, on the composer's strip above the field: the mention chips, then the attached
/// item. Nothing when there is neither.
struct ComposerExtras: View {
    var mentions: [Mention]
    var attachment: String?
    var unpick: (Mention) -> Void
    var detach: () -> Void

    var body: some View {
        if !mentions.isEmpty || attachment != nil {
            VStack(alignment: .leading, spacing: 4) {
                if !mentions.isEmpty {
                    ComposerMentions(mentions: mentions, remove: unpick)
                }
                if let attachment {
                    AttachedItem(title: attachment, remove: detach)
                }
            }
        }
    }
}
