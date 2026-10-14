import MarcelKit
import Observation
import PixelKit
import SwiftUI

/// The system status screen: is the hub up, can it reach the NUC's runner, is Marcel's brain running (`GET /health`),
/// and the last alert this phone saw. `marcel://status` (a `broke` push about no one task) opens it, and so does
/// "System status" in Settings.
@MainActor
@Observable
public final class StatusModel {
    public private(set) var health: Loadable<Health> = .loading
    /// The push that opened the screen, else the last `broke` push this phone kept.
    public let alert: PushAlert?

    @ObservationIgnored let service: any SettingsService

    public init(service: any SettingsService, alert: PushAlert? = nil, alerts: LastAlertStore = LastAlertStore()) {
        self.service = service
        self.alert = alert ?? alerts.load()
    }

    public func load() async {
        do {
            health = .loaded(try await service.health())
        } catch {
            // `/health` needs no token: failing to reach it means the hub (or the tunnel) is down.
            health = .failed((error as? SettingsError) == .offline
                ? "Couldn't reach the hub. It may be down, or this phone is offline." : SettingsError.words(for: error))
        }
    }
}

public struct StatusView: View {
    @State private var model: StatusModel
    @Environment(\.dismiss) private var dismiss
    @Environment(\.colorScheme) private var scheme

    public init(model: StatusModel) {
        _model = State(initialValue: model)
    }

    public var body: some View {
        VStack(spacing: 0) {
            SettingsHeader(title: "System status", back: { dismiss() })
            ScrollView {
                StatusContent(model: model, text: SettingsText()).padding(PixelMetrics.padding)
            }
            .refreshable { await model.load() }
        }
        .background(ChatPalette(scheme).well.color)
        .toolbar(.hidden, for: .navigationBar)
        .task { await model.load() }
    }
}

struct StatusContent: View {
    var model: StatusModel
    var text: SettingsText

    @Environment(\.colorScheme) private var scheme

    var body: some View {
        let theme = ChatPalette(scheme)
        VStack(alignment: .leading, spacing: PixelMetrics.steps(5)) {
            SettingsCard("Now") {
                switch model.health {
                case .loading: SettingsNote(text: "Asking the hub…")
                case .notAvailableYet: SettingsNote(text: "Not available yet.")
                case .failed(let words), .unavailable(let words): SettingsNote(text: words, accent: true)
                case .loaded(let health):
                    ForEach(text.statusLines(health), id: \.label) { line in
                        HStack(alignment: .firstTextBaseline, spacing: PixelMetrics.steps(4)) {
                            StateBadge(line.ok ? .done : .failed, text: line.ok ? "OK" : "Problem")
                            SettingsRow(line.label, detail: line.value)
                        }
                        .accessibilityElement(children: .combine)
                    }
                }
            }
            SettingsCard("Last alert") {
                if let alert = model.alert {
                    SettingsRow(alert.title, detail: alert.body)
                    Text(text.when(alert.at)).font(.footnote).foregroundStyle(theme.muted.color)
                } else {
                    SettingsNote(text: "No alerts on this phone.")
                }
            }
        }
    }
}
