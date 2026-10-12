import MarcelKit
import PixelKit
import SwiftUI
import UIKit

/// Settings (B-22, B-18, B-19): the agent's name, where notifications go, plan usage, schedules (pause, delete),
/// devices (revoke, unpair this phone) and the system status screen. Opened from the avatar header in the chat.
public struct SettingsView: View {
    @State private var model: SettingsModel
    private let statusModel: () -> StatusModel
    private let unpair: () -> Void

    @State private var showingStatus = false
    @Environment(\.dismiss) private var dismiss
    @Environment(\.colorScheme) private var scheme

    /// - Parameters:
    ///   - status: makes the status screen's model when "System status" is tapped.
    ///   - unpair: forgets the hub on this phone (after a confirmation).
    public init(model: SettingsModel, status: @escaping () -> StatusModel, unpair: @escaping () -> Void) {
        _model = State(initialValue: model)
        self.statusModel = status
        self.unpair = unpair
    }

    public var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                SettingsHeader(title: "Settings", back: { dismiss() })
                ScrollView {
                    SettingsContent(model: model, text: SettingsText(), actions: actions)
                        .padding(PixelMetrics.padding)
                }
                .refreshable { await model.load() }
            }
            .background(PixelTheme(scheme).background.color)
            .toolbar(.hidden, for: .navigationBar)
            .navigationDestination(isPresented: $showingStatus) {
                StatusView(model: statusModel())
            }
        }
        .task { await model.load() }
    }

    private var actions: SettingsActions {
        var actions = SettingsActions()
        actions.saveName = { Task { await model.saveName() } }
        actions.turnOnNotifications = { Task { await model.turnOnNotifications() } }
        actions.copyTopic = { UIPasteboard.general.string = $0 }
        actions.setPaused = { schedule, paused in Task { await model.setPaused(schedule, paused) } }
        actions.delete = { schedule in Task { await model.delete(schedule) } }
        actions.revoke = { device in Task { await model.revoke(device) } }
        actions.unpair = unpair
        actions.openStatus = { showingStatus = true }
        return actions
    }
}

/// What the buttons in Settings do. Snapshots pass the empty one.
struct SettingsActions {
    var saveName: () -> Void = {}
    var turnOnNotifications: () -> Void = {}
    var copyTopic: (String) -> Void = { _ in }
    var setPaused: (Schedule, Bool) -> Void = { _, _ in }
    var delete: (Schedule) -> Void = { _ in }
    var revoke: (Device) -> Void = { _ in }
    var unpair: () -> Void = {}
    var openStatus: () -> Void = {}
}

/// Every section, top to bottom. The live screen puts it in a scroll view; snapshots draw it as it is.
struct SettingsContent: View {
    @Bindable var model: SettingsModel
    var text: SettingsText
    var actions: SettingsActions

    @State private var confirming: Confirm?
    @Environment(\.settingsStill) private var still

    /// What a destructive button asks first.
    enum Confirm: Identifiable {
        case delete(Schedule)
        case revoke(Device)
        case unpair

        var id: String {
            switch self {
            case .delete(let schedule): "schedule-\(schedule.id)"
            case .revoke(let device): "device-\(device.id)"
            case .unpair: "unpair"
            }
        }

        var title: String {
            switch self {
            case .delete(let schedule): "Delete “\(schedule.name)”? It won't run again."
            case .revoke(let device): "Remove “\(device.name)”? It stops reaching Marcel at once."
            case .unpair: "Unpair this phone? It forgets the hub and everything saved from it."
            }
        }

        var button: String {
            switch self {
            case .delete: "Delete"
            case .revoke: "Remove"
            case .unpair: "Unpair"
            }
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: PixelMetrics.steps(7)) {
            if let problem = model.problem {
                ProblemBanner(text: problem) { model.problem = nil }
            }
            agentSection
            notificationsSection
            usageSection
            schedulesSection
            devicesSection
            SettingsCard("System") {
                SettingsRow("System status", detail: "The hub, the NUC and the last alert") {
                    SettingsButton(title: "Open", action: actions.openStatus)
                }
            }
        }
        .confirmationDialog(confirming?.title ?? "", isPresented: Binding(
            get: { confirming != nil }, set: { if !$0 { confirming = nil } }
        ), titleVisibility: .visible, presenting: confirming) { confirm in
            Button(confirm.button, role: .destructive) {
                switch confirm {
                case .delete(let schedule): actions.delete(schedule)
                case .revoke(let device): actions.revoke(device)
                case .unpair: actions.unpair()
                }
            }
            Button("Cancel", role: .cancel) {}
        }
    }

    // MARK: Sections

