import CoreGraphics
import ImageIO
import MarcelKit
import PixelKit
import SwiftUI

/// The look picker (S-19.4, B-44): a live preview, "Pick from my name", the animals, the colours, the personalities
/// and Save. Onboarding and Settings show it; `saved` is called with the hub's answer.
public struct LookPicker: View {
    @Bindable var model: LookModel
    var showsSave: Bool
    var saved: (Agent) -> Void

    @Environment(\.colorScheme) private var scheme
    @Environment(\.dynamicTypeSize) private var dynamicType

    /// - Parameter showsSave: false where the screen around it saves the look itself (onboarding's "Start chatting").
    public init(model: LookModel, showsSave: Bool = true, saved: @escaping (Agent) -> Void = { _ in }) {
        self.model = model
        self.showsSave = showsSave
        self.saved = saved
    }

    public var body: some View {
        let theme = ChatPalette(scheme)
        VStack(alignment: .leading, spacing: PixelMetrics.steps(5)) {
            preview(theme)
            LookCard("Animal") {
                LazyVGrid(columns: columns(min: dynamicType.isAccessibilitySize ? 150 : 76), spacing: 8) {
                    ForEach(LookFromName.animals, id: \.self) { animal in
                        AnimalCell(model: model, animal: animal)
                    }
                }
            }
            LookCard("Colour") {
                LazyVGrid(columns: columns(min: 44), spacing: 8) {
                    ForEach([LookColor.natural] + LookFromName.colors, id: \.self) { color in
                        Swatch(color: color, animal: model.look.animal, selected: model.look.color == color) {
                            model.choose(color: color)
                        }
                    }
                }
            }
            LookCard("Personality") {
                LazyVGrid(columns: columns(min: dynamicType.isAccessibilitySize ? 220 : 104), spacing: 8) {
                    ForEach(LookFromName.personalities, id: \.self) { personality in
                        LookChoice(LookWords.personality(personality),
                                   selected: model.look.personality == personality) {
                            model.choose(personality: personality)
                        }
                    }
                }
            }
            if let problem = model.problem {
                Text(problem)
                    .font(.subheadline)
                    .foregroundStyle(LookAccent.color(theme))
            }
            if showsSave {
                LookButton(title: model.saving ? "Saving…" : "Save", primary: true, enabled: model.canSave) {
                    Task { if let agent = await model.save() { saved(agent) } }
                }
            }
        }
    }

    private func preview(_ theme: ChatPalette) -> some View {
        HStack(alignment: .center, spacing: PixelMetrics.steps(4)) {
            // The words next to it say the look; VoiceOver reads them once.
            PreviewImage(png: model.previews[model.look], size: 112, words: LookWords.describe(model.look))
                .accessibilityHidden(true)
                .task(id: model.look) { await model.loadPreview(model.look) }
                .padding(8)
                .chatSurface()
            VStack(alignment: .leading, spacing: PixelMetrics.steps(2)) {
                Text(model.name)
                    .font(.title3.weight(.bold))
                    .foregroundStyle(theme.ink.color)
                Text(LookWords.describe(model.look))
                    .font(.subheadline)
                    .foregroundStyle(theme.ink.color)
                if let problem = model.previewProblem, model.previews[model.look] == nil {
                    Text(problem)
                        .font(.footnote)
                        .foregroundStyle(LookAccent.color(theme))
                }
                LookButton(title: "Pick from my name", primary: false, enabled: true) { model.pickFromName() }
                    .accessibilityHint("Picks the look the name \(model.name) gives")
            }
        }
        .accessibilityElement(children: .contain)
    }

    private func columns(min: CGFloat) -> [GridItem] {
        [GridItem(.adaptive(minimum: min), spacing: 8)]
    }
}

/// An animal in the grid, drawn in the colour and personality chosen so far.
struct AnimalCell: View {
    var model: LookModel
    var animal: LookAnimal

    @Environment(\.colorScheme) private var scheme

    var body: some View {
        let theme = ChatPalette(scheme)
        let look = model.trying(animal: animal)
        let selected = model.look.animal == animal
        Button { model.choose(animal: animal) } label: {
            VStack(spacing: 2) {
                PreviewImage(png: model.previews[look], size: 48, words: LookWords.animal(animal))
                Text(LookWords.animal(animal))
                    .font(.caption)
                    .lineLimit(1)
                    .minimumScaleFactor(0.8)
            }
            .foregroundStyle(theme.ink.color)
            .padding(.vertical, 6)
            .frame(maxWidth: .infinity, minHeight: PixelMetrics.minTapTarget)
            .background(RoundedRectangle(cornerRadius: 10).fill(theme.card.color))
            .overlay(RoundedRectangle(cornerRadius: 10)
                .strokeBorder(selected ? LookAccent.color(theme) : theme.line.color, lineWidth: selected ? 3 : 1))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .task(id: look) { await model.loadPreview(look) }
        .accessibilityLabel(LookWords.animal(animal))
        .accessibilityAddTraits(selected ? [.isSelected] : [])
    }
}

/// A colour: a round swatch; "its own colours" shows the animal's own colour with a sparkle.
struct Swatch: View {
    var color: LookColor
    var animal: LookAnimal
    var selected: Bool
    var action: () -> Void

