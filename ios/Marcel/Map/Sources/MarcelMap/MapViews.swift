import MarcelItems
import PixelKit
import SwiftUI

/// The top strip (wireframe Q2): "Projects" in bold and "+ New" on the right, on a white strip with a thin line under
/// it. Marcel makes projects, so "+ New" opens the chat with the request started; nil in snapshots.
struct MapHeader: View {
    var newProject: (() -> Void)?

    @Environment(\.colorScheme) private var scheme
    @Environment(\.agentName) private var agentName

    var body: some View {
        ChatHeader("Projects", spoken: "Map of your projects") {
            Button { newProject?() } label: {
                Text("+ New")
                    .font(.subheadline)
                    .foregroundStyle(ChatPalette(scheme).muted.color)
                    .frame(minWidth: PixelMetrics.minTapTarget, minHeight: PixelMetrics.minTapTarget)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel("New project")
            .accessibilityHint("Opens the chat to ask \(agentName) for one")
        }
    }
}

/// One project on the map (wireframe Q2 v10): a white rounded tile, framed in gold with a gold badge when something
/// waits on you; its name, what is going on in it, when its board last changed, its Claude Code link, and "Open
/// tracker" when it has a tracker (B-46). VoiceOver reads the tile as one sentence in full words; the tracker is a
/// button of its own.
struct TileView: View {
    var tile: ProjectTile
    /// Opens the project's room. The summary VoiceOver lands on is the button for it; nil in snapshots.
    var open: (() -> Void)? = nil

    @Environment(\.colorScheme) private var scheme

    var body: some View {
        let palette = ChatPalette(scheme)
        VStack(alignment: .leading, spacing: 8) {
            VStack(alignment: .leading, spacing: 5) {
                if let needsYou = tile.needsYou {
                    ChatBadge(needsYou)
                }
                Text(tile.name)
                    .font(.body.weight(.bold))
                    .foregroundStyle(palette.ink.color)
                    .fixedSize(horizontal: false, vertical: true)
                PixelText(tile.detail, role: .smallLabel)
                    .foregroundStyle(palette.muted.color)
                    .fixedSize(horizontal: false, vertical: true)
                PixelText(tile.updated, role: .smallLabel)
                    .foregroundStyle(palette.muted.color)
                    .fixedSize(horizontal: false, vertical: true)
                if tile.claudeProject != nil {
                    Text("Claude Code →")
                        .font(.footnote.weight(.semibold))
                        .foregroundStyle(palette.link.color)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(tile.spoken)
            .accessibilityAddTraits(.isButton)
            .accessibilityHint("Opens the project room")
            .accessibilityAction { open?() }
            Spacer(minLength: 0)
            if let tracker = tile.tracker {
                TrackerButton(url: tracker)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .padding(ChatScreenMetrics.padding)
        .chatSurface(tile.needsYou == nil ? .plain : .framed(.needsYou))
    }
}

/// The tiles in two columns (one at accessibility text sizes), each row as tall as its tallest tile.
struct TileGrid<Tile: View>: View {
    var tiles: [ProjectTile]
    var tile: (ProjectTile) -> Tile

    @Environment(\.dynamicTypeSize) private var dynamicType

    var body: some View {
        let columns = dynamicType.isAccessibilitySize ? 1 : 2
        let rows = stride(from: 0, to: tiles.count, by: columns).map { Array(tiles[$0..<min($0 + columns, tiles.count)]) }
        Grid(horizontalSpacing: MapMetrics.gap, verticalSpacing: MapMetrics.gap) {
            ForEach(rows.indices, id: \.self) { index in
                GridRow {
                    ForEach(rows[index].indices, id: \.self) { column in
                        tile(rows[index][column])
                    }
                    if rows[index].count < columns {
                        Color.clear.gridCellUnsizedAxes([.horizontal, .vertical])
                    }
                }
            }
        }
    }
}

/// What an empty map says.
struct EmptyMap: View {
    @Environment(\.colorScheme) private var scheme
    @Environment(\.agentName) private var agentName

    var body: some View {
        Text("No projects yet. \(agentName) files every thread in one; ask to start one.")
            .font(.body)
            .foregroundStyle(ChatPalette(scheme).muted.color)
            .fixedSize(horizontal: false, vertical: true)
            .frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// A problem from the last action, in Marcel's words, with a way to dismiss it.
struct ProblemLine: View {
    var text: String
    var dismiss: () -> Void

    @Environment(\.colorScheme) private var scheme

    var body: some View {
        let palette = ChatPalette(scheme)
        Button(action: dismiss) {
            Text(text)
                .font(.subheadline)
                .foregroundStyle(palette.ink.color)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, minHeight: PixelMetrics.minTapTarget, alignment: .leading)
                .padding(.horizontal, 12)
                .chatSurface(.framed(.failed))
        }
        .buttonStyle(.plain)
        .accessibilityLabel(text)
        .accessibilityHint("Dismisses this message")
    }
}

/// The map laid out in a plain stack: what the scroll view shows, without the scroll view. `ImageRenderer` draws
/// nothing inside a `ScrollView`, so the snapshot tests render this; the screen uses the same pieces.
struct MapStill: View {
    var map: ProjectMap
    var problem: String?

    @Environment(\.colorScheme) private var scheme

    var body: some View {
        VStack(spacing: 0) {
            MapHeader()
            MapContent(map: map, problem: problem, dismiss: {}, tile: { TileView(tile: $0) })
        }
        .background(ChatPalette(scheme).well.color)
    }
}

/// The map's body: a problem line and the tiles. The screen passes buttons; the snapshot the bare tiles.
struct MapContent<Tile: View>: View {
    var map: ProjectMap
    var problem: String?
    var dismiss: () -> Void
    @ViewBuilder var tile: (ProjectTile) -> Tile

    var body: some View {
        VStack(alignment: .leading, spacing: MapMetrics.gap) {
            if let problem { ProblemLine(text: problem, dismiss: dismiss) }
            if map.isEmpty { EmptyMap() }
            TileGrid(tiles: map.tiles, tile: tile)
        }
        .padding(MapMetrics.inset)
    }
}

enum MapMetrics {
    static let inset = ChatScreenMetrics.content
    static let gap: CGFloat = ChatScreenMetrics.gap
}
