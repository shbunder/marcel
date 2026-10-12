import MarcelKit
import SwiftData
import SwiftUI

/// Pairing until the phone has a token; then the app. The chat (S-12.3) replaces `ConnectedView`.
struct RootView: View {
    let connection: HubConnection

    var body: some View {
        switch connection.phase {
        case .unpaired:
            PairingView(connection: connection)
        case .paired(let hub):
            ConnectedView(hub: hub, stream: connection.stream, unpair: connection.unpair)
        }
    }
}

struct ConnectedView: View {
    let hub: PairedHub
    let stream: EventStream?
    let unpair: () -> Void
    @Query(sort: \CachedMessage.createdAt) private var messages: [CachedMessage]

    var body: some View {
        NavigationStack {
            List {
                Section("Hub") {
                    LabeledContent("Address", value: hub.serverURL.absoluteString)
                    LabeledContent("Connection", value: statusText)
                    if let problem = stream?.lastProblem {
                        Text(problem).foregroundStyle(.secondary)
                    }
                }
                Section("Cached") {
                    LabeledContent("Messages", value: "\(messages.count)")
                }
                Section {
                    Button("Unpair this phone", role: .destructive, action: unpair)
                }
            }
            .navigationTitle("Marcel")
        }
    }

    private var statusText: String {
        switch stream?.status ?? .idle {
        case .idle: "Not connected"
        case .connecting: "Connecting…"
        case .live: "Live"
        case .offline(let reason): reason
        case .unpaired: "The hub no longer accepts this phone."
        }
    }
}
