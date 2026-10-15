import ImageIO
import MarcelKit
import MarcelLook
import PixelKit
import SwiftUI

/// Settings › Buddies (B-49, wireframe Q6's People replaced): a card per buddy (its avatar and name, "Shown on this
/// phone" on the one this phone shows) with Rename, Look and Archive; then "Pair another phone", which makes a
/// one-time code as a QR card, starting on a buddy you pick (or the first one).
public struct BuddiesView: View {
    @State private var model: BuddiesModel
    @State private var lookFor: Agent?
    @Environment(\.dismiss) private var dismiss
    @Environment(\.colorScheme) private var scheme

    public init(model: BuddiesModel) {
        _model = State(initialValue: model)
    }

    public var body: some View {
        VStack(spacing: 0) {
            SettingsHeader(title: "Buddies", back: { dismiss() })
            ScrollView {
                // "Expires in 9 min." counts down while the screen is open.
                TimelineView(.everyMinute) { context in
                    BuddiesContent(model: model, now: context.date, editLook: { lookFor = $0 })
                        .padding(PixelMetrics.padding)
                }
            }
            .refreshable { await model.load() }
        }
        .background(ChatPalette(scheme).well.color)
        .toolbar(.hidden, for: .navigationBar)
        .task { await model.load() }
        .navigationDestination(item: Binding(get: { lookFor.map(\.id) }, set: { if $0 == nil { lookFor = nil } })) { _ in
            if let agent = lookFor, let looks = model.looks {
                BuddyLookScreen(look: LookModel(service: looks, agentId: agent.id, name: agent.name,
                                                current: agent.drawnLook)) {
                    lookFor = nil
                    Task { await model.load() }
                }
            }
        }
    }
}

/// One buddy's look picker, back to Buddies once saved.
struct BuddyLookScreen: View {
    var look: LookModel
    var saved: () -> Void
    @Environment(\.dismiss) private var dismiss
    @Environment(\.colorScheme) private var scheme

    var body: some View {
        VStack(spacing: 0) {
            SettingsHeader(title: "Look", back: { dismiss() })
            ScrollView {
                LookPicker(model: look) { _ in saved() }
                    .padding(PixelMetrics.padding)
            }
        }
        .background(ChatPalette(scheme).well.color)
        .toolbar(.hidden, for: .navigationBar)
    }
}

/// Everything on the Buddies screen; snapshots draw it without the scroll view.
struct BuddiesContent: View {
    @Bindable var model: BuddiesModel
    var now: Date
    var editLook: (Agent) -> Void = { _ in }

    @State private var archiving: Agent?
    @Environment(\.settingsStill) private var still

    var body: some View {
        VStack(alignment: .leading, spacing: PixelMetrics.steps(5)) {
            if let problem = model.problem {
                ProblemBanner(text: problem) { model.problem = nil }
            }
            if let archived = model.archived {
                ProblemBanner(text: archived) { model.dismissArchived() }
            }
            switch model.buddies {
            case .loading: SettingsNote(text: "Loading…")
            case .notAvailableYet: SettingsNote(text: "Not available yet: the hub doesn't do buddies yet.")
            case .failed(let words), .unavailable(let words): SettingsNote(text: words, accent: true)
            case .loaded(let list):
                ForEach(list, id: \.id) { agent in
                    BuddyCard(agent: agent, avatar: model.avatar(for: agent), shown: agent.id == model.shown,
                              canArchive: list.count > 1, still: still,
                              rename: { name in Task { await model.rename(agent, to: name) } },
                              look: model.looks == nil ? nil : { editLook(agent) },
                              archive: { archiving = agent })
                }
            }
            SettingsCard("Pair another phone") {
                VStack(alignment: .leading, spacing: PixelMetrics.steps(3)) {
                    SettingsNote(text: "Another phone of yours can use Marcel too. It sees every buddy and starts on "
                                 + "the one you pick.")
                    if case .loaded(let list) = model.buddies, list.count > 1 {
                        if still {
                            // ImageRenderer cannot draw a menu: the snapshot shows what it says.
                            Text("Starts on: " + (list.first { $0.id == model.startOn }?.name ?? "the first buddy"))
                                .font(.body)
                        } else {
                            Picker("Starts on", selection: $model.startOn) {
                                Text("The first buddy").tag(String?.none)
                                ForEach(list, id: \.id) { Text($0.name).tag(Optional($0.id)) }
                            }
                            .pickerStyle(.menu)
                        }
                    }
                }
                SettingsButton(title: model.busy ? "Making a code…" : "Make a code", enabled: !model.busy) {
                    Task { await model.newCode() }
                }
            }
            if let code = model.code {
                CodeCard(code: code, now: now) { model.closeCode() }
            }
        }
        .confirmationDialog(archiving.map { "Archive \($0.name)?" } ?? "", isPresented: Binding(
            get: { archiving != nil }, set: { if !$0 { archiving = nil } }
        ), titleVisibility: .visible, presenting: archiving) { agent in
            Button("Archive \(agent.name)", role: .destructive) { Task { await model.archive(agent) } }
            Button("Cancel", role: .cancel) {}
        } message: { agent in
            Text(BuddiesModel.archiveWords(agent))
        }
    }
}

