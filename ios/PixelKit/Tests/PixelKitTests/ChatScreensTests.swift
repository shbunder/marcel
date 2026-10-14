import SwiftUI
import XCTest
@testable import PixelKit

/// The pieces the other tabs use to speak the chat's language (S-18.6): their colours reach 4.5:1, the tracker button
/// shows only for a web address, the composer sends only real text, and how they look (light, dark, largest text).
@MainActor
final class ChatScreensTests: XCTestCase {
    func testEveryTextPairReachesFourAndAHalfToOne() {
        for palette in [ChatPalette.light, ChatPalette.dark] {
            for pair in palette.screenTextPairs {
                XCTAssertGreaterThanOrEqual(pair.text.contrast(with: pair.on), 4.5,
                                            "\(pair.name) in \(palette == .dark ? "dark" : "light")")
            }
        }
    }

    func testTheTrackerShowsOnlyForAWebAddress() {
        XCTAssertEqual(TrackerButton.url("https://claude.ai/artifact/abc")?.host, "claude.ai")
        XCTAssertNotNil(TrackerButton.url("http://nuc.local/tracker"))
        for nothing in [nil, "", "not a url", "javascript:alert(1)", "file:///etc/passwd", "marcel://project/x",
                        "https://"] {
            XCTAssertNil(TrackerButton.url(nothing), "\(nothing ?? "nil") should show no tracker")
        }
    }

    func testTheComposerSendsOnlyRealText() {
        func field(_ text: String) -> ChatComposerField<EmptyView> {
            ChatComposerField(text: .constant(text), placeholder: "Tell Marcel…", fieldLabel: "Message", send: {})
        }
        XCTAssertFalse(field("").canSend)
        XCTAssertFalse(field("  \n ").canSend)
        XCTAssertTrue(field("Also check Safari").canSend)
    }

    func testThePieces() {
        Snapshot.assert(
            VStack(spacing: 0) {
                ChatHeader("Projects") {
                    PixelText("+ NEW", role: .smallLabel)
                }
                ChatHeader("Recipe app", subtitle: "Cook from what's in the fridge", back: {})
                VStack(alignment: .leading, spacing: ChatScreenMetrics.gap) {
                    ChatSectionTitle("QUESTS")
                    Text("A plain card").padding(ChatScreenMetrics.padding)
                        .frame(maxWidth: .infinity, alignment: .leading).chatSurface()
                    Text("Needs you").padding(ChatScreenMetrics.padding)
                        .frame(maxWidth: .infinity, alignment: .leading).chatSurface(.framed(.needsYou))
                    Text("Working").padding(ChatScreenMetrics.padding)
                        .frame(maxWidth: .infinity, alignment: .leading).chatSurface(.framed(.started))
                    Text("+ New").padding(ChatScreenMetrics.padding)
                        .frame(maxWidth: .infinity, alignment: .leading).chatSurface(.dashed)
                    HStack {
                        ChatBadge("1 NEEDS YOU")
                        ChatBadge("UPDATED", kind: .done)
                    }
                    FlowRow {
                        TrackerButton(url: URL(string: "https://claude.ai/artifact/abc")!)
                        ChatPillButton("Ask about this", kind: .started) {}
                    }
                }
                .padding(ChatScreenMetrics.content)
                ChatComposerField(text: .constant(""), placeholder: "Tell Marcel about Recipe app…",
                                  fieldLabel: "Message", still: true, send: {})
            },
            named: "ChatScreens", padding: 0)
    }
}
