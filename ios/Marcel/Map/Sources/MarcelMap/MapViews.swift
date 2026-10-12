import MarcelItems
import PixelKit
import SwiftUI

/// The top strip (wireframe 5A): "Map · projects", on the bar colour with a heavy line under it.
struct MapHeader: View {
    @Environment(\.colorScheme) private var scheme

    var body: some View {
        let theme = PixelTheme(scheme)
        PixelText("Map · projects", role: .title)
            .foregroundStyle(theme.text.color)
            .accessibilityLabel("Map of your projects")
            .accessibilityAddTraits(.isHeader)
            .padding(.horizontal, PixelMetrics.padding + PixelMetrics.steps(1))
            .padding(.vertical, PixelMetrics.steps(5))
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(theme.bar.color)
            .overlay(alignment: .bottom) {
                Rectangle().fill(theme.line.color).frame(height: PixelMetrics.thickBorder)
            }
    }
}

/// One project on the map: its name, a "needs you" badge, what is going on in it, when its board last changed, and
/// its Claude Code link. VoiceOver reads it as one sentence in full words.
struct TileView: View {
    var tile: ProjectTile

    @Environment(\.colorScheme) private var scheme

    var body: some View {
        let theme = PixelTheme(scheme)
        VStack(alignment: .leading, spacing: PixelMetrics.steps(3)) {
            PixelText(tile.name, role: .label)
                .foregroundStyle(theme.text.color)
                .fixedSize(horizontal: false, vertical: true)
            PixelText(tile.detail, role: .detail)
                .foregroundStyle(theme.muted.color)
                .fixedSize(horizontal: false, vertical: true)
            PixelText(tile.updated, role: .detail)
                .foregroundStyle(theme.muted.color)
                .fixedSize(horizontal: false, vertical: true)
            if let needsYou = tile.needsYou {
                StateBadge(.needsYou, text: needsYou)
            }
            if tile.claudeProject != nil {
                ProjectChip("Claude Code →", tone: .blue)
            }
            Spacer(minLength: 0)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .padding(PixelMetrics.padding)
        .pixelBox(fill: theme.card, line: theme.line, shadow: theme.shadow)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(tile.spoken)
    }
}

/// "+ New": a dashed tile. Marcel makes projects, so it opens the chat with the request started.
struct NewTile: View {
    @Environment(\.colorScheme) private var scheme
    @Environment(\.agentName) private var agentName

    var body: some View {
        let theme = PixelTheme(scheme)
        VStack(alignment: .leading, spacing: PixelMetrics.steps(3)) {
            PixelText("+ New", role: .label)
                .foregroundStyle(theme.text.color)
            PixelText("or just ask \(agentName)", role: .detail)
                .foregroundStyle(theme.muted.color)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 0)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .frame(minHeight: PixelMetrics.minTapTarget)
        .padding(PixelMetrics.padding)
        .overlay(
            Rectangle().strokeBorder(theme.line.color, style: StrokeStyle(lineWidth: PixelMetrics.thickBorder,
                                                                          dash: [PixelMetrics.steps(3)])))
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("New project")
        .accessibilityHint("Opens the chat to ask \(agentName) for one")
    }
}

/// The tiles in two columns (one at accessibility text sizes), each row as tall as its tallest tile.
struct TileGrid<Tile: View, New: View>: View {
    var tiles: [ProjectTile]
    var tile: (ProjectTile) -> Tile
    var new: New

    init(tiles: [ProjectTile], @ViewBuilder tile: @escaping (ProjectTile) -> Tile, @ViewBuilder new: () -> New) {
        self.tiles = tiles
        self.tile = tile
        self.new = new()
    }

    @Environment(\.dynamicTypeSize) private var dynamicType

    var body: some View {
        let columns = dynamicType.isAccessibilitySize ? 1 : 2
        // The "+ new" tile is the last cell.
        let cells = tiles.map(Optional.some) + [nil]
        let rows = stride(from: 0, to: cells.count, by: columns).map { Array(cells[$0..<min($0 + columns, cells.count)]) }
        Grid(horizontalSpacing: MapMetrics.gap, verticalSpacing: MapMetrics.gap) {
            ForEach(rows.indices, id: \.self) { index in
                GridRow {
                    ForEach(rows[index].indices, id: \.self) { column in
                        if let project = rows[index][column] {
                            tile(project)
                        } else {
                            new
                        }
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
        PixelText("No projects yet. \(agentName) files every thread in one; ask to start one.", role: .body)
            .foregroundStyle(PixelTheme(scheme).muted.color)
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
        let theme = PixelTheme(scheme)
        Button(action: dismiss) {
            PixelText(text, role: .detail)
                .foregroundStyle(theme.text.color)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, minHeight: PixelMetrics.minTapTarget, alignment: .leading)
                .padding(.horizontal, PixelMetrics.padding)
                .pixelBox(fill: theme.dialog, line: theme.line, shadow: nil, border: PixelMetrics.thinBorder)
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
            MapContent(map: map, problem: problem, dismiss: {}, tile: { TileView(tile: $0) }, new: { NewTile() })
        }
        .background(PixelTheme(scheme).background.color)
    }
}

/// The map's body: a problem line, the tiles and "+ new". The screen passes buttons; the snapshot the bare tiles.
struct MapContent<Tile: View, New: View>: View {
    var map: ProjectMap
    var problem: String?
    var dismiss: () -> Void
    @ViewBuilder var tile: (ProjectTile) -> Tile
    @ViewBuilder var new: () -> New

    var body: some View {
        VStack(alignment: .leading, spacing: MapMetrics.gap) {
            if let problem { ProblemLine(text: problem, dismiss: dismiss) }
            if map.isEmpty { EmptyMap() }
            TileGrid(tiles: map.tiles, tile: tile, new: new)
        }
        .padding(MapMetrics.inset)
    }
}

enum MapMetrics {
    static let inset = EdgeInsets(top: PixelMetrics.steps(5), leading: 12, bottom: 16, trailing: 12)
    static let gap: CGFloat = PixelMetrics.steps(5)
}
