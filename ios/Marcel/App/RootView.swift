import MarcelChat
import MarcelKit
import PixelKit
import SwiftUI

/// Pairing until the phone has a token; then the four tabs.
struct RootView: View {
    let connection: HubConnection

    var body: some View {
        switch connection.phase {
        case .unpaired:
            PairingView(connection: connection)
        case .paired(let hub):
            MainTabs(connection: connection, hub: hub)
        }
    }
}

/// Chat, Quests, Map and Items under PixelKit's tab bar. Chat is the home tab.
struct MainTabs: View {
    let connection: HubConnection
    @State private var chat: ChatModel
    @State private var tab: PixelTabBar.Tab.ID = "chat"
    @Environment(\.colorScheme) private var scheme

    private static let conversationKey = "chat.mainConversationId"

    init(connection: HubConnection, hub: PairedHub) {
        self.connection = connection
        let client = HubClient.make(serverURL: hub.serverURL, tokenStore: connection.tokenStore)
        _chat = State(initialValue: ChatModel(
            service: LiveChatService(client: client),
            conversationId: UserDefaults.standard.string(forKey: Self.conversationKey),
            remember: { UserDefaults.standard.set($0, forKey: Self.conversationKey) }))
    }

    var body: some View {
        VStack(spacing: 0) {
            Group {
                switch tab {
                case "quests": QuestsView()
                case "map": ComingSoonView(title: "Map")
                case "items": ComingSoonView(title: "Items")
                default:
                    NavigationStack {
                        ChatScreen(model: chat, offline: offlineText)
                            .toolbar(.hidden, for: .navigationBar)
                    }
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            // Until settings exist, unpairing is a long press on the tab bar.
            PixelTabBar(selection: $tab)
                .contextMenu {
                    Button("Unpair this phone", role: .destructive, action: unpair)
                }
                .accessibilityAction(named: "Unpair this phone", unpair)
        }
        .background(PixelTheme(scheme).background.color)
    }

    private func unpair() {
        UserDefaults.standard.removeObject(forKey: Self.conversationKey)
        connection.unpair()
    }

    /// The stream's own words when it is not live.
    private var offlineText: String? {
        switch connection.stream?.status ?? .idle {
        case .offline: "Offline · showing what's saved"
        case .connecting, .idle: nil
        case .live: nil
        case .unpaired: "The hub no longer accepts this phone"
        }
    }
}

/// A tab that is not built yet.
struct ComingSoonView: View {
    var title: String
    @Environment(\.colorScheme) private var scheme

    var body: some View {
        let theme = PixelTheme(scheme)
        VStack(spacing: PixelMetrics.steps(4)) {
            PixelText(title, role: .title).foregroundStyle(theme.text.color)
            PixelText("Coming soon.", role: .body).foregroundStyle(theme.muted.color)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(theme.background.color)
        .accessibilityElement(children: .combine)
    }
}
