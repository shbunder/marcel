import MarcelKit
import SwiftData
import SwiftUI

@main
struct MarcelApp: App {
    private let container: ModelContainer
    @State private var connection: HubConnection
    @UIApplicationDelegateAdaptor(AppDelegate.self) private var delegate

    init() {
        let container: ModelContainer
        do {
            container = try MarcelCache.makeContainer()
        } catch {
            // A cache that cannot open is rebuilt from the hub; start with an empty one in memory.
            container = try! MarcelCache.makeContainer(inMemory: true)
        }
        self.container = container
        _connection = State(initialValue: HubConnection.live(cache: MarcelCache(container: container)))
    }

    var body: some Scene {
        WindowGroup {
            RootView(connection: connection, router: delegate.router)
                .task { connection.start() }
                // `marcel://` links from the ntfy app, Safari or another app. Checked and routed by `LinkRouter`.
                .onOpenURL { delegate.router.open($0) }
        }
        .modelContainer(container)
    }
}
