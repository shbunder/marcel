import SwiftUI

// The tabs beyond the chat in the chat's language (ADR-007, wireframe Q2 v10, S-18.6): a white header with a thin line
// under it, white rounded cards with a thin edge on the soft ground, state as a coloured frame, small pixel labels only
// for group titles and badges, and a rounded composer. Body text is the system font (Dynamic Type).

/// A screen's top strip: an optional "‹" back button, the title in bold body text with an optional line under it, and
/// whatever goes on the right ("+ New", "Notes").
public struct ChatHeader<Trailing: View>: View {
    public var title: String
    public var subtitle: String?
    /// What VoiceOver reads for the title; the title and subtitle when nil.
    public var spoken: String?
    /// When set, VoiceOver reads the title (a heading) and the subtitle as two items, the subtitle with these words.
    public var subtitleSpoken: String?
    public var back: (() -> Void)?
    public var backLabel: String
    public var trailing: Trailing

    @Environment(\.colorScheme) private var scheme

    public init(_ title: String, subtitle: String? = nil, spoken: String? = nil, subtitleSpoken: String? = nil,
                back: (() -> Void)? = nil, backLabel: String = "Back", @ViewBuilder trailing: () -> Trailing) {
        self.title = title
        self.subtitle = subtitle
        self.spoken = spoken
        self.subtitleSpoken = subtitleSpoken
        self.back = back
        self.backLabel = backLabel
        self.trailing = trailing()
    }

    public var body: some View {
        let palette = ChatPalette(scheme)
        HStack(alignment: .center, spacing: 6) {
            if let back {
                Button(action: back) {
                    Text("‹")
                        .font(.title.weight(.semibold))
                        .foregroundStyle(palette.ink.color)
                        .frame(width: PixelMetrics.minTapTarget, height: PixelMetrics.minTapTarget)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityLabel(backLabel)
            }
            if let subtitleSpoken {
                titles(palette, subtitleSpoken: subtitleSpoken)
            } else {
                titles(palette, subtitleSpoken: nil)
                    .accessibilityElement(children: .ignore)
                    .accessibilityLabel(spoken ?? [title, subtitle].compactMap { $0 }.joined(separator: ". "))
                    .accessibilityAddTraits(.isHeader)
            }
            Spacer(minLength: 8)
            trailing
        }
        .padding(.leading, back == nil ? ChatScreenMetrics.inset : 4)
        .padding(.trailing, ChatScreenMetrics.inset)
        .padding(.vertical, back == nil ? 12 : 4)
        .frame(maxWidth: .infinity, minHeight: 52, alignment: .leading)
        .background(palette.card.color)
        .overlay(alignment: .bottom) { Rectangle().fill(palette.line.color).frame(height: 1) }
    }

    private func titles(_ palette: ChatPalette, subtitleSpoken: String?) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title)
                .font(.headline.weight(.bold))
                .foregroundStyle(palette.ink.color)
                .fixedSize(horizontal: false, vertical: true)
                .accessibilityAddTraits(.isHeader)
            if let subtitle {
                Text(subtitle)
                    .font(.footnote)
                    .foregroundStyle(palette.muted.color)
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityLabel(subtitleSpoken ?? subtitle)
            }
        }
    }
}

extension ChatHeader where Trailing == EmptyView {
    public init(_ title: String, subtitle: String? = nil, spoken: String? = nil, subtitleSpoken: String? = nil,
                back: (() -> Void)? = nil, backLabel: String = "Back") {
        self.init(title, subtitle: subtitle, spoken: spoken, subtitleSpoken: subtitleSpoken, back: back,
                  backLabel: backLabel) { EmptyView() }
    }
}

/// How a card on the soft ground is drawn: white with a thin grey edge, or framed in a state's colour (2 pt); only a
/// "needs you" card is tinted.
public enum ChatSurfaceStyle: Equatable, Sendable {
    /// White, thin grey edge.
    case plain
    /// White (or the needs-you tint), framed in that kind's colour.
    case framed(ChatCardKind)
    /// The well colour with a thin edge: code, a note, a problem line.
    case well
    /// No fill, a dashed edge: "+ New".
    case dashed
}

extension View {
    /// A white rounded card on the soft ground (12 pt corners), or framed in a state's colour.
    public func chatSurface(_ style: ChatSurfaceStyle = .plain, radius: CGFloat = ChatScreenMetrics.radius)
        -> some View {
        modifier(ChatSurface(style: style, radius: radius))
    }
}

struct ChatSurface: ViewModifier {
    var style: ChatSurfaceStyle
    var radius: CGFloat

    @Environment(\.colorScheme) private var scheme

