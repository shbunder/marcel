import MarcelKit
import PixelKit
import SwiftData
import SwiftUI

/// One item, whatever its kind (wireframe 4B): Markdown natively, HTML in a locked-down web view, files in
/// QuickLook, pull requests and links as cards, dashboards and boards drawn from their JSON. A version line on top,
/// the history behind a chip, and "Ask about this" at the bottom.
///
/// The item comes from the cache (`.modelContainer` must be set), which the live event stream keeps current: an
/// `artifact.updated` changes the version on screen and the viewer loads the new bytes. The Chat, Map and Items tabs
/// open it; the back button dismisses it however it was shown.
public struct ArtifactViewer: View {
    let artifactId: String
    @Query private var rows: [CachedArtifact]
    @State private var model: ItemViewerModel
    /// The version on screen; nil is the current one, which follows new versions as they arrive.
    @State private var shown: Int?
    @State private var showingHistory = false
    @Environment(\.modelContext) private var context
    @Environment(\.dismiss) private var dismiss
    @Environment(\.colorScheme) private var scheme

    /// - Parameters:
    ///   - service: the hub; nil reads the paired hub from pairing's stores (address and Keychain token).
    ///   - conversationId: the main conversation, when the caller already knows it ("Ask about this" posts there).
    public init(artifactId: String, service: (any ItemsService)? = nil, conversationId: String? = nil) {
        self.artifactId = artifactId
        _rows = Query(filter: #Predicate<CachedArtifact> { $0.id == artifactId })
        _model = State(initialValue: ItemViewerModel(service: service, conversationId: conversationId))
    }

    public var body: some View {
        // "Updated 2 min ago" moves on once a minute.
        TimelineView(.periodic(from: .now, by: 60)) { timeline in
            if let artifact = ItemsInput.artifact(rows) {
                screen(artifact, now: timeline.date)
                    .task(id: ItemViewerModel.key(artifact.id, shown ?? artifact.currentVersion)) {
                        await model.load(artifact, version: shown)
                    }
                    .task(id: ItemViewerModel.key(artifact.id, artifact.currentVersion)) {
                        await model.loadVersions(artifact)
                    }
            } else {
                missing
            }
        }
        .background(PixelTheme(scheme).background.color)
        .toolbar(.hidden, for: .navigationBar)
        .onAppear {
            if model.cache == nil { model.cache = MarcelCache(container: context.container) }
            if model.service == nil { model.service = LiveItemsService.paired() }
        }
        .task { if rows.isEmpty { await model.fetch(id: artifactId) } }
    }

    private func screen(_ artifact: Artifact, now: Date) -> some View {
        @Bindable var model = model
        let versions = model.history(artifact)
        let body = model.body(artifact, version: shown)
        let historyCount = versions.isEmpty ? (artifact.currentVersion > 1 ? artifact.currentVersion : nil)
            : versions.count
        return VStack(spacing: 0) {
            ViewerHeader(title: artifact.title, line: VersionLine(artifact, showing: shown, versions: versions,
                                                                   now: now),
                         back: { dismiss() })
            content(body, artifact: artifact)
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
            ViewerActions(
                draft: $model.draft, asking: model.asking, sending: model.sending, sent: model.sent,
                problem: model.problem, historyCount: historyCount, showingOld: shown != nil,
                toggleAsk: { model.asking.toggle() },
                send: { Task { await model.ask(about: artifact, version: shown) } },
                history: { showingHistory = true },
                latest: { shown = nil })
        }
        .sheet(isPresented: $showingHistory) {
            ScrollView {
                HistoryList(rows: versions.map { VersionRow($0, current: artifact.currentVersion, now: now) },
                            state: model.versions[ItemViewerModel.key(artifact.id, artifact.currentVersion)],
                            shown: shown ?? artifact.currentVersion) { version in
                    shown = version == artifact.currentVersion ? nil : version
                    showingHistory = false
                }
                .padding(ItemsMetrics.inset)
            }
            .background(PixelTheme(scheme).background.color)
            .presentationDetents([.medium, .large])
        }
    }

    @ViewBuilder private func content(_ body: ItemBody, artifact: Artifact) -> some View {
        switch body {
        case .html(let html, let trusted):
            VStack(spacing: PixelMetrics.steps(3)) {
                ScriptsLine(trusted: trusted)
                    .padding(.horizontal, ItemsMetrics.inset.leading)
                    .padding(.top, PixelMetrics.steps(3))
                HTMLView(html: html, trusted: trusted)
            }
        case .file(let file):
            FileItemView(file: file, version: shown ?? artifact.currentVersion)
        default:
            ScrollView {
                NativeBody(body_: body, title: artifact.title)
                    .padding(ItemsMetrics.inset)
            }
            .refreshable { await model.fetch(id: artifact.id) }
        }
    }

    private var missing: some View {
        let theme = PixelTheme(scheme)
        return VStack(spacing: 0) {
            ViewerHeader(title: "Item", line: nil, back: { dismiss() })
            PixelText(model.problem ?? "Opening the item…", role: .body)
                .foregroundStyle(theme.muted.color)
                .fixedSize(horizontal: false, vertical: true)
                .padding(ItemsMetrics.inset)
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
        }
    }
}
