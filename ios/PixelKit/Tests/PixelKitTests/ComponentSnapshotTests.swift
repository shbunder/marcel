import SwiftUI
import XCTest
@testable import PixelKit

/// Every component in light, dark and the largest Dynamic Type size (Accessibility 5), compared with the reference
/// PNGs in `__Snapshots__/`. Changing how a component looks makes these fail until the images are re-recorded.
@MainActor
final class ComponentSnapshotTests: XCTestCase {
    func testDialogBox() {
        Snapshot.assert(
            DialogBox(speaker: "Marcel",
                      text: "Good morning. Today: swim class 16:30, paper bins tonight. Two things wait for you.",
                      showsMore: true),
            named: "DialogBox")
    }

    func testChoicePrompt() {
        Snapshot.assert(
            DialogBox(speaker: "Marcel",
                      text: "Supplier A is cheapest: €486 a year less with solar, no exit fee. Switch now?") {
                ChoicePrompt([.init("Yes"), .init("Not now"), .init("Details")])
            },
            named: "ChoicePrompt")
    }

    func testChoicePromptAnswered() {
        Snapshot.assert(
            ChoicePrompt([.init("Tue 9:10"), .init("Thu 8:30")], marked: "Thu 8:30").disabled(true),
            named: "ChoicePrompt-answered")
    }

    func testQuestCard() {
        Snapshot.assert(
            VStack(spacing: 10) {
                QuestCard(title: "Energy contracts", state: .working, project: "Home energy",
                          detail: "step 1 of 4", progress: 0.22)
                QuestCard(title: "Dentist booking", state: .needsYou, project: "Family", tone: .rose,
                          detail: "pick Tue 9:10 or Thu 8:30")
                QuestCard(title: "Avatar sprite in app", state: .ready, project: "Marcel dev", tone: .blue,
                          detail: "PR #41 · CI green")
            },
            named: "QuestCard")
    }

    func testArtifactCard() {
        Snapshot.assert(
            ArtifactCard(title: "Energy comparison", badge: "Updated now") {
                ArtifactRow("Supplier A + solar", value: "€1,010", emphasised: true)
                ArtifactRow("Supplier A", value: "€1,284")
                ArtifactRow("Current", value: "€1,496")
            },
            named: "ArtifactCard")
    }

    func testItemGetBanner() {
        Snapshot.assert(ItemGetBanner(item: "Energy comparison", savedTo: "Home energy"), named: "ItemGetBanner")
    }

    func testProjectChip() {
        Snapshot.assert(
            VStack(alignment: .leading, spacing: 8) {
                ProjectChip("Home energy", tone: .teal)
                ProjectChip("Family", tone: .rose)
                ProjectChip("Marcel dev", tone: .blue)
                ProjectChip("Health", tone: .green)
                ProjectChip("Garden", tone: .gold)
            }
            .frame(maxWidth: .infinity, alignment: .leading),
            named: "ProjectChip")
    }

    func testStateBadge() {
        Snapshot.assert(
            FlowRow { ForEach(StateBadge.Kind.allCases, id: \.self) { StateBadge($0) } },
            named: "StateBadge")
    }

    func testPixelTabBar() {
        Snapshot.assert(PixelTabBar(selection: .constant("quests")), named: "PixelTabBar", padding: 0)
    }

    func testHeartsHeader() {
        Snapshot.assert(
            HeartsHeader(title: "Marcel", subtitle: "Morning · nothing running", needsYou: 2) {
                PixelAvatarPlaceholder()
            },
            named: "HeartsHeader", padding: 0)
    }

    func testGallery() {
        Snapshot.assert(PixelGallery(), named: "Gallery", padding: 0)
    }
}

/// Real Dutch and French phrases, with every character that needs a fallback, in body text and in labels. Look at
/// `__Snapshots__/SampleSheet.*.png` after a font change: every phrase must read clearly.
@MainActor
final class SampleSheetSnapshotTests: XCTestCase {
    static let phrases = ["€486", "−€486/jaar", "l’été", "één", "Ça va", "Taak ✓", "Klaar ✔", "→ volgende",
                          "← terug", "ĳs", "« Oui » „ja” “yes” — 18° …", "Brûlé, naïef, œuvre"]

    func testSampleSheet() {
        Snapshot.assert(
            VStack(alignment: .leading, spacing: 6) {
                PixelText("Body", role: .smallLabel, capitals: true)
                ForEach(Self.phrases, id: \.self) { PixelText($0, role: .body) }
                PixelText("Labels", role: .smallLabel)
                ForEach(Self.phrases, id: \.self) { PixelText($0, role: .label) }
            }
            .frame(maxWidth: .infinity, alignment: .leading),
            named: "SampleSheet")
    }
}