    @Environment(\.colorScheme) private var scheme

    var body: some View {
        let theme = ChatPalette(scheme)
        let fill = LookColors.bubble(KnownLook(animal: animal, color: color, personality: .cozy))
        Button(action: action) {
            Circle()
                .fill(fill.color)
                .overlay(Circle().strokeBorder(theme.line.color, lineWidth: 1))
                .overlay {
                    if color == .natural {
                        Image(systemName: "sparkles")
                            .font(.caption.weight(.bold))
                            .foregroundStyle(theme.ink.color)
                    }
                }
                .frame(width: 32, height: 32)
                .padding(3)
                .overlay(Circle().strokeBorder(selected ? LookAccent.color(theme) : .clear, lineWidth: 3))
                .frame(width: PixelMetrics.minTapTarget, height: PixelMetrics.minTapTarget)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel(LookWords.color(color))
        .accessibilityAddTraits(selected ? [.isSelected] : [])
    }
}

/// A word to pick (a personality).
struct LookChoice: View {
    var title: String
    var selected: Bool
    var action: () -> Void

    @Environment(\.colorScheme) private var scheme

    init(_ title: String, selected: Bool, action: @escaping () -> Void) {
        self.title = title
        self.selected = selected
        self.action = action
    }

    var body: some View {
        let theme = ChatPalette(scheme)
        Button(action: action) {
            Text(title)
                .font(.subheadline.weight(selected ? .semibold : .regular))
                .foregroundStyle(theme.ink.color)
                .frame(maxWidth: .infinity, minHeight: PixelMetrics.minTapTarget)
                .background(RoundedRectangle(cornerRadius: 10).fill(theme.card.color))
                .overlay(RoundedRectangle(cornerRadius: 10)
                    .strokeBorder(selected ? LookAccent.color(theme) : theme.line.color, lineWidth: selected ? 3 : 1))
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityAddTraits(selected ? [.isSelected] : [])
    }
}

/// A titled section.
struct LookCard<Content: View>: View {
    var title: String
    var content: Content

    @Environment(\.colorScheme) private var scheme

    init(_ title: String, @ViewBuilder content: () -> Content) {
        self.title = title
        self.content = content()
    }

    var body: some View {
        let theme = ChatPalette(scheme)
        VStack(alignment: .leading, spacing: PixelMetrics.steps(3)) {
            PixelText(title, role: .smallLabel)
                .foregroundStyle(theme.muted.color)
                .accessibilityAddTraits(.isHeader)
            content
        }
        .accessibilityElement(children: .contain)
    }
}

/// A rounded button in the normal font, at least 44 pt tall; the primary one is a filled rose pill.
struct LookButton: View {
    var title: String
    var primary: Bool
    var enabled: Bool
    var action: () -> Void

    @Environment(\.colorScheme) private var scheme

    var body: some View {
        let theme = ChatPalette(scheme)
        Button(action: action) {
            Text(title)
                .font((primary ? Font.body : .subheadline).weight(.bold))
                .foregroundStyle((primary ? ChatCardKind.done.buttonText(in: theme) : theme.ink).color)
                .padding(.horizontal, 16)
                .frame(minHeight: PixelMetrics.minTapTarget)
                .background((primary ? ChatCardKind.done.label(in: theme) : theme.card).color, in: Capsule())
                .overlay(Capsule().strokeBorder(primary ? Color.clear : theme.line.color, lineWidth: 1))
                .opacity(enabled ? 1 : 0.5)
                .frame(maxWidth: primary ? .infinity : nil)
        }
        .buttonStyle(.plain)
        .disabled(!enabled)
        .accessibilityLabel(title)
    }
}

/// A 32 × 32 preview from the hub, drawn with sharp pixels; the words while it loads or offline.
struct PreviewImage: View {
    var png: Data?
    var size: CGFloat
    var words: String

    @Environment(\.colorScheme) private var scheme

    var body: some View {
        Group {
            if let image = png.flatMap(Self.decode) {
                Image(decorative: image, scale: 1)
                    .interpolation(.none)
                    .antialiased(false)
                    .resizable()
            } else {
                Color.clear
            }
        }
        .frame(width: size, height: size)
        .accessibilityElement()
        .accessibilityLabel(words)
    }

    static func decode(_ png: Data) -> CGImage? {
        guard let source = CGImageSourceCreateWithData(png as CFData, nil) else { return nil }
        return CGImageSourceCreateImageAtIndex(source, 0, nil)
    }
}

/// The picker's selection colour: Marcel's rose, the chat's "done" label colour.
enum LookAccent {
    static func color(_ palette: ChatPalette) -> Color { ChatCardKind.done.label(in: palette).color }
}