    func body(content: Content) -> some View {
        let palette = ChatPalette(scheme)
        let shape = RoundedRectangle(cornerRadius: radius, style: .continuous)
        switch style {
        case .plain:
            content
                .background(palette.card.color, in: shape)
                .overlay(shape.strokeBorder(palette.line.color, lineWidth: 1))
        case .framed(let kind):
            content
                .background(kind.ground(in: palette).color, in: shape)
                .overlay(shape.strokeBorder(kind.frame(in: palette).color, lineWidth: ChatCard<EmptyView>.frameWidth))
        case .well:
            content
                .background(palette.well.color, in: shape)
                .overlay(shape.strokeBorder(palette.line.color, lineWidth: 1))
        case .dashed:
            content
                .overlay(shape.strokeBorder(palette.muted.color, style: StrokeStyle(lineWidth: 1.5, dash: [5, 4])))
        }
    }
}

/// A group title on the soft ground ("QUESTS", "ITEMS", a project's name): small pixel capitals in the muted colour.
public struct ChatSectionTitle: View {
    public var title: String
    public var spoken: String?

    @Environment(\.colorScheme) private var scheme

    public init(_ title: String, spoken: String? = nil) {
        self.title = title
        self.spoken = spoken
    }

    public var body: some View {
        PixelText(title, role: .smallLabel)
            .foregroundStyle(ChatPalette(scheme).muted.color)
            .fixedSize(horizontal: false, vertical: true)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.top, 6)
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(spoken ?? title)
            .accessibilityAddTraits(.isHeader)
    }
}

/// A small filled badge in a state's colour ("1 NEEDS YOU", "UPDATED"): white (dark mode: card) text on the label
/// colour, rounded.
public struct ChatBadge: View {
    public var text: String
    public var kind: ChatCardKind

    @Environment(\.colorScheme) private var scheme

    public init(_ text: String, kind: ChatCardKind = .needsYou) {
        self.text = text
        self.kind = kind
    }

    public var body: some View {
        let palette = ChatPalette(scheme)
        PixelText(text, role: .smallLabel)
            .foregroundStyle(Self.text(kind, in: palette).color)
            .lineLimit(1)
            .fixedSize()
            .padding(.horizontal, 7)
            .padding(.vertical, 3)
            .background(Self.fill(kind, in: palette).color, in: Capsule())
    }

    /// "Needs you" is the wireframe's bright gold with dark letters; the others are their label colour with light ones.
    public static func fill(_ kind: ChatCardKind, in palette: ChatPalette) -> PixelColor {
        kind == .needsYou ? kind.frame(in: palette) : kind.label(in: palette)
    }

    public static func text(_ kind: ChatCardKind, in palette: ChatPalette) -> PixelColor {
        kind == .needsYou ? palette.youInk : kind.buttonText(in: palette)
    }
}

/// An outlined rounded button on the soft ground ("Ask about this", "Open in GitHub", "Open tracker"), in a state's
/// colour, at least 44 pt tall.
public struct ChatPillButton: View {
    public var title: String
    public var kind: ChatCardKind
    public var filled: Bool
    public var action: () -> Void

    @Environment(\.colorScheme) private var scheme

    public init(_ title: String, kind: ChatCardKind = .done, filled: Bool = false, action: @escaping () -> Void) {
        self.title = title
        self.kind = kind
        self.filled = filled
        self.action = action
    }

    public var body: some View {
        let palette = ChatPalette(scheme)
        let ink = kind.label(in: palette)
        Button(action: action) {
            Text(title)
                .font(.subheadline.weight(.semibold))
                .foregroundStyle((filled ? kind.buttonText(in: palette) : ink).color)
                .lineLimit(1)
                .padding(.horizontal, 14)
                .frame(minHeight: PixelMetrics.minTapTarget)
                .background(filled ? ink.color : palette.card.color, in: Capsule())
                .overlay(Capsule().strokeBorder(ink.color, lineWidth: 1.5))
                .contentShape(Capsule())
        }
        .buttonStyle(.plain)
        .fixedSize()
        .accessibilityLabel(title)
    }
}

/// "Open tracker" (B-46): a coding project's tracker page, shown only when the project has one.
public struct TrackerButton: View {
    public var url: URL

    @Environment(\.openURL) private var openURL

    public init(url: URL) {
        self.url = url
    }

    public var body: some View {
        ChatPillButton("Open tracker", kind: .started) { openURL(url) }
            .accessibilityHint("Opens the project's tracker")
    }

    /// The tracker's address when it is one the app may open (http or https with a host), else nil: nothing shows.
    public static func url(_ text: String?) -> URL? {
        guard let text, let url = URL(string: text), let scheme = url.scheme?.lowercased(),
              scheme == "https" || scheme == "http", url.host != nil else { return nil }
        return url
    }
}

/// A rounded composer: the text field (or, for snapshots, its words) on the well colour with a thin edge, and a round
/// send button in Marcel's rose, on a white strip with a thin line above it. What goes with the next message (mention
/// chips, an attached item) sits above the field on the same strip, at the same inset.
public struct ChatComposerField<Accessory: View>: View {
    @Binding public var text: String
    public var placeholder: String
    public var fieldLabel: String
    /// Snapshots: `ImageRenderer` draws a text field as a yellow box, so draw its words instead.
    public var still: Bool
    public var focused: FocusState<Bool>.Binding?
    public var send: () -> Void
    /// What VoiceOver calls the send button ("Send", "Send to this quest").
    public var sendLabel: String
    /// Above the field, on the strip: what goes with the next message.
    public var accessory: Accessory

