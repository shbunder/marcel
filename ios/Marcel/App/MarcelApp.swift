import MarcelKit
import SwiftData
import SwiftUI

@main
struct MarcelApp: App {
    private let container: ModelContainer
    @State private var connection: HubConnection

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
            RootView(connection: connection)
                .task { connection.start() }
        }
        .modelContainer(container)
    }
}
