import MarcelKit
import PixelKit
import SwiftUI

/// People (B-37), from Settings, for the owner only: everyone with their helper, phones and quests this week; "Add
/// person" (their name and their helper's), then the code for their first phone as a QR and a link; remove a person
/// after a confirmation that says what happens.
public struct PeopleView: View {
    @State private var model: PeopleModel
    @Environment(\.dismiss) private var dismiss
    @Environment(\.colorScheme) private var scheme

    public init(model: PeopleModel) {
        _model = State(initialValue: model)
    }

    public var body: some View {
        VStack(spacing: 0) {
            SettingsHeader(title: "People", back: { dismiss() })
            ScrollView {
                // "Expires in 9 min." counts down while the screen is open.
                TimelineView(.everyMinute) { context in
                    PeopleContent(model: model, now: context.date)
                        .padding(PixelMetrics.padding)
                }
            }
            .refreshable { await model.load() }
        }
        .background(ChatPalette(scheme).well.color)
        .toolbar(.hidden, for: .navigationBar)
        .task { await model.load() }
    }
}

/// Everything on the People screen; snapshots draw it without the scroll view.
struct PeopleContent: View {
    @Bindable var model: PeopleModel
    var now: Date

    @State private var name = ""
    @State private var helper = ""
    @State private var removing: User?
    @Environment(\.settingsStill) private var still

    var body: some View {
        VStack(alignment: .leading, spacing: PixelMetrics.steps(5)) {
            if let problem = model.problem {
                ProblemBanner(text: problem) { model.problem = nil }
            }
            if let removed = model.removed {
                ProblemBanner(text: removed) { model.dismissRemoved() }
            }
            if let code = model.code {
                CodeCard(code: code, now: now) { model.closeCode() }
            }
            SettingsCard("Everyone") {
                switch model.people {
                case .loading: SettingsNote(text: "Loading…")
                case .notAvailableYet: SettingsNote(text: "Not available yet: the hub doesn't do people yet.")
                case .failed(let words), .unavailable(let words): SettingsNote(text: words, accent: true)
                case .loaded(let list):
                    ForEach(list, id: \.id) { user in
                        SettingsRow(user.role == .owner ? "\(user.name) (owner)" : user.name,
                                    detail: Self.line(user)) {
                            if user.role != .owner {
                                HStack(spacing: PixelMetrics.steps(3)) {
                                    SettingsButton(title: "New code") { Task { await model.newCode(for: user) } }
                                    SettingsButton(title: "Remove", destructive: true) { removing = user }
                                }
                            }
                        }
                    }
                }
            }
            SettingsCard("Add person") {
                SettingsNote(text: "They get their own helper and their own chat. You'll get a code for their phone.")
                PeopleField(placeholder: "Their name", text: $name, still: still)
                PeopleField(placeholder: "Their helper's name", text: $helper, still: still)
                SettingsButton(title: model.busy ? "Adding…" : "Add person", enabled: !model.busy) {
                    Task {
                        if await model.add(name: name, helperName: helper) {
                            name = ""
                            helper = ""
                        }
                    }
                }
            }
        }
        .confirmationDialog(removing.map { "Remove \($0.name)?" } ?? "", isPresented: Binding(
            get: { removing != nil }, set: { if !$0 { removing = nil } }
        ), titleVisibility: .visible, presenting: removing) { user in
            Button("Remove \(user.name)", role: .destructive) { Task { await model.remove(user) } }
            Button("Cancel", role: .cancel) {}
        } message: { user in
            Text(PeopleModel.removalWords(user))
        }
    }

    /// "Helper: Pip · 2 phones · 3 quests this week".
    static func line(_ user: User) -> String {
        var parts: [String] = []
        if let agent = user.agent { parts.append("Helper: \(agent.name)") }
        parts.append(user.deviceCount == 1 ? "1 phone" : "\(user.deviceCount) phones")
        parts.append(user.questsThisWeek == 1 ? "1 quest this week" : "\(user.questsThisWeek) quests this week")
        return parts.joined(separator: " · ")
    }
}

/// The code for someone's phone: the QR to scan, the link to send, and the time left.
struct CodeCard: View {
    var code: PeopleModel.Code
    var now: Date
    var close: () -> Void

    @Environment(\.colorScheme) private var scheme

    var body: some View {
        let theme = ChatPalette(scheme)
        SettingsCard("Code for \(code.person)'s phone") {
            SettingsNote(text: "On \(code.person)'s phone: open Marcel and scan this, or send them the link.")
            if let qr = PeopleModel.qr(code.link) {
                Image(qr, scale: 1, label: Text("QR code for \(code.person)'s phone"))
                    .interpolation(.none)
                    .resizable()
                    .frame(width: 200, height: 200)
                    .padding(12)
                    .background(Color.white, in: RoundedRectangle(cornerRadius: 12))
                    .frame(maxWidth: .infinity)
            }
            Text(code.code)
                .font(.title2.monospaced().weight(.semibold))
                .foregroundStyle(theme.ink.color)
                .frame(maxWidth: .infinity)
                .accessibilityLabel("Code \(code.code.map(String.init).joined(separator: " "))")
            SettingsNote(text: PeopleModel.timeLeft(code, now: now))
            HStack(spacing: PixelMetrics.steps(3)) {
                if let url = URL(string: code.link) {
                    ShareLink(item: url, message: Text("Your code for Marcel")) {
                        PixelText("Send link", role: .smallLabel)
                            .foregroundStyle(theme.ink.color)
                            .padding(.horizontal, 14)
                            .frame(minHeight: PixelMetrics.minTapTarget)
                            .background(theme.card.color, in: Capsule())
                            .overlay(Capsule().strokeBorder(theme.line.color, lineWidth: 1))
                    }
                }
                SettingsButton(title: "Done", action: close)
            }
        }
    }
}

/// A text field in the chat look; plain text in snapshots.
struct PeopleField: View {
    var placeholder: String
    @Binding var text: String
    var still: Bool

    @Environment(\.colorScheme) private var scheme

    var body: some View {
        let theme = ChatPalette(scheme)
        Group {
            if still {
                Text(text.isEmpty ? placeholder : text)
                    .foregroundStyle((text.isEmpty ? theme.muted : theme.ink).color)
                    .frame(maxWidth: .infinity, alignment: .leading)
            } else {
                TextField(placeholder, text: $text)
                    .textInputAutocapitalization(.words)
                    .foregroundStyle(theme.ink.color)
                    .accessibilityLabel(placeholder)
            }
        }
        .padding(.horizontal, 12)
        .frame(minHeight: PixelMetrics.minTapTarget)
        .background(theme.card.color, in: RoundedRectangle(cornerRadius: 10))
        .overlay(RoundedRectangle(cornerRadius: 10).strokeBorder(theme.line.color, lineWidth: 1))
    }
}
