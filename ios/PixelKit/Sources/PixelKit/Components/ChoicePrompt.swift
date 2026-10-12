import SwiftUI

/// A row of answers, Zelda style: "▶ Yes   Not now   Details". The ▶ sits on the answer that has focus — the
/// keyboard's (arrow keys move it, Return picks) or VoiceOver's — and on the first answer otherwise. Each answer is
/// a plain button that VoiceOver reads by its words; the ▶ is never read. Disable the prompt (`.disabled(true)`)
/// once it is answered and it greys out.
public struct ChoicePrompt: View {
    public struct Choice: Identifiable, Hashable, Sendable {
        public var id: String
        public var title: String

        public init(id: String, title: String) {
            self.id = id
            self.title = title
        }

        public init(_ title: String) {
            self.init(id: title, title: title)
        }
    }

    public var choices: [Choice]
    public var onChoose: (Choice) -> Void

    @State private var marked: Choice.ID?
    @FocusState private var keyboardFocus: Choice.ID?
    @AccessibilityFocusState private var voiceOverFocus: Choice.ID?
    @Environment(\.colorScheme) private var scheme
    @Environment(\.dynamicTypeSize) private var dynamicType
    @Environment(\.isEnabled) private var isEnabled
    @Environment(\.pixelRendersOffscreen) private var rendersOffscreen

    /// - Parameter marked: the answer the ▶ starts on (the first one when nil).
    public init(_ choices: [Choice], marked: Choice.ID? = nil, onChoose: @escaping (Choice) -> Void = { _ in }) {
        self.choices = choices
        self.onChoose = onChoose
        _marked = State(initialValue: marked)
    }

    /// The answer the ▶ is on.
    var markedChoice: Choice.ID? { marked ?? choices.first?.id }

    /// Moves the ▶ by `step` answers, staying within the row. Used by the arrow keys.
    static func move(_ current: Choice.ID?, by step: Int, in choices: [Choice]) -> Choice.ID? {
        guard !choices.isEmpty else { return nil }
        let index = choices.firstIndex { $0.id == current } ?? 0
        return choices[min(max(index + step, 0), choices.count - 1)].id
    }

    public var body: some View {
        ViewThatFits(in: .horizontal) {
            HStack(spacing: PixelMetrics.steps(6)) { buttons }
            VStack(alignment: .leading, spacing: 0) { buttons }
        }
        .onKeyPress(.leftArrow) { step(-1) }
        .onKeyPress(.upArrow) { step(-1) }
        .onKeyPress(.rightArrow) { step(1) }
        .onKeyPress(.downArrow) { step(1) }
        .onKeyPress(.return) {
            guard isEnabled, let choice = choices.first(where: { $0.id == markedChoice }) else { return .ignored }
            onChoose(choice)
            return .handled
        }
        .onChange(of: keyboardFocus) { _, id in if let id { marked = id } }
        .onChange(of: voiceOverFocus) { _, id in if let id { marked = id } }
    }

    private func step(_ by: Int) -> KeyPress.Result {
        marked = Self.move(markedChoice, by: by, in: choices)
        keyboardFocus = marked
        return .handled
    }

    @ViewBuilder private var buttons: some View {
        let theme = PixelTheme(scheme)
        let ink = isEnabled ? theme.text : theme.muted
        ForEach(choices) { choice in
            Button {
                marked = choice.id
                onChoose(choice)
            } label: {
                HStack(spacing: PixelMetrics.steps(3)) {
                    PixelGlyph(.pointer, color: isEnabled ? theme.accent : theme.muted,
                               pixel: PixelGlyph.pixel(for: dynamicType))
                        .opacity(choice.id == markedChoice ? 1 : 0)
                    Text(choice.title)
                        .pixelText(.label)
                        .foregroundStyle(ink.color)
                        .lineLimit(1)
                        .fixedSize()
                }
                .frame(minWidth: PixelMetrics.minTapTarget, minHeight: PixelMetrics.minTapTarget, alignment: .leading)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .focused($keyboardFocus, equals: choice.id)
            .modifier(TracksVoiceOverFocus(focus: $voiceOverFocus, id: choice.id, enabled: !rendersOffscreen))
            .accessibilityLabel(choice.title)
        }
    }
}

/// Moves the ▶ with VoiceOver's focus. Skipped when drawn off screen: `ImageRenderer` draws nothing at all for a
/// view with an accessibility-focus binding (there is no accessibility host), so the snapshot tests turn it off.
/// The accessibility audit test renders the prompt in a real window, with this on.
private struct TracksVoiceOverFocus: ViewModifier {
    var focus: AccessibilityFocusState<ChoicePrompt.Choice.ID?>.Binding
    var id: ChoicePrompt.Choice.ID
    var enabled: Bool

    func body(content: Content) -> some View {
        if enabled {
            content.accessibilityFocused(focus, equals: id)
        } else {
            content
        }
    }
}

extension EnvironmentValues {
    /// True while a view is drawn by `ImageRenderer` (the snapshot tests).
    @Entry var pixelRendersOffscreen = false
}

#Preview("Choice prompt") {
    PixelPreview { _ in
        DialogBox(speaker: "Marcel", text: "Supplier A is cheapest: €486 a year less with solar. Switch now?") {
            ChoicePrompt([.init("Yes"), .init("Not now"), .init("Details")])
        }
    }
}