    @Environment(\.colorScheme) private var scheme

    public init(text: Binding<String>, placeholder: String, fieldLabel: String, still: Bool = false,
                focused: FocusState<Bool>.Binding? = nil, sendLabel: String = "Send", send: @escaping () -> Void,
                @ViewBuilder accessory: () -> Accessory) {
        self.sendLabel = sendLabel
        _text = text
        self.placeholder = placeholder
        self.fieldLabel = fieldLabel
        self.still = still
        self.focused = focused
        self.send = send
        self.accessory = accessory()
    }

    var canSend: Bool { !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }

    public var body: some View {
        let palette = ChatPalette(scheme)
        VStack(alignment: .leading, spacing: 8) {
            accessory
            bar(palette)
        }
        .padding(.horizontal, ChatScreenMetrics.inset)
        .padding(.vertical, 8)
        .background(palette.card.color)
        .overlay(alignment: .top) { Rectangle().fill(palette.line.color).frame(height: 1) }
    }

    private func bar(_ palette: ChatPalette) -> some View {
        HStack(alignment: .bottom, spacing: 8) {
            field(palette)
                .padding(.horizontal, 14)
                .padding(.vertical, 10)
                .frame(maxWidth: .infinity, minHeight: PixelMetrics.minTapTarget, alignment: .leading)
                .background(palette.well.color, in: RoundedRectangle(cornerRadius: 22, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: 22, style: .continuous)
                    .strokeBorder(palette.line.color, lineWidth: 1))
            Button(action: send) {
                Image(systemName: "arrow.up")
                    .font(.body.weight(.bold))
                    .foregroundStyle((canSend ? ChatCardKind.done.buttonText(in: palette) : palette.muted).color)
                    .frame(width: PixelMetrics.minTapTarget, height: PixelMetrics.minTapTarget)
                    .background((canSend ? ChatCardKind.done.label(in: palette) : palette.well).color, in: Circle())
                    .overlay(Circle().strokeBorder(canSend ? Color.clear : palette.line.color, lineWidth: 1))
            }
            .buttonStyle(.plain)
            .disabled(!canSend)
            .accessibilityLabel(sendLabel)
        }
    }

    @ViewBuilder private func field(_ palette: ChatPalette) -> some View {
        if still {
            Text(text.isEmpty ? placeholder : text)
                .font(.body)
                .foregroundStyle((text.isEmpty ? palette.muted : palette.ink).color)
                .fixedSize(horizontal: false, vertical: true)
        } else if let focused {
            editable(palette).focused(focused)
        } else {
            editable(palette)
        }
    }

    private func editable(_ palette: ChatPalette) -> some View {
        TextField(text: $text, axis: .vertical) {
            Text(placeholder).foregroundStyle(palette.muted.color)
        }
        .lineLimit(1...5)
        .font(.body)
        .foregroundStyle(palette.ink.color)
        .tint(ChatCardKind.done.label(in: palette).color)
        .accessibilityLabel(fieldLabel)
    }
}

/// Spacing shared by the chat-look screens.
public enum ChatScreenMetrics {
    /// Side inset of a screen's content.
    public static let inset: CGFloat = 14
    /// Corner radius of a card or tile.
    public static let radius: CGFloat = 12
    /// Space between cards.
    public static let gap: CGFloat = 10
    /// Inside a card.
    public static let padding = EdgeInsets(top: 10, leading: 12, bottom: 10, trailing: 12)
    /// A screen's content under its header.
    public static let content = EdgeInsets(top: 12, leading: inset, bottom: 16, trailing: inset)
}

extension ChatPalette {
    /// The text pairs the chat-look screens add (S-18.6), held to 4.5:1 by PixelKit's `ChatScreensTests`.
    public var screenTextPairs: [(name: String, text: PixelColor, on: PixelColor)] {
        var pairs: [(String, PixelColor, PixelColor)] = [
            ("ink on ground", ink, well), ("muted on ground", muted, well), ("ink on header", ink, card),
            ("muted on header", muted, card),
        ]
        for kind in ChatCardKind.allCases {
            pairs.append(("\(kind) badge text", ChatBadge.text(kind, in: self), ChatBadge.fill(kind, in: self)))
            pairs.append(("\(kind) pill on card", kind.label(in: self), card))
            pairs.append(("\(kind) label on ground", kind.label(in: self), well))
        }
        return pairs
    }
}

extension ChatComposerField where Accessory == EmptyView {
    /// A composer with nothing above the field.
    public init(text: Binding<String>, placeholder: String, fieldLabel: String, still: Bool = false,
                focused: FocusState<Bool>.Binding? = nil, sendLabel: String = "Send", send: @escaping () -> Void) {
        self.init(text: text, placeholder: placeholder, fieldLabel: fieldLabel, still: still, focused: focused,
                  sendLabel: sendLabel, send: send, accessory: { EmptyView() })
    }
}
