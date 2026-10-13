import SwiftUI

/// The bottom tab strip: Chat, Quests, Map, Items, in the chat's language (S-18.6, wireframe Q2 v10): white with a
/// thin line above it, the selected tab in Marcel's rose with a short bar over it. Each tab is a button VoiceOver
/// reads by name, with the "selected" trait on the current one.
public struct PixelTabBar: View {
    public struct Tab: Identifiable, Hashable, Sendable {
        public var id: String
        public var title: String

        public init(id: String, title: String) {
            self.id = id
            self.title = title
        }
    }

    /// The app's four tabs (wireframes 1A–5A).
    public static let appTabs: [Tab] = [
        Tab(id: "chat", title: "Chat"), Tab(id: "quests", title: "Quests"),
        Tab(id: "map", title: "Map"), Tab(id: "items", title: "Items"),
    ]

    public var tabs: [Tab]
    @Binding public var selection: Tab.ID

    @Environment(\.colorScheme) private var scheme

    public init(tabs: [Tab] = PixelTabBar.appTabs, selection: Binding<Tab.ID>) {
        self.tabs = tabs
        _selection = selection
    }

    public var body: some View {
        let palette = ChatPalette(scheme)
        let active = ChatCardKind.done.label(in: palette)
        HStack(spacing: 0) {
            ForEach(tabs) { tab in
                let isSelected = tab.id == selection
                Button {
                    selection = tab.id
                } label: {
                    PixelText(tab.title, role: .smallLabel)
                        .lineLimit(1)
                        .fixedSize()
                        .foregroundStyle((isSelected ? active : palette.muted).color)
                        .frame(maxWidth: .infinity, minHeight: PixelMetrics.minTapTarget)
                        .overlay(alignment: .top) {
                            if isSelected {
                                Capsule().fill(active.color).frame(width: 28, height: 2)
                            }
                        }
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityLabel(tab.title)
                .accessibilityAddTraits(isSelected ? .isSelected : [])
            }
        }
        .padding(.bottom, PixelMetrics.steps(1))
        .frame(maxWidth: .infinity)
        .background(palette.card.color)
        .overlay(alignment: .top) {
            Rectangle().fill(palette.line.color).frame(height: 1)
        }
    }
}

#Preview("Tab bar") {
    PixelPreview { _ in PixelTabBar(selection: .constant("chat")) }
}
