import MarcelKit
import SwiftUI
import XCTest

@testable import MarcelChat

/// The chat screen's main states in light and dark (and the plain feed at the largest text size), compared with
/// `__Snapshots__/` pixel for pixel. Re-record with `make -C ios chat-record` after a deliberate change and look at
/// the PNGs before committing them.
@MainActor
final class ChatSnapshotTests: XCTestCase {
    private let twoModes: [Snapshot.Variant] = [.light, .dark]

    func testConversation() {
        Snapshot.assert(
            ChatStill(header: ChatHeaderState(), items: [
                .marcel(MarcelLine(id: "m1", body: ChatMarkdown.render(
                    "Good morning. Today: swim class **16:30**, paper bins tonight."))),
                .user(UserLine(id: "u1", text: "What's the weather Sunday?")),
                .marcel(MarcelLine(id: "m2", body: ChatMarkdown.render(
                    "18° and dry. Good for the garden lunch option. See [the forecast](https://example.com)."))),
            ]),
            named: "Chat-conversation", padding: 0)
    }

    func testCollapsedHeaderOffline() {
        Snapshot.assert(
            ChatStill(header: ChatHeaderState(offline: "Offline · showing what's saved"), items: [
                .user(UserLine(id: "u1", text: "Where's the dentist thing?")),
            ], collapsed: true),
            named: "Chat-offline", variants: twoModes, padding: 0)
    }
}
