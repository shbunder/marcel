import SwiftUI

/// Marcel speaking: a white box with a hard border and shadow, the speaker's name on top and, when there is
/// more to read, a blinking ▼ in the corner. Anything under the text (a `ChoicePrompt`) goes in `accessory`.
public struct DialogBox<Accessory: View>: View {
    public var speaker: String
    public var text: String
    public var showsMore: Bool
    public var accessory: Accessory

    @Environment(\.colorScheme) private var scheme
    @Environment(\.dynamicTypeSize) private var dynamicType
    @Environment(\.accessibilityReduceMotion) private var systemReduceMotion
    @Environment(\.pixelReduceMotionOverride) private var reduceMotionOverride
    private var reduceMotion: Bool { reduceMotionOverride ?? systemReduceMotion }

    public init(speaker: String, text: String, showsMore: Bool = false, @ViewBuilder accessory: () -> Accessory) {
        self.speaker = speaker
        self.text = text
        self.showsMore = showsMore
        self.accessory = accessory()
    }

    /// What VoiceOver reads for the speaker and the text. The ▼ is decorative and never read.
    public var accessibilityText: String { "\(speaker) says: \(text)" }

    public var body: some View {
        let theme = PixelTheme(scheme)
        VStack(alignment: .leading, spacing: PixelMetrics.steps(2)) {
            VStack(alignment: .leading, spacing: PixelMetrics.steps(2)) {
                PixelText(speaker, role: .smallLabel)
                    .foregroundStyle(theme.accent.color)
                PixelText(text, role: .body)
                    .foregroundStyle(theme.text.color)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(accessibilityText)
            accessory
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, PixelMetrics.padding + 2)
        .padding(.top, PixelMetrics.padding)
        .padding(.bottom, showsMore ? PixelMetrics.padding + PixelMetrics.steps(3) : PixelMetrics.padding)
        .overlay(alignment: .bottomTrailing) {
            if showsMore {
                MoreMarker(color: theme.text, pixel: PixelGlyph.pixel(for: dynamicType), reduceMotion: reduceMotion)
                    .padding(.trailing, PixelMetrics.steps(4))
                    .padding(.bottom, PixelMetrics.steps(3))
            }
        }
        .pixelBox(fill: theme.dialog, line: theme.line, shadow: theme.shadow)
        .accessibilityElement(children: .contain)
    }
}

extension DialogBox where Accessory == EmptyView {
    public init(speaker: String, text: String, showsMore: Bool = false) {
        self.init(speaker: speaker, text: text, showsMore: showsMore) { EmptyView() }
    }
}

/// The ▼ "there is more" marker. It blinks once a second (on half, off half, like the wireframes' `steps(1)`
/// animation); with Reduce Motion on it stays still and visible.
struct MoreMarker: View {
    var color: PixelColor
    var pixel: CGFloat
    var reduceMotion: Bool
    @Environment(\.pixelBlinkDate) private var pinnedDate

    static let period: TimeInterval = 1

    /// Whether the marker is drawn at `date`. Reduce Motion always shows it.
    static func isVisible(at date: Date, reduceMotion: Bool) -> Bool {
        if reduceMotion { return true }
        let phase = date.timeIntervalSinceReferenceDate.truncatingRemainder(dividingBy: period)
        return phase < period / 2
    }

    /// Whether the marker animates at all: the control Reduce Motion switches off.
    static func animates(reduceMotion: Bool) -> Bool { !reduceMotion }

    var body: some View {
        if Self.animates(reduceMotion: reduceMotion) {
            TimelineView(.periodic(from: .distantPast, by: Self.period / 2)) { timeline in
                PixelGlyph(.more, color: color, pixel: pixel)
                    .opacity(Self.isVisible(at: pinnedDate ?? timeline.date, reduceMotion: reduceMotion) ? 1 : 0)
            }
        } else {
            PixelGlyph(.more, color: color, pixel: pixel)
        }
    }
}

extension EnvironmentValues {
    /// Tests pin Reduce Motion with this; the system setting is read-only.
    @Entry var pixelReduceMotionOverride: Bool? = nil
    /// Tests pin the blink's clock with this, so a render shows a known half of the blink.
    @Entry var pixelBlinkDate: Date? = nil
}

#Preview("Dialog box") {
    PixelPreview { _ in
        DialogBox(
            speaker: "Marcel",
            text: "Good morning. Today: swim class 16:30, paper bins tonight. Two things wait for you.",
            showsMore: true
        )
    }
}
