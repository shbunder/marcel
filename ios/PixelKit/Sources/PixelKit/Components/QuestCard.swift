import SwiftUI

/// A thread, shown as a quest: its state, its title, the project it is filed under, a detail line ("step 1 of 4")
/// and, while it works, a progress bar. VoiceOver reads it as one sentence with the progress as its value.
public struct QuestCard: View {
    public var title: String
    public var state: StateBadge.Kind
    public var stateText: String?
    public var project: String?
    public var tone: ProjectChip.Tone
    public var detail: String?
    public var progress: Double?

    @Environment(\.colorScheme) private var scheme
    @Environment(\.dynamicTypeSize) private var dynamicType

    public init(
        title: String, state: StateBadge.Kind, stateText: String? = nil, project: String? = nil,
        tone: ProjectChip.Tone = .teal, detail: String? = nil, progress: Double? = nil
    ) {
        self.title = title
        self.state = state
        self.stateText = stateText
        self.project = project
        self.tone = tone
        self.detail = detail
        self.progress = progress.map { min(max($0, 0), 1) }
    }

    public var accessibilityText: String {
        var parts = [title, StateBadge(state, text: stateText).accessibilityText]
        if let project { parts.append("Project: \(project)") }
        if let detail { parts.append(detail) }
        return parts.joined(separator: ". ")
    }

    public var accessibilityProgress: String? {
        progress.map { "\(Int(($0 * 100).rounded())) percent done" }
    }

    public var body: some View {
        let theme = PixelTheme(scheme)
        VStack(alignment: .leading, spacing: PixelMetrics.steps(3)) {
            HStack(alignment: .center, spacing: PixelMetrics.steps(4)) {
                StateBadge(state, text: stateText)
                PixelText(title, role: .label)
                    .foregroundStyle(theme.text.color)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if project != nil || detail != nil {
                // With accessibility text sizes the detail goes under the chip instead of beside it.
                let layout = dynamicType.isAccessibilitySize
                    ? AnyLayout(VStackLayout(alignment: .leading, spacing: PixelMetrics.steps(2)))
                    : AnyLayout(HStackLayout(alignment: .center, spacing: PixelMetrics.steps(3)))
                layout {
                    if let project { ProjectChip(project, tone: tone) }
                    if let detail {
                        PixelText(detail, role: .detail)
                            .foregroundStyle(theme.muted.color)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
            if let progress {
                PixelProgressBar(value: progress, theme: theme)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, PixelMetrics.padding)
        .padding(.vertical, PixelMetrics.steps(4))
        .pixelBox(fill: theme.card, line: theme.line, shadow: nil, border: PixelMetrics.thinBorder)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(accessibilityText)
        .accessibilityValue(accessibilityProgress ?? "")
    }
}

/// A hard-edged bar: a 2-pt border around a track, filled in whole grid steps. The fill ends in a 2-pt line in the
/// border colour, so where it stops is clear even where rose and the light track are close (non-text contrast).
struct PixelProgressBar: View {
    var value: Double
    var theme: PixelTheme

    var body: some View {
        GeometryReader { proxy in
            let inner = proxy.size.width - 2 * PixelMetrics.thinBorder
            let width = (inner * value / PixelMetrics.grid).rounded() * PixelMetrics.grid
            HStack(spacing: 0) {
                Rectangle().fill(theme.fill.color)
                if width > 0, width < inner {
                    Rectangle().fill(theme.line.color).frame(width: PixelMetrics.grid)
                }
            }
            .frame(width: max(0, width))
            .padding(PixelMetrics.thinBorder)
        }
        .frame(height: PixelMetrics.steps(6))
        .background(Rectangle().fill(theme.track.color))
        .overlay(Rectangle().strokeBorder(theme.line.color, lineWidth: PixelMetrics.thinBorder))
    }
}

#Preview("Quest cards") {
    PixelPreview { _ in
        VStack(spacing: 10) {
            QuestCard(title: "Energy contracts", state: .working, project: "Home energy",
                      detail: "step 1 of 4", progress: 0.22)
            QuestCard(title: "Dentist booking", state: .needsYou, project: "Family", tone: .rose,
                      detail: "pick Tue 9:10 or Thu 8:30")
        }
    }
}
