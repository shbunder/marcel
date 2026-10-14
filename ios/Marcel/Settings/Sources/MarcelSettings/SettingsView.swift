import MarcelKit
import MarcelLook
import PixelKit
import SwiftUI
import UIKit

/// What Settings needs from the rest of the app: the helper's avatar, the look picker's hub, the Face ID switch, the
/// notebook, and signing out. Each is optional, so a screen or a test passes only what it has.
public struct SettingsAppLinks {
    /// The helper as the chat header draws it (its own sheet, S-19.3).
    public var avatar: AnyView?
    /// The look picker's hub (`GET /avatar-previews`, `PATCH /agents/{id}`); "Edit look and name" needs it.
    public var looks: (any LookService)?
    /// Face ID or the passcode to open the app (`AppLock.enabled`, S-17.5).
    public var faceID: Binding<Bool>?
    /// Opens Marcel's notebook.
    public var openNotebook: (() -> Void)?
    /// "Sign out of this phone" (`AppFlow.signOut`): returns what to tell the person when the hub wasn't told.
    public var signOut: () async -> String?

    public init(avatar: AnyView? = nil, looks: (any LookService)? = nil, faceID: Binding<Bool>? = nil,
                openNotebook: (() -> Void)? = nil, signOut: @escaping () async -> String? = { nil }) {
        self.avatar = avatar
        self.looks = looks
        self.faceID = faceID
        self.openNotebook = openNotebook
        self.signOut = signOut
    }
}

/// Settings (B-40, explainer Q6), opened from the avatar in the chat, in four groups: **Helper** (avatar, name, look),
/// **You** (notifications, Face ID), **Marcel** (notebook, schedules, usage, system status) and **Account** (People for
/// the owner, phones, sign out).
public struct SettingsView: View {
    @State private var model: SettingsModel
    private let statusModel: () -> StatusModel
    private let peopleModel: () -> PeopleModel
    private let links: SettingsAppLinks
    private let signedOut: (String?) -> Void

    @State private var showing: Screen?
    @Environment(\.dismiss) private var dismiss
    @Environment(\.colorScheme) private var scheme

    enum Screen: Hashable {
        case status, people, look
    }

    /// - Parameters:
    ///   - status: makes the status screen's model when "System status" is tapped.
    ///   - people: makes People's model (owner only).
    ///   - links: what Settings needs from the app.
    ///   - signedOut: after "Sign out of this phone", with the note when the hub couldn't be told.
    public init(model: SettingsModel, status: @escaping () -> StatusModel, people: @escaping () -> PeopleModel,
                links: SettingsAppLinks, signedOut: @escaping (String?) -> Void = { _ in }) {
        _model = State(initialValue: model)
        self.statusModel = status
        self.peopleModel = people
        self.links = links
        self.signedOut = signedOut
    }

    public var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                SettingsHeader(title: "Settings", back: { dismiss() })
                ScrollView {
                    SettingsContent(model: model, text: SettingsText(), links: links, actions: actions)
                        .padding(PixelMetrics.padding)
                }
                .refreshable { await model.load() }
            }
            .background(ChatPalette(scheme).well.color)
            .toolbar(.hidden, for: .navigationBar)
            .navigationDestination(item: $showing) { screen in
                switch screen {
                case .status: StatusView(model: statusModel())
                case .people: PeopleView(model: peopleModel())
                case .look:
                    if let looks = links.looks, let agent = model.agent {
                        LookEditor(model: LookModel(service: looks, agentId: agent.id, name: agent.name,
                                                    current: agent.drawnLook)) { Task { await model.load() } }
                    }
                }
            }
        }
        .task { await model.load() }
    }

    private var actions: SettingsActions {
        var actions = SettingsActions()
        actions.saveName = { Task { await model.saveName() } }
        actions.turnOnNotifications = { Task { await model.turnOnNotifications() } }
        actions.copyTopic = { TopicClipboard.copy($0) }
        actions.setPaused = { schedule, paused in Task { await model.setPaused(schedule, paused) } }
        actions.delete = { schedule in Task { await model.delete(schedule) } }
        actions.pauseInstead = { Task { await model.pauseInsteadOfDeleting() } }
        actions.revoke = { device in Task { await model.revoke(device) } }
        actions.signOut = { [model, links, signedOut] in
            Task {
                let note = await model.signOut(links.signOut)
                signedOut(note)
            }
        }
        actions.openStatus = { showing = .status }
        actions.openPeople = { showing = .people }
        actions.editLook = { showing = .look }
        actions.openNotebook = links.openNotebook ?? {}
        return actions
    }
}