/// One buddy: its avatar, its name in bold, "Shown on this phone" on the one shown; the name field with Save, then
/// Look and Archive (not for the last buddy: an install always keeps one), on a white rounded card.
struct BuddyCard: View {
    var agent: Agent
    var avatar: Data?
    var shown: Bool
    var canArchive: Bool
    var still: Bool
    var rename: (String) -> Void
    var look: (() -> Void)?
    var archive: () -> Void

    @State private var name: String?
    @Environment(\.colorScheme) private var scheme

    var body: some View {
        let theme = ChatPalette(scheme)
        let typed = Binding(get: { name ?? agent.name }, set: { name = $0 })
        VStack(alignment: .leading, spacing: PixelMetrics.steps(3)) {
            HStack(spacing: PixelMetrics.steps(4)) {
                HelperAvatar(png: avatar, box: 48)
                    .accessibilityHidden(true)
                VStack(alignment: .leading, spacing: 2) {
                    Text(agent.name)
                        .font(.body.weight(.bold))
                        .foregroundStyle(theme.ink.color)
                    if shown {
                        Text("Shown on this phone")
                            .font(.footnote)
                            .foregroundStyle(theme.muted.color)
                    }
                }
            }
            .accessibilityElement(children: .combine)
            HStack(spacing: PixelMetrics.steps(3)) {
                BuddyField(placeholder: "Name", text: typed, still: still)
                SettingsButton(title: "Save", enabled: (name ?? agent.name) != agent.name) {
                    rename(typed.wrappedValue)
                    name = nil
                }
            }
            HStack(spacing: PixelMetrics.steps(3)) {
                if let look { SettingsButton(title: "Look", action: look) }
                if canArchive { SettingsButton(title: "Archive", destructive: true, action: archive) }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(12)
        .chatSurface()
    }
}

/// A helper's 32 × 32 avatar in a square box, at the largest whole number of screen pixels per avatar pixel that fits,
/// with no smoothing: the same low-res Pixel Zoo art as everywhere else. An empty soft square while it loads.
struct HelperAvatar: View {
    var png: Data?
    var box: CGFloat

    @Environment(\.displayScale) private var displayScale
    @Environment(\.colorScheme) private var scheme

    var body: some View {
        Group {
            if let png, let source = CGImageSourceCreateWithData(png as CFData, nil),
               let image = CGImageSourceCreateImageAtIndex(source, 0, nil) {
                let zoom = max(1, (box * displayScale / CGFloat(max(image.width, image.height))).rounded(.down))
                Image(decorative: image, scale: displayScale)
                    .interpolation(.none)
                    .antialiased(false)
                    .resizable()
                    .frame(width: CGFloat(image.width) * zoom / displayScale,
                           height: CGFloat(image.height) * zoom / displayScale)
            } else {
                RoundedRectangle(cornerRadius: 10).fill(ChatPalette(scheme).well.color)
            }
        }
        .frame(width: box, height: box)
    }
}

/// The code for another phone (wireframe Q6): the QR to scan, the code, the time left, the buddy it starts on, and the
/// link to send, on a white rounded card.
struct CodeCard: View {
    var code: BuddiesModel.Code
    var now: Date
    var close: () -> Void

    @Environment(\.colorScheme) private var scheme

    var body: some View {
        let theme = ChatPalette(scheme)
        VStack(spacing: PixelMetrics.steps(3)) {
            if let qr = BuddiesModel.qr(code.link) {
                Image(qr, scale: 1, label: Text("QR code for the other phone"))
                    .interpolation(.none)
                    .resizable()
                    .frame(width: 200, height: 200)
                    .padding(12)
                    .background(Color.white, in: RoundedRectangle(cornerRadius: 12))
                    .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(theme.line.color, lineWidth: 1))
            }
            PixelText("Scan on the other phone", role: .smallLabel)
                .foregroundStyle(theme.muted.color)
                .multilineTextAlignment(.center)
                .accessibilityAddTraits(.isHeader)
            Text(code.code)
                .font(.title2.monospaced().weight(.semibold))
                .foregroundStyle(theme.ink.color)
                .accessibilityLabel("Code \(code.code.map(String.init).joined(separator: " "))")
            Text(Self.words(code) + " " + BuddiesModel.timeLeft(code, now: now))
                .font(.footnote)
                .foregroundStyle(theme.muted.color)
                .multilineTextAlignment(.center)
                .fixedSize(horizontal: false, vertical: true)
            HStack(spacing: PixelMetrics.steps(3)) {
                if let url = URL(string: code.link) {
                    ShareLink(item: url, message: Text("Your code for Marcel")) {
                        Text("Send link")
                            .font(.subheadline.weight(.semibold))
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
        .frame(maxWidth: .infinity)
        .padding(12)
        .chatSurface()
        .accessibilityElement(children: .contain)
    }

    /// "On the other phone, open Marcel and scan this, or send the link. It starts on Pip."
    static func words(_ code: BuddiesModel.Code) -> String {
        "On the other phone, open Marcel and scan this, or send the link."
            + (code.startsOn.map { " It starts on \($0)." } ?? "")
    }
}

/// A text field in the chat look; plain text in snapshots.
struct BuddyField: View {
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
