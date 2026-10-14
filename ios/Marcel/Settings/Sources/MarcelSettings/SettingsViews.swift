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

/// A titled card: the group's name in the link colour ("HELPER"), its rows underneath, on a white surface.
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
        VStack(alignment: .leading, spacing: PixelMetrics.steps(4)) {
            PixelText(title, role: .smallLabel)
                .foregroundStyle(theme.link.color)
                .fixedSize(horizontal: false, vertical: true)
                .accessibilityAddTraits(.isHeader)
            content
        }
        .foregroundStyle(theme.ink.color)
        .padding(PixelMetrics.padding + PixelMetrics.thinBorder)
        .frame(maxWidth: .infinity, alignment: .leading)
        .fixedSize(horizontal: false, vertical: true)
        .chatSurface()
        .accessibilityElement(children: .contain)
    }
}

/// A small rounded button with a pixel label, at least 44 pt tall; a destructive one in the "failed" colour.
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
            PixelText(title, role: .smallLabel)
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

/// A usage bar: label and percentage, then a rounded bar on the well colour, filled in the link colour.
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
                    Capsule().fill(theme.link.color).frame(width: geometry.size.width * meter.fraction)
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
