import SwiftUI

/// A small filled tag with a thread's state ("Working", "Your OK", "Done"). Its colours are the same in both
/// themes: the tag brings its own background.
public struct StateBadge: View {
    /// The states a thread can be in (the app API's `TaskState`, plus "ready" for work waiting for review).
    public enum Kind: String, Sendable, CaseIterable {
        case queued, starting, working, needsYou, ready, done, failed, stopped, silent, unknown

        /// What the badge shows (drawn in capitals).
        public var title: String {
            switch self {
            case .queued: "Queued"
            case .starting: "Starting"
            case .working: "Working"
            case .needsYou: "Your OK"
            case .ready: "Ready"
            case .done: "Done"
            case .failed: "Failed"
            case .stopped: "Stopped"
            case .silent: "Quiet"
            case .unknown: "Unknown"
            }
        }

        /// What VoiceOver says.
        public var spokenTitle: String {
            switch self {
            case .needsYou: "Needs your OK"
            case .ready: "Ready for review"
            case .silent: "Gone quiet"
            case .unknown: "State unknown"
            default: title
            }
        }

        public var background: PixelColor {
            switch self {
            // The wireframes print white on rose and on sky blue; both fail AA (2.9:1), so those tags use ink.
            case .starting, .working: .rose
            case .needsYou: .gold
            case .ready: PixelColor(0x6A9BCC)
            case .done: .teal
            case .failed: .deep
            case .queued: PixelColor(0xE9DCCD)
            case .stopped, .silent, .unknown: PixelColor(0x6F5F63)
            }
        }

        public var foreground: PixelColor {
            switch self {
            case .starting, .working, .needsYou, .ready, .queued: .ink
            case .done, .failed, .stopped, .silent, .unknown: PixelColor(0xFFFFFF)
            }
        }
    }

    public var kind: Kind
    public var text: String?

    /// - Parameter text: replaces the state's own title, e.g. "1 needs you" on a project tile.
    public init(_ kind: Kind, text: String? = nil) {
        self.kind = kind
        self.text = text
    }

    public var accessibilityText: String { text ?? kind.spokenTitle }

    public var body: some View {
        PixelText(text ?? kind.title, role: .smallLabel)
            .foregroundStyle(kind.foreground.color)
            .lineLimit(1)
            .padding(.horizontal, PixelMetrics.steps(3))
            .padding(.vertical, PixelMetrics.steps(2))
            .background(Rectangle().fill(kind.background.color))
            .fixedSize()
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(accessibilityText)
    }
}

#Preview("State badges") {
    PixelPreview { _ in
        VStack(alignment: .leading, spacing: 8) {
            ForEach(StateBadge.Kind.allCases, id: \.self) { StateBadge($0) }
        }
    }
}