/// "Edit look": the look picker with its own Save, back to Settings once saved.
struct LookEditor: View {
    var model: LookModel
    var saved: () -> Void
    @Environment(\.dismiss) private var dismiss
    @Environment(\.colorScheme) private var scheme

    var body: some View {
        VStack(spacing: 0) {
            SettingsHeader(title: "Look", back: { dismiss() })
            ScrollView {
                LookPicker(model: model) { _ in
                    saved()
                    dismiss()
                }
                .padding(PixelMetrics.padding)
            }
        }
        .background(ChatPalette(scheme).well.color)
        .toolbar(.hidden, for: .navigationBar)
    }
}

/// What the buttons in Settings do. Snapshots pass the empty one.
struct SettingsActions {
    var saveName: () -> Void = {}
    var turnOnNotifications: () -> Void = {}
    var copyTopic: (String) -> Void = { _ in }
    var setPaused: (Schedule, Bool) -> Void = { _, _ in }
    var delete: (Schedule) -> Void = { _ in }
    var pauseInstead: () -> Void = {}
    var revoke: (Device) -> Void = { _ in }
    var signOut: () -> Void = {}
    var openStatus: () -> Void = {}
    var openPeople: () -> Void = {}
    var editLook: () -> Void = {}
    var openNotebook: () -> Void = {}
}

/// Every section, top to bottom. The live screen puts it in a scroll view; snapshots draw it as it is.
struct SettingsContent: View {
    @Bindable var model: SettingsModel
    var text: SettingsText
    var links = SettingsAppLinks()
    var actions: SettingsActions

    @State private var confirming: Confirm?
    @Environment(\.settingsStill) private var still
    @Environment(\.colorScheme) private var scheme

    /// What a destructive button asks first.
    enum Confirm: Identifiable {
        case delete(Schedule)
        case revoke(Device)
        case signOut

        var id: String {
            switch self {
            case .delete(let schedule): "schedule-\(schedule.id)"
            case .revoke(let device): "device-\(device.id)"
            case .signOut: "sign-out"
            }
        }

        var title: String {
            switch self {
            case .delete(let schedule): "Delete “\(schedule.name)”? It won't run again."
            case .revoke(let device): "Remove “\(device.name)”? It stops reaching Marcel at once."
            case .signOut: "Sign out of this phone? It forgets the hub and everything saved from it."
            }
        }

        var button: String {
            switch self {
            case .delete: "Delete"
            case .revoke: "Remove"
            case .signOut: "Sign out"
            }
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: PixelMetrics.steps(5)) {
            if let problem = model.problem {
                ProblemBanner(text: problem) { model.problem = nil }
            }
            helperGroup
            youGroup
            marcelGroup
            accountGroup
        }
        .confirmationDialog(confirming?.title ?? "", isPresented: Binding(
            get: { confirming != nil }, set: { if !$0 { confirming = nil } }
        ), titleVisibility: .visible, presenting: confirming) { confirm in
            Button(confirm.button, role: .destructive) {
                switch confirm {
                case .delete(let schedule): actions.delete(schedule)
                case .revoke(let device): actions.revoke(device)
                case .signOut: actions.signOut()
                }
            }
            Button("Cancel", role: .cancel) {}
        }
    }

    // MARK: Sections

