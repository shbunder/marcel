import PixelKit
import SwiftUI

/// Snapshot tests draw the name field as plain text (`ImageRenderer` draws a `TextField` as a yellow box).
extension EnvironmentValues {
    @Entry public var settingsStill = false
}

/// "‹ Settings": a screen's top strip on white (the chat look, ADR-007).
struct SettingsHeader: View {
    var title: String
    var back: () -> Void

    var body: some View {
        ChatHeader(title, back: back)
    }
}

/// A group (wireframe Q6): its name in small muted pixel capitals above a white rounded box ("YOU"), the rows inside
/// with a thin line between them.
struct SettingsCard<Content: View>: View {
    var title: String
    var content: Content

    @Environment(\.colorScheme) private var scheme

    init(_ title: String, @ViewBuilder content: () -> Content) {
        self.title = title
        self.content = content()
    }

    var body: some View {
        let theme = ChatPalette(scheme)
        VStack(alignment: .leading, spacing: 6) {
            PixelText(title, role: .smallLabel)
                .foregroundStyle(theme.muted.color)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.leading, 2)
                .accessibilityAddTraits(.isHeader)
            Group(subviews: content) { rows in
                VStack(alignment: .leading, spacing: 0) {
                    ForEach(Array(rows.enumerated()), id: \.element.id) { index, row in
                        if index > 0 {
                            Rectangle().fill(theme.line.color).frame(height: 1)
                        }
                        row
                            .padding(.horizontal, 12)
                            .padding(.vertical, 10)
                            .frame(maxWidth: .infinity, minHeight: PixelMetrics.minTapTarget, alignment: .leading)
                    }
                }
            }
            .foregroundStyle(theme.ink.color)
            .fixedSize(horizontal: false, vertical: true)
            .chatSurface()
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityElement(children: .contain)
    }
}

/// A small rounded button in the normal font, at least 44 pt tall; a destructive one in the "failed" colour.
struct SettingsButton: View {
    var title: String
    var destructive = false
    var enabled = true
    var action: () -> Void

    @Environment(\.colorScheme) private var scheme

    var body: some View {
        let theme = ChatPalette(scheme)
        let tint = destructive ? ChatCardKind.failed.label(in: theme) : theme.ink
        Button(action: action) {
            Text(title)
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(tint.color)
                .padding(.horizontal, 14)
                .frame(minHeight: PixelMetrics.minTapTarget)
                .background(theme.card.color, in: Capsule())
                .overlay(Capsule().strokeBorder(destructive ? tint.color : theme.line.color, lineWidth: 1))
                .opacity(enabled ? 1 : 0.5)
        }
        .buttonStyle(.plain)
        .disabled(!enabled)
    }
}

/// One line of a card: a label, and a detail line under it in the muted colour.
struct SettingsRow<Trailing: View>: View {
    var label: String
    var detail: String?
    var trailing: Trailing

    @Environment(\.colorScheme) private var scheme

    init(_ label: String, detail: String? = nil, @ViewBuilder trailing: () -> Trailing = { EmptyView() }) {
        self.label = label
        self.detail = detail
        self.trailing = trailing()
    }

    var body: some View {
        let theme = ChatPalette(scheme)
        ViewThatFits(in: .horizontal) {
            HStack(alignment: .center, spacing: PixelMetrics.steps(4)) {
                text(theme)
                Spacer(minLength: 0)
                trailing
            }
            VStack(alignment: .leading, spacing: PixelMetrics.steps(3)) {
                text(theme)
                trailing
            }
        }
    }

