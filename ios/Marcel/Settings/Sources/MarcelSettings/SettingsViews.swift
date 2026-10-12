import PixelKit
import SwiftUI

/// Snapshot tests draw the name field as plain text (`ImageRenderer` draws a `TextField` as a yellow box).
extension EnvironmentValues {
    @Entry public var settingsStill = false
}

/// "◀ Settings": the header of Settings and the status screen.
struct SettingsHeader: View {
    var title: String
    var back: () -> Void

    @Environment(\.colorScheme) private var scheme

    var body: some View {
        let theme = PixelTheme(scheme)
        HStack(spacing: PixelMetrics.steps(3)) {
            Button(action: back) {
                PixelGlyph(.pointer, color: theme.text, pixel: 3)
                    .scaleEffect(x: -1)
                    .frame(width: PixelMetrics.minTapTarget, height: PixelMetrics.minTapTarget)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Back")
            PixelText(title, role: .title)
                .foregroundStyle(theme.text.color)
                .accessibilityAddTraits(.isHeader)
            Spacer(minLength: 0)
        }
        .padding(.leading, PixelMetrics.steps(1))
        .padding(.trailing, PixelMetrics.padding)
        .padding(.vertical, PixelMetrics.steps(3))
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(theme.bar.color)
        .overlay(alignment: .bottom) {
            Rectangle().fill(theme.line.color).frame(height: PixelMetrics.thinBorder)
        }
    }
}

/// A titled box: the section's name in the accent colour, its rows underneath.
struct SettingsCard<Content: View>: View {
    var title: String
    var content: Content

    @Environment(\.colorScheme) private var scheme

    init(_ title: String, @ViewBuilder content: () -> Content) {
        self.title = title
        self.content = content()
    }

    var body: some View {
        let theme = PixelTheme(scheme)
        VStack(alignment: .leading, spacing: PixelMetrics.steps(4)) {
            PixelText(title, role: .smallLabel)
                .foregroundStyle(theme.accent.color)
                .accessibilityAddTraits(.isHeader)
            content
        }
        .foregroundStyle(theme.text.color)
        .padding(PixelMetrics.padding + PixelMetrics.thinBorder)
        .frame(maxWidth: .infinity, alignment: .leading)
        .pixelBox(fill: theme.dialog, line: theme.line, shadow: theme.shadow, border: PixelMetrics.thinBorder,
                  offset: PixelMetrics.smallShadow)
        .accessibilityElement(children: .contain)
    }
}

/// A pixel button: a small boxed label, at least 44 pt tall.
struct SettingsButton: View {
    var title: String
    var destructive = false
    var enabled = true
    var action: () -> Void

    @Environment(\.colorScheme) private var scheme

    var body: some View {
        let theme = PixelTheme(scheme)
        Button(action: action) {
            PixelText(title, role: .smallLabel)
                .foregroundStyle((destructive ? theme.accent : theme.text).color)
                .padding(.horizontal, PixelMetrics.steps(5))
                .frame(minHeight: PixelMetrics.minTapTarget)
                .pixelBox(fill: theme.card, line: destructive ? theme.accent : theme.line, shadow: theme.shadow,
                          border: PixelMetrics.thinBorder, offset: PixelMetrics.smallShadow)
                .opacity(enabled ? 1 : 0.5)
        }
        .buttonStyle(.plain)
        .disabled(!enabled)
    }
}

/// One line of a section: a label, and a detail line under it in the muted colour.
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
        let theme = PixelTheme(scheme)
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

    private func text(_ theme: PixelTheme) -> some View {
        VStack(alignment: .leading, spacing: PixelMetrics.steps(1)) {
            PixelText(label, role: .label, capitals: false)
                .fixedSize(horizontal: false, vertical: true)
            if let detail {
                PixelText(detail, role: .detail)
                    .foregroundStyle(theme.muted.color)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .accessibilityElement(children: .combine)
    }
}

/// A plain sentence in the muted colour: "Not available yet…", "Loading…".
struct SettingsNote: View {
    var text: String
    var accent = false

    @Environment(\.colorScheme) private var scheme

    var body: some View {
        let theme = PixelTheme(scheme)
        PixelText(text, role: .detail)
            .foregroundStyle((accent ? theme.accent : theme.muted).color)
            .fixedSize(horizontal: false, vertical: true)
            .frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// A usage bar: label and percentage, then the bar on the theme's track and fill.
struct UsageMeterView: View {
    var meter: SettingsText.Meter

    @Environment(\.colorScheme) private var scheme

    var body: some View {
        let theme = PixelTheme(scheme)
        VStack(alignment: .leading, spacing: PixelMetrics.steps(2)) {
            HStack {
                PixelText(meter.label, role: .label, capitals: false)
                Spacer(minLength: 0)
                PixelText(meter.value, role: .label)
            }
            GeometryReader { geometry in
                let width = (geometry.size.width * meter.fraction / PixelMetrics.grid).rounded() * PixelMetrics.grid
                ZStack(alignment: .leading) {
                    Rectangle().fill(theme.track.color)
                    Rectangle().fill(theme.fill.color).frame(width: width)
                }
            }
            .frame(height: PixelMetrics.steps(5))
            .overlay(Rectangle().strokeBorder(theme.line.color, lineWidth: PixelMetrics.thinBorder))
            if let detail = meter.detail {
                PixelText(detail, role: .detail).foregroundStyle(theme.muted.color)
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
        let theme = PixelTheme(scheme)
        Button(action: dismiss) {
            PixelText(text, role: .detail)
                .foregroundStyle(theme.accent.color)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(PixelMetrics.padding)
                .pixelBox(fill: theme.card, line: theme.accent, shadow: nil, border: PixelMetrics.thinBorder,
                          offset: 0)
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
