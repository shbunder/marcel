import AvatarKit
import MarcelKit
import PixelKit
import SwiftUI

/// The buddy sheet (B-49, ADR-013): a tap on the chat's avatar opens it. Every live buddy with its avatar, its name and
/// the last message of its chat; the one shown has a check. Picking one switches this phone to it; "New buddy" adds
/// one. Below, what the avatar used to open: this buddy's notebook and Settings.
struct BuddyPicker: View {
    var buddies: [BuddyChoice]
    var current: String?
    var links: AvatarLinks
    var pick: (String) -> Void
    var add: (String) -> Void

    @State private var naming = false
    @State private var newName = ""
    @Environment(\.colorScheme) private var scheme
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        ScrollView {
            BuddyList(buddies: buddies, current: current, links: links, pick: pick, newBuddy: { naming = true })
        }
        .background(ChatPalette(scheme).card.color)
        .alert("New buddy", isPresented: $naming) {
            TextField("Name", text: $newName)
            Button("Add") {
                add(newName)
                newName = ""
                dismiss()
            }
            Button("Cancel", role: .cancel) { newName = "" }
        } message: {
            Text("A buddy has its own chat, notebook and look. Pick a name; you can change it later.")
        }
    }
}

/// The sheet's rows without the scroll view (snapshots draw this). A view of its own, so it reads light or dark from
/// where it is drawn.
struct BuddyList: View {
    var buddies: [BuddyChoice]
    var current: String?
    var links: AvatarLinks
    var pick: (String) -> Void
    var newBuddy: () -> Void

    @Environment(\.colorScheme) private var scheme
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        let palette = ChatPalette(scheme)
        VStack(alignment: .leading, spacing: 0) {
            PixelText("Your buddies", role: .title)
                .foregroundStyle(palette.ink.color)
                .padding(.horizontal, ChatScreenMetrics.inset)
                .padding(.top, 20)
                .padding(.bottom, 12)
                .accessibilityAddTraits(.isHeader)
            ForEach(buddies) { buddy in
                BuddyRow(buddy: buddy, shown: buddy.id == current) {
                    pick(buddy.id)
                    dismiss()
                }
                Divider().overlay(palette.line.color).padding(.leading, ChatScreenMetrics.inset + 56)
            }
            Button(action: newBuddy) {
                Label("New buddy", systemImage: "plus.circle.fill")
                    .font(.body.weight(.semibold))
                    .foregroundStyle(palette.link.color)
                    .frame(maxWidth: .infinity, minHeight: 52, alignment: .leading)
                    .padding(.horizontal, ChatScreenMetrics.inset)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            if !links.actions.isEmpty {
                Divider().overlay(palette.line.color).padding(.vertical, 8)
                ForEach(Array(links.actions.enumerated()), id: \.offset) { _, link in
                    Button {
                        dismiss()
                        link.run()
                    } label: {
                        Text(link.name)
                            .font(.body)
                            .foregroundStyle(palette.ink.color)
                            .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
                            .padding(.horizontal, ChatScreenMetrics.inset)
                            .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                }
            }
        }
        .padding(.bottom, 24)
        .background(palette.card.color)
    }
}

/// One buddy: its avatar, its name in bold, the last message in the muted colour, and a check when shown.
struct BuddyRow: View {
    var buddy: BuddyChoice
    var shown: Bool
    var action: () -> Void

    @Environment(\.colorScheme) private var scheme
    @Environment(\.chatStillAvatars) private var still

    var body: some View {
        let palette = ChatPalette(scheme)
        Button(action: action) {
            HStack(spacing: 12) {
                ChatAvatar(state: .idle, box: 44, still: still, name: buddy.name, agentId: buddy.id)
                    .accessibilityHidden(true)
                VStack(alignment: .leading, spacing: 2) {
                    Text(buddy.name)
                        .font(.headline.weight(.bold))
                        .foregroundStyle(palette.ink.color)
                    Text(buddy.lastMessage ?? "No messages yet")
                        .font(.subheadline)
                        .foregroundStyle(palette.muted.color)
                        .lineLimit(1)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                if shown {
                    Image(systemName: "checkmark")
                        .font(.body.weight(.semibold))
                        .foregroundStyle(palette.link.color)
                }
            }
            .padding(.horizontal, ChatScreenMetrics.inset)
            .padding(.vertical, 10)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(BuddyRow.spoken(buddy, shown: shown))
        .accessibilityAddTraits(shown ? [.isButton, .isSelected] : .isButton)
    }

    /// "Pip, shown on this phone. Last message: …".
    static func spoken(_ buddy: BuddyChoice, shown: Bool) -> String {
        var parts = [buddy.name]
        if shown { parts.append("shown on this phone") }
        parts.append(buddy.lastMessage.map { "Last message: \($0)" } ?? "No messages yet")
        return parts.joined(separator: ". ")
    }
}
