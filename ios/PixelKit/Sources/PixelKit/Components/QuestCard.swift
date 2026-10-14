import SwiftUI

/// A thread, shown as a quest (wireframe Q2): its title in bold, one pixel-lettered line with the project and a detail
/// ("HOUSE · NUC · STEP 1/4"), its state as the frame's colour and, while it works, a progress bar. VoiceOver reads it as one sentence with the progress as its value.
public struct QuestCard: View {
    public var title: String
    public var state: StateBadge.Kind
    public var stateText: String?
    public var project: String?
    public var tone: ProjectChip.Tone
    public var detail: String?
    public var progress: Double?

    @Environment(\.colorScheme) private var scheme

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

    /// The card's frame (S-18.4, ADR-007): gold only while it needs you (on a light tint), teal while it works (a quiet
    /// quest still counts as working), rose when it is ready for review, red when it failed; a thin line otherwise.
    public static func frame(_ state: StateBadge.Kind) -> ChatCardKind? {
        switch state {
        case .needsYou: .needsYou
        case .starting, .working, .silent: .started
        case .ready: .done
        case .failed: .failed
        case .queued, .done, .stopped, .unknown: nil
        }
    }

    /// "Home energy · NUC · step 1 of 4"; "Failed · Family · NUC" when `saysState`; the state's word alone when
    /// there is no project and no detail.
    public static func meta(project: String?, detail: String?, state: String, saysState: Bool = false) -> String {
        let parts = [project, detail].compactMap { $0 }.filter { !$0.isEmpty }
        if parts.isEmpty { return state }
        return ((saysState ? [state] : []) + parts).joined(separator: " · ")
    }

    /// The states whose card names them in words: the ones that share a group and a look with a finished quest.
    public static func saysState(_ state: StateBadge.Kind) -> Bool {
        state == .failed || state == .stopped
    }

    public var body: some View {
        let palette = ChatPalette(scheme)
        let frame = Self.frame(state)
        VStack(alignment: .leading, spacing: 6) {
            Text(title)
                .font(.body.weight(.bold))
                .foregroundStyle(palette.ink.color)
                .fixedSize(horizontal: false, vertical: true)
            // One pixel-lettered meta line (wireframe Q2): the project, then the detail ("HOUSE · NUC · APPROVE FILE
            // READ"). The state is the frame's colour and the group above; a quest that failed or was stopped also says
            // so in words, since "Done" holds those too and a colour alone is not enough.
            PixelText(Self.meta(project: project, detail: detail, state: stateText ?? state.title,
                                saysState: Self.saysState(state)), role: .smallLabel)
                .foregroundStyle(palette.muted.color)
                .fixedSize(horizontal: false, vertical: true)
            if let progress {
                ChatProgressBar(value: progress, color: (frame ?? .started).frame(in: palette), track: palette.line)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 12)
        .padding(.vertical, 10)
        .background((frame?.ground(in: palette) ?? palette.card).color, in: RoundedRectangle(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder((frame?.frame(in: palette) ?? palette.line).color,
                                                                  lineWidth: frame == nil ? 1 : 2))
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(accessibilityText)
        .accessibilityValue(accessibilityProgress ?? "")
    }
}

/// A thin rounded progress bar in the card's frame colour.
struct ChatProgressBar: View {
    var value: Double
    var color: PixelColor
    var track: PixelColor

    var body: some View {
        GeometryReader { proxy in
            Capsule().fill(track.color)
                .overlay(alignment: .leading) {
                    Capsule().fill(color.color).frame(width: max(6, proxy.size.width * value))
                }
        }
        .frame(height: 6)
        .accessibilityHidden(true)
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
