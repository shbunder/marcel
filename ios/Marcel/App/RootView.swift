import MarcelChat
import MarcelItems
import MarcelKit
import MarcelMap
import MarcelQuest
import MarcelQuests
import MarcelSettings
import PixelKit
import SwiftUI
import UIKit
import UserNotifications

/// Pairing until the phone has a token; then the four tabs.
struct RootView: View {
    let connection: HubConnection
    let router: LinkRouter

    var body: some View {
        switch connection.phase {
        case .unpaired:
            PairingView(connection: connection)
                // A link that could not be opened (a pairing link) or a note from unpairing, said here too.
                .overlay(alignment: .top) {
                    if let notice = router.notice {
                        LinkNoticeView(text: notice) { router.notice = nil }
                    }
                }
                // Links wait again until the phone is paired.
                .onAppear { router.detach() }
        case .paired(let hub):
            MainTabs(connection: connection, hub: hub, router: router)
        }
    }
}

/// What opens over the tabs: Settings, or where a link leads.
enum OverTabs: Identifiable, Hashable {
    case settings
    case quest(String)
    case artifact(String)
    case status(PushAlert?)

    var id: Self { self }
}

/// Chat, Quests, Map and Items under PixelKit's tab bar. Chat is the home tab.
struct MainTabs: View {
    let connection: HubConnection
    let hub: PairedHub
    let router: LinkRouter
    private let settings: LiveSettingsService
    @State private var chat: ChatModel
    @State private var tab: PixelTabBar.Tab.ID = "chat"
    @State private var over: OverTabs?
    @State private var mapOpening: String?
    @Environment(\.colorScheme) private var scheme

    private static let conversationKey = "chat.mainConversationId"

    init(connection: HubConnection, hub: PairedHub, router: LinkRouter) {
        self.connection = connection
        self.hub = hub
        self.router = router
        let client = HubClient.make(serverURL: hub.serverURL, tokenStore: connection.tokenStore)
        settings = LiveSettingsService(client: client)
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
                case "map":
                    // "+ new" on the map asks Marcel for a project in the chat.
                    MapView(conversationId: chat.conversationId, opening: $mapOpening) { request in
                        chat.draft = request
                        tab = "chat"
                    }
                case "items": ItemsView(conversationId: chat.conversationId)
                default:
                    // Settings open from the avatar header.
                    ChatScreen(model: chat, offline: offlineText, openSettings: { over = .settings })
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            PixelTabBar(selection: $tab)
        }
        .background(PixelTheme(scheme).background.color)
        // A link that could not be opened says why, over the tabs, until tapped away.
        .overlay(alignment: .top) {
            if let notice = router.notice {
                LinkNoticeView(text: notice) { router.notice = nil }
            }
        }
        .sheet(item: $over) { over in
            NavigationStack { screen(over) }
        }
        // An open quest view follows its transcript and approvals through the live stream's cache.
        .environment(\.liveEvents, connection.cache)
        // Links (and pushes) that arrived before the tabs were up are looked up now; later ones at once.
        .onChange(of: router.destination, initial: true) { _, destination in
            guard let destination else { return }
            show(destination)
            router.consume()
        }
        .task {
            APNsRegistrar.shared.onToken = { [settings, hub] token in
                Task { _ = try? await settings.setPush(deviceId: hub.deviceId, target: .init(kind: .apns, apnsToken: token)) }
            }
            await router.attach(LiveLinkLookup(cache: connection.cache, service: settings,
                                               mainConversationId: { [chat] in chat.conversationId }))
            // Apple can change the token: ask again at each launch when this phone already uses APNs.
            if await UNUserNotificationCenter.current().notificationSettings().authorizationStatus == .authorized,
               UIApplication.shared.isRegisteredForRemoteNotifications {
                UIApplication.shared.registerForRemoteNotifications()
            }
        }
    }

    /// Goes where a link leads: a tab, or a screen over the tabs.
    private func show(_ destination: LinkDestination) {
        switch destination {
        case .chat(let messageId):
            over = nil
            tab = "chat"
            chat.focus = messageId
        case .room(let projectId):
            over = nil
            tab = "map"
            mapOpening = projectId
        case .quest(let taskId): over = .quest(taskId)
        case .artifact(let id): over = .artifact(id)
        case .status(let alert): over = .status(alert)
        }
    }

    @ViewBuilder private func screen(_ over: OverTabs) -> some View {
        switch over {
        case .settings:
            SettingsView(model: SettingsModel(service: settings, tokens: APNsRegistrar.shared, thisDevice: hub.deviceId),
                         status: { StatusModel(service: settings) },
                         unpair: unpair)
        case .quest(let taskId): QuestView(taskId: taskId)
        case .artifact(let id): ArtifactViewer(artifactId: id, conversationId: chat.conversationId)
        case .status(let alert): StatusView(model: StatusModel(service: settings, alert: alert))
        }
    }

    /// Forgets the hub on this phone. Settings has told the hub first; `note` says so when it couldn't, and shows on
    /// the pairing screen.
    private func unpair(_ note: String? = nil) {
        over = nil
        router.notice = note
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
