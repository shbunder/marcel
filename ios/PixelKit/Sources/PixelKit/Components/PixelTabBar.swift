import SwiftUI

/// The bottom tab strip: Chat, Quests, Map, Items. The selected tab is in the accent colour and underlined.
/// Each tab is a button VoiceOver reads by name, with the "selected" trait on the current one.
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
        let theme = PixelTheme(scheme)
        HStack(spacing: 0) {
            ForEach(tabs) { tab in
                let isSelected = tab.id == selection
                Button {
                    selection = tab.id
                } label: {
                    PixelText(tab.title, role: .smallLabel)
                        .lineLimit(1)
                        .fixedSize()
                        .foregroundStyle((isSelected ? theme.accent : theme.muted).color)
                        .padding(.bottom, PixelMetrics.steps(2))
                        .overlay(alignment: .bottom) {
                            if isSelected {
                                Rectangle().fill(theme.accent.color).frame(height: PixelMetrics.grid)
                            }
                        }
                        .frame(maxWidth: .infinity, minHeight: PixelMetrics.minTapTarget)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityLabel(tab.title)
                .accessibilityAddTraits(isSelected ? .isSelected : [])
            }
        }
        .padding(.top, PixelMetrics.steps(1))
        .padding(.bottom, PixelMetrics.steps(2))
        .frame(maxWidth: .infinity)
        .background(Rectangle().fill(theme.bar.color))
        .overlay(alignment: .top) {
            Rectangle().fill(theme.line.color).frame(height: PixelMetrics.thickBorder)
        }
    }
}

#Preview("Tab bar") {
    PixelPreview { _ in PixelTabBar(selection: .constant("chat")) }
}