    // MARK: Groups

    private var helperGroup: some View {
        SettingsCard("Helper") {
            if let agent = model.agent {
                HStack(spacing: PixelMetrics.steps(4)) {
                    if let avatar = links.avatar {
                        avatar
                            .frame(width: 56, height: 56)
                            .accessibilityHidden(true)
                    }
                    SettingsRow(agent.name, detail: agent.drawnLook.map(LookWords.describe) ?? "The giraffe") {
                        if links.looks != nil {
                            SettingsButton(title: "Edit look", action: actions.editLook)
                        }
                    }
                }
            }
            nameField
        }
    }

    @ViewBuilder private var youGroup: some View {
        SettingsCard("You") {
            notificationsRows
            if let faceID = links.faceID {
                Toggle(isOn: faceID) {
                    SettingsRow("Face ID", detail: "Asks for Face ID or the passcode before Marcel opens, and again "
                                + "after 5 minutes away.")
                }
                .toggleStyle(SettingsSwitch())
                .accessibilityHint("Locks Marcel behind Face ID")
            }
        }
    }

    @ViewBuilder private var marcelGroup: some View {
        SettingsCard("Marcel") {
            if links.openNotebook != nil {
                SettingsRow("Notebook", detail: "What Marcel remembers about you and your projects") {
                    SettingsButton(title: "Open", action: actions.openNotebook)
                }
            }
            SettingsLabel("Schedules")
            schedulesRows
            SettingsLabel("Plan usage")
            usageRows
            SettingsRow("System status", detail: "The hub, the NUC and the last alert") {
                SettingsButton(title: "Open", action: actions.openStatus)
            }
        }
    }

    @ViewBuilder private var accountGroup: some View {
        SettingsCard("Account") {
            if model.showsPeople {
                SettingsRow("People", detail: "Add someone to Marcel, or remove them") {
                    SettingsButton(title: "Open", action: actions.openPeople)
                }
            }
            SettingsLabel("Phones")
            phonesRows
            SettingsButton(title: "Sign out of this phone", destructive: true) { confirming = .signOut }
        }
    }

    // MARK: Rows

    @ViewBuilder private var nameField: some View {
        SettingsLabel("Name")
        Group {
            if still {
                Text(model.name)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .modifier(FieldBox())
            } else {
                TextField("Marcel", text: $model.name)
                    .textInputAutocapitalization(.words)
                    .submitLabel(.done)
                    .onSubmit(actions.saveName)
                    .modifier(FieldBox())
                    .accessibilityLabel("Your helper's name")
            }
        }
        if let agent = model.agent {
            SettingsRow("Thinks with \(agent.modelBrain), works with \(agent.modelWorker)") {
                SettingsButton(title: "Save", enabled: model.nameToSave != nil, action: actions.saveName)
            }
        } else {
            SettingsNote(text: "Loading…")
        }
    }

