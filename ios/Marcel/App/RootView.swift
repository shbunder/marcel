import AvatarKit
import MarcelChat
import MarcelItems
import MarcelKit
import MarcelLook
import MarcelMap
import MarcelNotebook
import MarcelQuest
import MarcelQuests
import MarcelSettings
import MarcelWelcome
import PixelKit
import SwiftUI
import UIKit
import UserNotifications

/// The welcome screen until the phone is paired, "Meet your helper" until the helper is onboarded, then the four
/// tabs (`AppFlow`, S-17.5). While paired, the Face ID lock covers everything (`AppLock`).
struct RootView: View {
    let connection: HubConnection
    let router: LinkRouter
    @State private var flow: AppFlow
    @State private var lock = AppLock()

    init(connection: HubConnection, router: LinkRouter) {
        self.connection = connection
        self.router = router
        _flow = State(initialValue: AppFlow(
            connection: connection,
            makeService: { hub in
                LiveWelcomeService(client: HubClient.make(serverURL: hub.serverURL,
                                                          tokenStore: connection.tokenStore))
            },
            makeLookService: { hub in
                LiveLookService(client: HubClient.make(serverURL: hub.serverURL, tokenStore: connection.tokenStore))
            }))
    }

    var body: some View {
        screen
            // Face ID in a window above everything, Settings and other sheets included.
            .appLock(lock, paired: connection.phase != .unpaired)
            .task(id: connection.phase) { await flow.update() }
            .onChange(of: connection.phase) { old, new in
                if old == .unpaired, new != .unpaired { lock.signedIn() }
                // However the phone was signed out (Settings, the hub refusing it): the chat forgets its ids too.
                if new == .unpaired {
                    UserDefaults.standard.removeObject(forKey: MainTabs.conversationKey)
                    UserDefaults.standard.removeObject(forKey: MainTabs.agentKey)
                    CurrentBuddy.shared.forget()
                }
            }
    }

    @ViewBuilder private var screen: some View {
        switch (flow.screen, connection.phase) {
        case (.main, .paired(let hub)):
            MainTabs(connection: connection, hub: hub, router: router,
                     faceID: Binding(get: { lock.enabled }, set: { lock.enabled = $0 }),
                     signOut: { await flow.signOut() })
        case (.onboarding, .paired):
            if let onboarding = flow.onboarding {
                OnboardingView(model: onboarding) { flow.finishedOnboarding() }
            }
        case (.checking, .paired):
            ProgressView()
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        default:
            WelcomeView { text in
                try await connection.pair(scanned: text, deviceName: UIDevice.current.name)
            }
            // A link that could not be opened (a pairing link) or a note from signing out, said here too.
            .overlay(alignment: .top) {
                if let notice = router.notice {
                    LinkNoticeView(text: notice) { router.notice = nil }
                }
            }
            // Links wait again until the phone is paired.
            .onAppear { router.detach() }
        }
    }
}

/// What opens over the tabs: Settings, Marcel's notebook, or where a link leads.
enum OverTabs: Identifiable, Hashable {
    case settings
    case notebook
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
    /// Face ID on or off (`AppLock.enabled`), for Settings.
    let faceID: Binding<Bool>
    /// "Sign out of this phone" (`AppFlow.signOut`).
    let signOut: () async -> String?
    private let settings: LiveSettingsService
    private let notebook: LiveNotebookService
    private let looks: LiveLookService
    /// "What Marcel carries": the brain's rollovers, followed from the moment the tabs are up.
    @State private var rollovers = RolloverLog()
    @State private var chat: ChatModel
    @State private var tab: PixelTabBar.Tab.ID = "chat"
    @State private var over: OverTabs?
    @State private var mapOpening: String?
    @Environment(\.colorScheme) private var scheme

    static let conversationKey = "chat.mainConversationId"
    static let agentKey = "chat.agentId"