    private var agentSection: some View {
        SettingsCard("Name") {
            if still {
                PixelText(model.name, role: .body, capitals: false)
                    .padding(PixelMetrics.steps(4))
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .modifier(FieldBox())
            } else {
                TextField("Marcel", text: $model.name)
                    .pixelText(.body)
                    .textInputAutocapitalization(.words)
                    .submitLabel(.done)
                    .onSubmit(actions.saveName)
                    .padding(PixelMetrics.steps(4))
                    .modifier(FieldBox())
                    .accessibilityLabel("Agent name")
            }
            if let agent = model.agent {
                SettingsRow("Thinks with \(agent.modelBrain), works with \(agent.modelWorker)") {
                    SettingsButton(title: "Save", enabled: model.nameToSave != nil, action: actions.saveName)
                }
            } else {
                SettingsNote(text: "Loading…")
            }
        }
    }

    @ViewBuilder private var notificationsSection: some View {
        SettingsCard("Notifications") {
            switch model.push {
            case .off:
                SettingsRow("Off on this phone", detail: "Marcel can't reach you when something needs you.") {
                    SettingsButton(title: "Turn on", enabled: model.me != nil, action: actions.turnOnNotifications)
                }
            case .apns:
                SettingsRow("On", detail: "Through Apple's push service.")
            case .ntfy(let topic, let server):
                SettingsRow("On, through ntfy", detail: "Notifications arrive in the ntfy app.")
                ForEach(Array(SettingsModel.ntfySteps(topic: topic, server: server).enumerated()), id: \.offset) {
                    SettingsNote(text: "\($0.offset + 1). \($0.element)")
                }
                // The topic is case-sensitive: drawn in the body face, never in the all-capitals label face.
                HStack(spacing: PixelMetrics.steps(4)) {
                    PixelText(topic, role: .body, capitals: false)
                        .fixedSize(horizontal: false, vertical: true)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .accessibilityLabel("Topic \(topic)")
                    SettingsButton(title: "Copy", action: { actions.copyTopic(topic) })
                }
            }
            if let note = model.pushNote { SettingsNote(text: note) }
        }
    }

    @ViewBuilder private var usageSection: some View {
        SettingsCard("Plan usage") {
            switch model.usage {
            case .loading: SettingsNote(text: "Loading…")
            case .notAvailableYet: SettingsNote(text: "Not available yet: the hub doesn't read plan usage yet.")
            case .failed(let words): SettingsNote(text: words, accent: true)
            case .loaded(let usage):
                ForEach(text.meters(usage), id: \.label) { UsageMeterView(meter: $0) }
                ForEach(text.usageNotes(usage), id: \.self) { SettingsNote(text: $0, accent: usage._guard.active) }
            }
        }
    }

    @ViewBuilder private var schedulesSection: some View {
        SettingsCard("Schedules") {
            switch model.schedules {
            case .loading: SettingsNote(text: "Loading…")
            case .notAvailableYet: SettingsNote(text: "Not available yet: the hub can't list schedules yet.")
            case .failed(let words): SettingsNote(text: words, accent: true)
            case .loaded(let list) where list.isEmpty:
                SettingsNote(text: "Nothing scheduled. Ask Marcel in the chat: “every Monday, …”.")
            case .loaded(let list):
                ForEach(list, id: \.id) { schedule in
                    SettingsRow(schedule.name, detail: text.scheduleLine(schedule)) {
                        HStack(spacing: PixelMetrics.steps(4)) {
                            SettingsButton(title: schedule.paused ? "Resume" : "Pause") {
                                actions.setPaused(schedule, !schedule.paused)
                            }
                            if !schedule.builtin {
                                SettingsButton(title: "Delete", destructive: true) { confirming = .delete(schedule) }
                            }
                        }
                    }
                }
            }
        }
    }

    @ViewBuilder private var devicesSection: some View {
        SettingsCard("Devices") {
            switch model.devices {
            case .loading: SettingsNote(text: "Loading…")
            case .notAvailableYet: SettingsNote(text: "Not available yet: the hub can't list devices yet.")
            case .failed(let words): SettingsNote(text: words, accent: true)
            case .loaded(let list):
                ForEach(list, id: \.id) { device in
                    SettingsRow(device.name, detail: text.deviceLine(device)) {
                        if device.current != true {
                            SettingsButton(title: "Remove", destructive: true) { confirming = .revoke(device) }
                        }
                    }
                }
            }
            SettingsButton(title: "Unpair this phone", destructive: true) { confirming = .unpair }
        }
    }
}

/// The name field's box.
struct FieldBox: ViewModifier {
    @Environment(\.colorScheme) private var scheme

    func body(content: Content) -> some View {
        let theme = PixelTheme(scheme)
        content
            .foregroundStyle(theme.text.color)
            .pixelBox(fill: theme.card, line: theme.line, shadow: nil, border: PixelMetrics.thinBorder, offset: 0)
    }
}
