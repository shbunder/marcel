import SwiftUI

/// Every component on one screen, laid out like the wireframes' chat (1B, 3B, 4A). Used by the snapshot and
/// accessibility tests and handy as a debug screen.
public struct PixelGallery: View {
    @State private var tab = "chat"
    @Environment(\.colorScheme) private var scheme

    public init() {}

    public var body: some View {
        let theme = PixelTheme(scheme)
        VStack(spacing: 0) {
            HeartsHeader(title: "Marcel", subtitle: "Morning · nothing running", needsYou: 2) {
                PixelAvatarPlaceholder()
            }
            VStack(alignment: .leading, spacing: PixelMetrics.padding) {
                QuestCard(title: "Energy contracts", state: .working, project: "Home energy",
                          detail: "step 1 of 4", progress: 0.22)
                QuestCard(title: "Dentist booking", state: .needsYou, project: "Family", tone: .rose,
                          detail: "pick Tue 9:10 or Thu 8:30")
                DialogBox(speaker: "Marcel",
                          text: "Supplier A is cheapest: €486 a year less with solar, no exit fee. Switch now?",
                          showsMore: true) {
                    ChoicePrompt([.init("Yes"), .init("Not now"), .init("Details")])
                }
                ItemGetBanner(item: "Energy comparison", savedTo: "Home energy")
                ArtifactCard(title: "Energy comparison", badge: "Updated now") {
                    ArtifactRow("Supplier A + solar", value: "€1,010", emphasised: true)
                    ArtifactRow("Supplier A", value: "€1,284")
                    ArtifactRow("Current", value: "€1,496")
                }
                FlowRow {
                    StateBadge(.working)
                    StateBadge(.needsYou)
                    StateBadge(.ready)
                    StateBadge(.done)
                    StateBadge(.failed)
                    ProjectChip("Home energy", tone: .teal)
                    ProjectChip("Family", tone: .rose)
                    ProjectChip("Marcel dev", tone: .blue)
                    ProjectChip("Health", tone: .green)
                }
            }
            .padding(.horizontal, PixelMetrics.steps(7))
            .padding(.vertical, PixelMetrics.steps(6))
            PixelTabBar(selection: $tab)
        }
        .background(theme.background.color)
    }
}

/// Lays children out left to right and wraps to a new line when the row is full.
struct FlowRow: Layout {
    var spacing: CGFloat = PixelMetrics.gap

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let rows = arrange(width: proposal.width ?? .infinity, subviews: subviews)
        let width = rows.map { $0.width }.max() ?? 0
        let height = rows.map(\.height).reduce(0, +) + spacing * CGFloat(max(rows.count - 1, 0))
        return CGSize(width: proposal.width ?? width, height: height)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        var y = bounds.minY
        for row in arrange(width: bounds.width, subviews: subviews) {
            var x = bounds.minX
            for index in row.items {
                let size = subviews[index].sizeThatFits(.unspecified)
                subviews[index].place(at: CGPoint(x: x, y: y + (row.height - size.height) / 2), proposal: .unspecified)
                x += size.width + spacing
            }
            y += row.height + spacing
        }
    }

    private struct Row { var items: [Int] = []; var width: CGFloat = 0; var height: CGFloat = 0 }

    private func arrange(width: CGFloat, subviews: Subviews) -> [Row] {
        var rows: [Row] = [Row()]
        for index in subviews.indices {
            let size = subviews[index].sizeThatFits(.unspecified)
            let extra = rows[rows.count - 1].items.isEmpty ? size.width : size.width + spacing
            if rows[rows.count - 1].width + extra > width, !rows[rows.count - 1].items.isEmpty {
                rows.append(Row())
            }
            let added = rows[rows.count - 1].items.isEmpty ? size.width : size.width + spacing
            rows[rows.count - 1].items.append(index)
            rows[rows.count - 1].width += added
            rows[rows.count - 1].height = max(rows[rows.count - 1].height, size.height)
        }
        return rows
    }
}

/// A stand-in for AvatarKit's sprite in previews and the gallery: a small pixel giraffe head.
struct PixelAvatarPlaceholder: View {
    @Environment(\.colorScheme) private var scheme

    private static let rows = [
        "..I.....I..",
        "..R.....R..",
        ".RRRRRRRRR.",
        "RRRRRRRRRRR",
        "RRIRRRRRIRR",
        "RRRRRRRRRRR",
        ".RRRDDDRRR.",
        "..RDDDDDR..",
        "...RRRRR...",
        "...RRRRR...",
        "...RRDRR...",
        "...RRRRR...",
    ]

    var body: some View {
        let theme = PixelTheme(scheme)
        let pixel: CGFloat = 4
        Canvas { context, _ in
            for (y, row) in Self.rows.enumerated() {
                for (x, cell) in row.enumerated() {
                    let color: PixelColor? = switch cell {
                    case "R": .rose
                    case "D": .deep
                    case "I": theme.text
                    default: nil
                    }
                    guard let color else { continue }
                    let rect = CGRect(x: CGFloat(x) * pixel, y: CGFloat(y) * pixel, width: pixel, height: pixel)
                    context.fill(Path(rect), with: .color(color.color))
                }
            }
        }
        .frame(width: 44, height: 48)
        .accessibilityHidden(true)
    }
}

/// Shows a preview in the light and the dark theme side by side.
struct PixelPreview<Content: View>: View {
    var content: (ColorScheme) -> Content

    init(@ViewBuilder content: @escaping (ColorScheme) -> Content) {
        self.content = content
    }

    var body: some View {
        HStack(spacing: 0) {
            ForEach([ColorScheme.light, .dark], id: \.self) { scheme in
                content(scheme)
                    .padding(16)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    .background(PixelTheme(scheme).background.color)
                    .environment(\.colorScheme, scheme)
            }
        }
    }
}

#Preview("Gallery") {
    ScrollView { PixelGallery() }
}