    init(connection: HubConnection, hub: PairedHub, router: LinkRouter, faceID: Binding<Bool>,
         signOut: @escaping () async -> String?) {
        self.connection = connection
        self.hub = hub
        self.router = router
        self.faceID = faceID
        self.signOut = signOut
        let client = HubClient.make(serverURL: hub.serverURL, tokenStore: connection.tokenStore)
        settings = LiveSettingsService(client: client)
        notebook = LiveNotebookService(client: client)
        looks = LiveLookService(client: client)
        _chat = State(initialValue: ChatModel(
            service: LiveChatService(client: client),
            conversationId: UserDefaults.standard.string(forKey: Self.conversationKey),
            remember: { UserDefaults.standard.set($0, forKey: Self.conversationKey) },
            avatarDirectory: ChatModel.avatarDirectory,
            agentId: UserDefaults.standard.string(forKey: Self.agentKey),
            rememberAgent: { UserDefaults.standard.set($0, forKey: Self.agentKey) }))
    }

    var body: some View {
        VStack(spacing: 0) {
            Group {
                switch tab {
                case "quests": QuestsView()
                case "map":
                    // "+ new" on the map asks Marcel for a project in the chat, after anything already typed there.
                    MapView(conversationId: chat.conversationId, opening: $mapOpening) { request in
                        chat.propose(request)
                        tab = "chat"
                    }
                case "items": ItemsView(conversationId: chat.conversationId)
                default:
                    // Marcel's notebook and Settings open from the avatar header (it asks which).
                    ChatScreen(model: chat, offline: offlineText, openSettings: { over = .settings },
                               openNotebook: { over = .notebook })
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            PixelTabBar(selection: $tab)
        }
        // White behind the tab bar and the home indicator, like the tab bar itself (S-18.6).
        .background(ChatPalette(scheme).card.color)
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
        // Every tab and sheet says the agent's name as `GET /me` last gave it (Settings can rename it).
        .environment(\.agentName, chat.agentName ?? AgentName.fallback)
        // "Ask about this" in any item viewer: the Chat tab, with the item on the next message.
        .environment(\.askInChat) { item in
            over = nil
            tab = "chat"
            chat.attach(item)
        }
        // A push about another buddy opens the app on that buddy (B-49): the chat switches (every tab follows), and
        // only then does its link open, so a switch never wipes the message it scrolls to.
        .onChange(of: router.buddy, initial: true) { _, buddy in
            guard let buddy else { return }
            Task {
                await chat.switchTo(buddy)
                router.consumeBuddy()
                if let destination = router.destination {
                    show(destination)
                    router.consume()
                }
            }
        }
        // Links (and pushes) that arrived before the tabs were up are looked up now; later ones at once. A push that
        // also switches buddy waits for the switch above.
        .onChange(of: router.destination, initial: true) { _, destination in
            guard let destination, router.buddy == nil else { return }
            show(destination)
            router.consume()
        }
        .task {
            // Also forgets the log and the notebook's drafts when the phone is unpaired.
            rollovers.attach(to: connection)
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
            SettingsView(model: settingsModel(), status: { StatusModel(service: settings) },
                         buddies: { BuddiesModel(service: settings, looks: looks) }, links: settingsLinks, signedOut: signedOut)
        case .notebook:
            NotebookView(model: NotebookModel(service: notebook, rollovers: rollovers, projectNames: { [connection] in
                Dictionary((try? connection.cache.projects())?.map { ($0.slug, $0.name) } ?? [],
                           uniquingKeysWith: { first, _ in first })
            }))
        case .quest(let taskId): QuestView(taskId: taskId)
        case .artifact(let id): ArtifactViewer(artifactId: id, conversationId: chat.conversationId)
        case .status(let alert): StatusView(model: StatusModel(service: settings, alert: alert))
        }
    }

    /// Settings, telling the chat header the agent's name (after a rename too).
    private func settingsModel() -> SettingsModel {
        let model = SettingsModel(service: settings, tokens: APNsRegistrar.shared, thisDevice: hub.deviceId)
        model.nameChanged = { [chat] name in chat.agentName = name }
        return model
    }

    /// What Settings needs from the app: the helper as the header draws it, the look picker's hub, Face ID, the
    /// notebook, and signing out.
    private var settingsLinks: SettingsAppLinks {
        SettingsAppLinks(
            avatar: AnyView(AvatarView(state: .idle, box: 56, sheet: chat.avatars.sheet(for: chat.agentId),
                                       name: chat.agentName ?? AgentName.fallback)),
            looks: looks, faceID: faceID, openNotebook: { over = .notebook }, signOut: signOut)
    }

    /// After "Sign out of this phone" (the app is on the welcome screen by now): what to say there when the hub
    /// wasn't told.
    private func signedOut(_ note: String?) {
        over = nil
        router.notice = note
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