    @ViewBuilder private var notificationsRows: some View {
        Group {
            switch model.push {
            case .off:
                SettingsRow("Notifications are off", detail: "Marcel can't reach you when something needs you.") {
                    SettingsButton(title: "Turn on", enabled: model.me != nil, action: actions.turnOnNotifications)
                }
            case .apns:
                SettingsRow("Notifications are on", detail: "Through Apple's push service.")
            case .ntfy(let topic, let server):
                SettingsRow("Notifications are on, through ntfy", detail: "They arrive in the ntfy app.")
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

    @ViewBuilder private var usageRows: some View {
        Group {
            switch model.usage {
            case .loading: SettingsNote(text: "Loading…")
            case .notAvailableYet: SettingsNote(text: "Not available yet: the hub doesn't read plan usage yet.")
            case .unavailable(let words): SettingsNote(text: words)
            case .failed(let words): SettingsNote(text: words, accent: true)
            case .loaded(let usage):
                ForEach(text.meters(usage), id: \.label) { UsageMeterView(meter: $0) }
                ForEach(text.usageNotes(usage), id: \.self) { SettingsNote(text: $0, accent: usage._guard.active) }
            }
        }
    }

    @ViewBuilder private var schedulesRows: some View {
        Group {
            switch model.schedules {
            case .loading: SettingsNote(text: "Loading…")
            case .notAvailableYet: SettingsNote(text: "Not available yet: the hub can't list schedules yet.")
            case .failed(let words), .unavailable(let words): SettingsNote(text: words, accent: true)
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
                    if model.pauseInstead?.id == schedule.id {
                        SettingsRow("Pause it instead?", detail: "“\(schedule.name)” is built in, so it can't be "
                                    + "deleted. Paused, it doesn't run until you resume it.") {
                            SettingsButton(title: "Pause", action: actions.pauseInstead)
                        }
                    }
                }
            }
        }
    }

    @ViewBuilder private var phonesRows: some View {
        Group {
            switch model.devices {
            case .loading: SettingsNote(text: "Loading…")
            case .notAvailableYet: SettingsNote(text: "Not available yet: the hub can't list devices yet.")
            case .failed(let words), .unavailable(let words): SettingsNote(text: words, accent: true)
            case .loaded(let list):
                ForEach(list, id: \.id) { device in
                    SettingsRow(device.name, detail: text.deviceLine(device)) {
                        if device.current != true {
                            SettingsButton(title: "Remove", destructive: true) { confirming = .revoke(device) }
                        }
                    }
                }
            }
        }
    }
}

/// The name field's box, rounded like the chat's.
struct FieldBox: ViewModifier {
    @Environment(\.colorScheme) private var scheme

    func body(content: Content) -> some View {
        let theme = ChatPalette(scheme)
        content
            .foregroundStyle(theme.ink.color)
            .padding(.horizontal, 12)
            .frame(minHeight: PixelMetrics.minTapTarget)
            .background(theme.card.color, in: RoundedRectangle(cornerRadius: 10))
            .overlay(RoundedRectangle(cornerRadius: 10).strokeBorder(theme.line.color, lineWidth: 1))
    }
}

/// An on/off switch in the chat look, drawn with shapes (the system switch does not show in snapshots).
struct SettingsSwitch: ToggleStyle {
    func makeBody(configuration: Configuration) -> some View {
        SwitchBody(configuration: configuration)
    }

    struct SwitchBody: View {
        let configuration: Configuration
        @Environment(\.colorScheme) private var scheme
        @Environment(\.accessibilityReduceMotion) private var reduceMotion

        var body: some View {
            let theme = ChatPalette(scheme)
            let on = configuration.isOn
            HStack(spacing: PixelMetrics.steps(4)) {
                configuration.label
                Button { configuration.isOn.toggle() } label: {
                    Capsule()
                        .fill((on ? ChatCardKind.done.label(in: theme) : theme.line).color)
                        .frame(width: 51, height: 31)
                        .overlay(alignment: on ? .trailing : .leading) {
                            Circle().fill(Color.white).padding(2).shadow(radius: 1, y: 1)
                        }
                        .animation(reduceMotion ? nil : .snappy(duration: 0.2), value: on)
                        .frame(minWidth: PixelMetrics.minTapTarget, minHeight: PixelMetrics.minTapTarget)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
            }
            .accessibilityElement(children: .combine)
            .accessibilityAddTraits(.isToggle)
            .accessibilityValue(on ? "On" : "Off")
            .accessibilityAction { configuration.isOn.toggle() }
        }
    }
}

/// A small label inside a group ("PHONES", "SCHEDULES").
struct SettingsLabel: View {
    var text: String
    @Environment(\.colorScheme) private var scheme

    init(_ text: String) { self.text = text }

    var body: some View {
        PixelText(text, role: .smallLabel)
            .foregroundStyle(ChatPalette(scheme).muted.color)
            .padding(.top, PixelMetrics.steps(2))
            .accessibilityAddTraits(.isHeader)
    }
}
