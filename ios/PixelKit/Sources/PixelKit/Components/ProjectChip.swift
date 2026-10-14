import SwiftUI

/// A project's name in an outlined tag ("Home energy"), coloured by the project's tone. Tells you where a thread
/// or item is filed.
public struct ProjectChip: View {
    /// The project colours. Each has a darker light-theme ink and a lighter dark-theme ink so the name keeps
    /// AA contrast on every surface it sits on.
    public enum Tone: String, Sendable, CaseIterable {
        case teal, rose, blue, green, gold

        public func color(in theme: PixelTheme) -> PixelColor {
            let isDark = theme == .dark
            switch self {
            case .teal: return isDark ? PixelColor(0x8CC2BF) : .teal
            case .rose: return isDark ? PixelColor(0xE89AAE) : .deep
            case .blue: return isDark ? PixelColor(0xA9B8E6) : PixelColor(0x4F6396)
            // The wireframe's sage (#6f8a55) is 3.38:1 on cream; darkened to pass AA.
            case .green: return isDark ? PixelColor(0xAFCB8F) : PixelColor(0x4E6A38)
            case .gold: return isDark ? .gold : PixelColor(0x8A5A0B)
            }
        }
    }

    public var name: String
    public var tone: Tone
    @Environment(\.colorScheme) private var scheme

    public init(_ name: String, tone: Tone = .teal) {
        self.name = name
        self.tone = tone
    }

    public var accessibilityText: String { "Project: \(name)" }

    /// The chat's language: the name on white in a rounded frame of the project's colour.
    public var body: some View {
        let ink = tone.color(in: PixelTheme(scheme)).color
        Text(name)
            .font(.caption.weight(.semibold))
            .lineLimit(1)
            .foregroundStyle(ink)
            .padding(.horizontal, 10)
            .padding(.vertical, 4)
            .background(ChatPalette(scheme).card.color, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: 12, style: .continuous).strokeBorder(ink, lineWidth: 1))
            .fixedSize()
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(accessibilityText)
    }
}

#Preview("Project chips") {
    PixelPreview { _ in
        VStack(alignment: .leading, spacing: 8) {
            ForEach(ProjectChip.Tone.allCases, id: \.self) { ProjectChip("Home energy", tone: $0) }
        }
    }
}
