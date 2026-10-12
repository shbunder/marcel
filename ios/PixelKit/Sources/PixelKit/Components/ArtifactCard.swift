import SwiftUI

/// A preview of an artifact: a gold header with its title and a badge ("Updated now", "Pinned · v7"), and the
/// artifact's first lines underneath. The body is yours: rows, numbers, a mini table (see `ArtifactRow`).
public struct ArtifactCard<Content: View>: View {
    public var title: String
    public var badge: String?
    public var content: Content

    @Environment(\.colorScheme) private var scheme

    public init(title: String, badge: String? = nil, @ViewBuilder content: () -> Content) {
        self.title = title
        self.badge = badge
        self.content = content()
    }

    public var accessibilityText: String {
        ["Artifact: \(title)", badge].compactMap { $0 }.joined(separator: ". ")
    }

    public var body: some View {
        let theme = PixelTheme(scheme)
        VStack(alignment: .leading, spacing: 0) {
            HStack(alignment: .firstTextBaseline, spacing: PixelMetrics.steps(4)) {
                Text(title)
                    .fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 0)
                if let badge {
                    Text(badge).multilineTextAlignment(.trailing)
                }
            }
            .pixelText(.smallLabel)
            .foregroundStyle(theme.onHeader.color)
            .padding(.horizontal, PixelMetrics.padding)
            .padding(.vertical, PixelMetrics.steps(3))
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Rectangle().fill(theme.header.color))
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(accessibilityText)
            .accessibilityAddTraits(.isHeader)

            VStack(alignment: .leading, spacing: PixelMetrics.steps(2)) { content }
                .pixelText(.detail)
                .foregroundStyle(theme.text.color)
                .padding(.horizontal, PixelMetrics.padding)
                .padding(.top, PixelMetrics.steps(4))
                .padding(.bottom, PixelMetrics.steps(5))
                .frame(maxWidth: .infinity, alignment: .leading)
        }
        // The thick border is drawn over the edges; inset the content by it.
        .padding(PixelMetrics.thickBorder)
        .pixelBox(fill: theme.dialog, line: theme.line, shadow: theme.shadow)
        .accessibilityElement(children: .contain)
    }
}

/// One line of an artifact preview: a label on the left, an optional value on the right.
public struct ArtifactRow: View {
    public var label: String
    public var value: String?
    public var emphasised: Bool

    public init(_ label: String, value: String? = nil, emphasised: Bool = false) {
        self.label = label
        self.value = value
        self.emphasised = emphasised
    }

    @Environment(\.dynamicTypeSize) private var dynamicType

    public var body: some View {
        // With accessibility text sizes the value goes under its label instead of beside it.
        let layout = dynamicType.isAccessibilitySize
            ? AnyLayout(VStackLayout(alignment: .leading, spacing: 0))
            : AnyLayout(HStackLayout(alignment: .firstTextBaseline))
        layout {
            Text(label).fixedSize(horizontal: false, vertical: true)
            if !dynamicType.isAccessibilitySize { Spacer(minLength: PixelMetrics.steps(4)) }
            if let value {
                Text(value).fontWeight(emphasised ? .semibold : .regular)
            }
        }
        .accessibilityElement(children: .combine)
    }
}

#Preview("Artifact card") {
    PixelPreview { _ in
        ArtifactCard(title: "Energy comparison", badge: "Updated now") {
            ArtifactRow("Supplier A + solar", value: "€1,010", emphasised: true)
            ArtifactRow("Supplier A", value: "€1,284")
            ArtifactRow("Current", value: "€1,496")
        }
    }
}