    private func text(_ theme: ChatPalette) -> some View {
        VStack(alignment: .leading, spacing: PixelMetrics.steps(1)) {
            Text(label).font(.body)
                .foregroundStyle(theme.ink.color)
                .fixedSize(horizontal: false, vertical: true)
            if let detail {
                Text(detail).font(.footnote)
                    .foregroundStyle(theme.muted.color)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .accessibilityElement(children: .combine)
    }
}

/// A row that opens something (wireframe Q6): the label, a muted value and "›" on the right; the whole row is the button.
struct SettingsLinkRow: View {
    var label: String
    var detail: String?
    var value: String?
    var action: () -> Void

    @Environment(\.colorScheme) private var scheme

    init(_ label: String, detail: String? = nil, value: String? = nil, action: @escaping () -> Void) {
        self.label = label
        self.detail = detail
        self.value = value
        self.action = action
    }

    var body: some View {
        let theme = ChatPalette(scheme)
        Button(action: action) {
            SettingsRow(label, detail: detail) {
                Text([value, "›"].compactMap { $0 }.joined(separator: " "))
                    .font(.subheadline)
                    .foregroundStyle(theme.muted.color)
                    .accessibilityHidden(true)
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel([label, value].compactMap { $0 }.joined(separator: ", "))
        .accessibilityHint(detail ?? "")
        .accessibilityAddTraits(.isButton)
    }
}

/// A row that does something that can't be undone ("Sign out of this phone"): bold words in the "failed" colour.
struct SettingsDangerRow: View {
    var label: String
    var action: () -> Void

    @Environment(\.colorScheme) private var scheme

    var body: some View {
        Button(action: action) {
            Text(label)
                .font(.body.weight(.bold))
                .foregroundStyle(ChatCardKind.failed.label(in: ChatPalette(scheme)).color)
                .frame(maxWidth: .infinity, minHeight: PixelMetrics.minTapTarget, alignment: .leading)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}

/// The helper at the top of Settings (wireframe Q6): its avatar, its name in bold, its look in words and "Edit avatar
/// and name ›", on a white rounded card. The whole card opens the editor.
struct SettingsHero<Avatar: View>: View {
    var name: String
    var look: String
    var avatar: Avatar?
    var edit: (() -> Void)?

    @Environment(\.colorScheme) private var scheme

    var body: some View {
        let theme = ChatPalette(scheme)
        let card = HStack(spacing: 12) {
            if let avatar {
                avatar
                    .frame(width: 56, height: 56)
                    .accessibilityHidden(true)
            }
            VStack(alignment: .leading, spacing: 2) {
                Text(name)
                    .font(.headline.weight(.bold))
                    .foregroundStyle(theme.ink.color)
                Text(look)
                    .font(.footnote)
                    .foregroundStyle(theme.muted.color)
                    .fixedSize(horizontal: false, vertical: true)
                if edit != nil {
                    Text("Edit avatar and name ›")
                        .font(.footnote.weight(.bold))
                        .foregroundStyle(ChatCardKind.done.label(in: theme).color)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .padding(12)
        .chatSurface()
        if let edit {
            Button(action: edit) { card.contentShape(Rectangle()) }
                .buttonStyle(.plain)
                .accessibilityElement(children: .ignore)
                .accessibilityLabel("\(name). \(look)")
                .accessibilityHint("Edit the avatar and the name")
                .accessibilityAddTraits(.isButton)
        } else {
            card
                .accessibilityElement(children: .ignore)
                .accessibilityLabel("\(name). \(look)")
        }
    }
}

/// A plain sentence in the muted colour: "Not available yet…", "Loading…"; a problem in the link colour.
struct SettingsNote: View {
    var text: String
    var accent = false

    @Environment(\.colorScheme) private var scheme

    var body: some View {
        let theme = ChatPalette(scheme)
        Text(text).font(.footnote)
            .foregroundStyle((accent ? theme.link : theme.muted).color)
            .fixedSize(horizontal: false, vertical: true)
            .frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// A usage bar: label and percentage, then a rounded bar on the well colour, filled in teal like the wireframe's.
struct UsageMeterView: View {
    var meter: SettingsText.Meter

    @Environment(\.colorScheme) private var scheme

    var body: some View {
        let theme = ChatPalette(scheme)
        VStack(alignment: .leading, spacing: PixelMetrics.steps(2)) {
            HStack {
                Text(meter.label).font(.body)
                Spacer(minLength: 0)
                Text(meter.value).font(.body.monospacedDigit().weight(.semibold))
            }
            .foregroundStyle(theme.ink.color)
            GeometryReader { geometry in
                ZStack(alignment: .leading) {
                    Capsule().fill(theme.well.color)
                    Capsule().fill(ChatCardKind.started.frame(in: theme).color)
                        .frame(width: geometry.size.width * meter.fraction)
                }
            }
            .frame(height: 8)
            .overlay(Capsule().strokeBorder(theme.line.color, lineWidth: 1))
            if let detail = meter.detail {
                Text(detail).font(.footnote).foregroundStyle(theme.muted.color)
            }
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel([meter.label, "\(meter.value) used", meter.detail].compactMap { $0 }.joined(separator: ", "))
    }
}

/// A problem line at the top of a screen; tap to dismiss.
struct ProblemBanner: View {
    var text: String
    var dismiss: () -> Void

    @Environment(\.colorScheme) private var scheme

    var body: some View {
        let theme = ChatPalette(scheme)
        Button(action: dismiss) {
            Text(text).font(.subheadline)
                .foregroundStyle(theme.link.color)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(PixelMetrics.padding)
                .chatSurface(.well)
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Problem: \(text)")
        .accessibilityHint("Double-tap to dismiss")
    }
}

/// A link that could not be opened, over the tabs. Tap to dismiss.
public struct LinkNoticeView: View {
    public var text: String
    public var dismiss: () -> Void

    public init(text: String, dismiss: @escaping () -> Void) {
        self.text = text
        self.dismiss = dismiss
    }

    public var body: some View {
        ProblemBanner(text: text, dismiss: dismiss)
            .padding(.horizontal, PixelMetrics.padding)
            .padding(.top, PixelMetrics.steps(2))
    }
}
