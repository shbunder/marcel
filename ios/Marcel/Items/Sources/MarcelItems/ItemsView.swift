import MarcelKit
import PixelKit
import SwiftData
import SwiftUI

/// The Items tab (wireframe 4C, B-21): every item Marcel's quests made, grouped by project, the project with the
/// newest change first. Tapping a tile opens `ArtifactViewer`.
///
/// Everything comes from the cache, which the live event stream keeps current, so a new item or a new version shows
/// as soon as it arrives. Needs the app's `.modelContainer`.
public struct ItemsView: View {
    @Query private var artifacts: [CachedArtifact]
    @Query private var projects: [CachedProject]
    @State private var model: ItemsModel
    @State private var path: [String] = []
    @Environment(\.modelContext) private var context
    @Environment(\.colorScheme) private var scheme
    private let service: (any ItemsService)?
    private let conversationId: String?

    /// - Parameters:
    ///   - service: the hub; nil reads the paired hub from pairing's stores (address and Keychain token).
    ///   - conversationId: the main conversation, when the chat already knows it.
    public init(service: (any ItemsService)? = nil, conversationId: String? = nil) {
        _model = State(initialValue: ItemsModel(service: service))
        self.service = service
        self.conversationId = conversationId
    }

    public var body: some View {
        NavigationStack(path: $path) {
            TimelineView(.periodic(from: .now, by: 60)) { timeline in
                // Only the buddy this phone shows (B-49).
                let mine = CurrentBuddy.shared.rows(projects: projects, tasks: [], artifacts: artifacts)
                let input = ItemsInput.read(artifacts: mine.artifacts, projects: mine.projects)
                VStack(spacing: 0) {
                    ItemsHeader()
                    ScrollView {
                        ItemsContent(library: ItemsLibrary(artifacts: input.artifacts, projects: input.projects,
                                                           now: timeline.date),
                                     problem: model.problem) { tile in
                            Button { path.append(tile.id) } label: { ItemTileView(tile: tile) }
                                .buttonStyle(.plain)
                                .accessibilityHint("Opens the item")
                        }
                    }
                    .refreshable { await model.refresh() }
                }
            }
            .background(ChatPalette(scheme).well.color)
            .toolbar(.hidden, for: .navigationBar)
            .navigationDestination(for: String.self) { id in
                ArtifactViewer(artifactId: id, service: service, conversationId: conversationId)
            }
        }
        .onAppear {
            if model.cache == nil { model.cache = MarcelCache(container: context.container) }
            if model.service == nil { model.service = LiveItemsService.paired() }
        }
        .task { await model.refresh() }
    }
}

/// The top strip: "Items", grouped by project, on white.
struct ItemsHeader: View {
    @Environment(\.colorScheme) private var scheme

    var body: some View {
        ChatHeader("Items", spoken: "Items, by project") {
            PixelText("By project", role: .smallLabel)
                .foregroundStyle(ChatPalette(scheme).muted.color)
                .accessibilityHidden(true)
        }
    }
}

/// One item's tile (the wireframe's card): its name in bold, what it is and when it changed in small pixel capitals,
/// and the "updated" badge on a living item.
struct ItemTileView: View {
    var tile: ItemTile

    @Environment(\.colorScheme) private var scheme

    var body: some View {
        let theme = ChatPalette(scheme)
        VStack(alignment: .leading, spacing: PixelMetrics.steps(3)) {
            Text(tile.title).font(.body.weight(.bold))
                .foregroundStyle(theme.ink.color)
                .fixedSize(horizontal: false, vertical: true)
            PixelText(tile.detail, role: .smallLabel)
                .foregroundStyle(theme.muted.color)
                .fixedSize(horizontal: false, vertical: true)
            PixelText(tile.updated, role: .smallLabel)
                .foregroundStyle(theme.muted.color)
                .fixedSize(horizontal: false, vertical: true)
            if let badge = tile.badge {
                ChatBadge(badge, kind: .done)
            }
            Spacer(minLength: 0)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .frame(minHeight: PixelMetrics.minTapTarget)
        .padding(PixelMetrics.padding)
        .chatSurface()
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(tile.spoken)
    }
}

/// The tab's body: a problem line, then each project's name and its tiles in two columns (one at accessibility
/// text sizes), each row as tall as its tallest tile. The screen passes buttons; the snapshot the bare tiles.
struct ItemsContent<Tile: View>: View {
    var library: ItemsLibrary
    var problem: String?
    @ViewBuilder var tile: (ItemTile) -> Tile

    @Environment(\.colorScheme) private var scheme
    @Environment(\.dynamicTypeSize) private var dynamicType

    var body: some View {
        let theme = ChatPalette(scheme)
        let columns = dynamicType.isAccessibilitySize ? 1 : 2
        VStack(alignment: .leading, spacing: ItemsMetrics.gap) {
            if let problem {
                Text(problem).font(.footnote)
                    .foregroundStyle(theme.link.color)
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityLabel("Problem: \(problem)")
            }
            if library.isEmpty {
                Text("No items yet. When a quest makes something — a document, a pull request, a board — it "
                          + "shows here.").font(.body)
                    .foregroundStyle(theme.muted.color)
                    .fixedSize(horizontal: false, vertical: true)
            }
            ForEach(library.sections) { section in
                ChatSectionTitle(section.name)
                let rows = stride(from: 0, to: section.tiles.count, by: columns).map {
                    Array(section.tiles[$0..<min($0 + columns, section.tiles.count)])
                }
                Grid(horizontalSpacing: ItemsMetrics.gap, verticalSpacing: ItemsMetrics.gap) {
                    ForEach(rows.indices, id: \.self) { index in
                        GridRow {
                            ForEach(rows[index]) { tile($0) }
                            if rows[index].count < columns {
                                Color.clear.gridCellUnsizedAxes([.horizontal, .vertical])
                            }
                        }
                    }
                }
                // Each project's grid at its own height: the tiles in a row stretch to the tallest, and no taller.
                .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(ItemsMetrics.inset)
    }
}

/// The tab laid out in a plain stack, for the snapshot tests (`ImageRenderer` draws nothing in a `ScrollView`).
struct ItemsStill: View {
    var library: ItemsLibrary
    var problem: String?

    @Environment(\.colorScheme) private var scheme

    var body: some View {
        VStack(spacing: 0) {
            ItemsHeader()
            ItemsContent(library: library, problem: problem) { ItemTileView(tile: $0) }
        }
        .background(ChatPalette(scheme).well.color)
    }
}
