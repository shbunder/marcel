import AvatarKit
import MarcelKit
import MarcelLook
import PixelKit
import SwiftUI

extension EnvironmentValues {
    /// Snapshot tests draw text fields as plain text and the camera as an empty box (`ImageRenderer` can draw
    /// neither), and the avatar as one still frame.
    @Entry public var welcomeStill = false
}

/// The welcome screen (S-17.5): the avatar, how to get in, the scanner, a paste-a-link fallback, and what to do
/// without a code. `pair` gets what was scanned or pasted and throws the reason it did not work.
public struct WelcomeView: View {
    var pair: (String) async throws -> Void
    @State private var pasted = ""
    @State private var working = false
    @State private var problem: String?

    @Environment(\.colorScheme) private var scheme
    @Environment(\.welcomeStill) private var still

    public init(pair: @escaping (String) async throws -> Void) {
        self.pair = pair
    }

    public var body: some View {
        let theme = PixelTheme(scheme)
        Scrolling(still: still) {
            VStack(spacing: PixelMetrics.steps(5)) {
                WelcomeAvatar(still: still)
                PixelText("Welcome to Marcel", role: .title)
                    .foregroundStyle(theme.text.color)
                    .accessibilityAddTraits(.isHeader)
                Text("Scan the code from the person who runs Marcel at home")
                    .font(.body)
                    .multilineTextAlignment(.center)
                    .foregroundStyle(theme.text.color)
                Group {
                    if still {
                        theme.card.color
                    } else {
                        QRScannerView { code in submit(code) }
                    }
                }
                .frame(maxWidth: .infinity, minHeight: 220, maxHeight: 280)
                .clipShape(RoundedRectangle(cornerRadius: 12))
                .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(theme.line.color, lineWidth: 1))
                .accessibilityLabel("Camera, looking for the code")
                VStack(alignment: .leading, spacing: PixelMetrics.steps(2)) {
                    Text("Or paste the link you were sent")
                        .font(.subheadline)
                        .foregroundStyle(theme.text.color)
                    WelcomeField(placeholder: "marcel://pair?…", text: $pasted, still: still) { submit(pasted) }
                    WelcomeButton(title: working ? "Signing in…" : "Sign in", enabled: !working && !pasted.isEmpty) {
                        submit(pasted)
                    }
                }
                if let problem {
                    Text(problem)
                        .font(.subheadline)
                        .foregroundStyle(theme.accent.color)
                        .multilineTextAlignment(.center)
                }
                Text("No code? Ask the owner to add you.")
                    .font(.footnote)
                    .foregroundStyle(theme.muted.color)
                    .multilineTextAlignment(.center)
            }
            .padding(PixelMetrics.padding)
        }
        .background(theme.background.color.ignoresSafeArea())
    }

    private func submit(_ text: String) {
        let text = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !working, !text.isEmpty else { return }
        working = true
        problem = nil
        Task {
            do { try await pair(text) } catch { problem = error.localizedDescription }
            working = false
        }
    }
}

/// "Hi Emma! Meet your helper" (S-17.5): the helper's name, the look picker, and "Start chatting".
public struct OnboardingView: View {
    @Bindable var model: OnboardingModel
    var started: () -> Void

    @Environment(\.colorScheme) private var scheme
    @Environment(\.welcomeStill) private var still

    public init(model: OnboardingModel, started: @escaping () -> Void) {
        self.model = model
        self.started = started
    }

    public var body: some View {
        let theme = PixelTheme(scheme)
        Scrolling(still: still) {
            VStack(alignment: .leading, spacing: PixelMetrics.steps(5)) {
                PixelText(model.greeting, role: .title)
                    .foregroundStyle(theme.text.color)
                    .accessibilityAddTraits(.isHeader)
                Text("Your helper does the work for you. Pick how it looks and what it's called; you can change "
                    + "both later in Settings.")
                    .font(.subheadline)
                    .foregroundStyle(theme.text.color)
                VStack(alignment: .leading, spacing: PixelMetrics.steps(2)) {
                    PixelText("Name", role: .smallLabel)
                        .foregroundStyle(theme.accent.color)
                        .accessibilityAddTraits(.isHeader)
                    WelcomeField(placeholder: "Your helper's name", text: $model.helperName, still: still) {}
                        .accessibilityLabel("Your helper's name")
                }
                LookPicker(model: model.look, showsSave: false)
                if let problem = model.problem {
                    Text(problem)
                        .font(.subheadline)
                        .foregroundStyle(theme.accent.color)
                }
                WelcomeButton(title: model.starting ? "Starting…" : "Start chatting", enabled: model.canStart) {
                    Task { if await model.startChatting() { started() } }
                }
            }
            .padding(PixelMetrics.padding)
        }
        .background(theme.background.color.ignoresSafeArea())
    }
}

/// Over everything while the app is locked: the avatar and "Unlock". Face ID is asked for as soon as it shows.
public struct LockView: View {
    var lock: AppLock

    @Environment(\.colorScheme) private var scheme
    @Environment(\.welcomeStill) private var still

    public init(lock: AppLock) {
        self.lock = lock
    }

    public var body: some View {
        let theme = PixelTheme(scheme)
        VStack(spacing: PixelMetrics.steps(5)) {
            Spacer()
            WelcomeAvatar(still: still)
            PixelText("Marcel is locked", role: .title)
                .foregroundStyle(theme.text.color)
                .accessibilityAddTraits(.isHeader)
            if let problem = lock.problem {
                Text(problem)
                    .font(.subheadline)
                    .foregroundStyle(theme.accent.color)
            }
            WelcomeButton(title: "Unlock", enabled: true) { Task { await lock.unlock() } }
                .frame(maxWidth: 240)
            Spacer()
        }
        .padding(PixelMetrics.padding)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(theme.background.color.ignoresSafeArea())
        .task { if !still { await lock.unlock() } }
    }
}

/// A scroll view in the app; the plain content in snapshots (`ImageRenderer` does not draw a scroll view's content).
struct Scrolling<Content: View>: View {
    var still: Bool
    @ViewBuilder var content: Content

    var body: some View {
        if still { content } else { ScrollView { content } }
    }
}

/// The bundled giraffe, waving hello: the helper is not known yet on these screens.
struct WelcomeAvatar: View {
    var still: Bool

    var body: some View {
        Group {
            if still {
                AvatarFrameView(frame: SpriteSheet.giraffe.loop(for: .idle).firstFrame, size: .large)
            } else {
                AvatarView(state: .idle, size: .large)
            }
        }
        .accessibilityHidden(true)
    }
}

/// A text field in the pixel look; plain text in snapshots.
struct WelcomeField: View {
    var placeholder: String
    @Binding var text: String
    var still: Bool
    var submit: () -> Void

    @Environment(\.colorScheme) private var scheme

    var body: some View {
        let theme = PixelTheme(scheme)
        Group {
            if still {
                Text(text.isEmpty ? placeholder : text)
                    .foregroundStyle((text.isEmpty ? theme.muted : theme.text).color)
                    .frame(maxWidth: .infinity, alignment: .leading)
            } else {
                TextField(placeholder, text: $text)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .onSubmit(submit)
                    .foregroundStyle(theme.text.color)
            }
        }
        .padding(.horizontal, 12)
        .frame(minHeight: PixelMetrics.minTapTarget)
        .background(RoundedRectangle(cornerRadius: 10).fill(theme.card.color))
        .overlay(RoundedRectangle(cornerRadius: 10).strokeBorder(theme.line.color, lineWidth: 1))
    }
}

/// The screen's main button, filled, at least 44 pt tall.
struct WelcomeButton: View {
    var title: String
    var enabled: Bool
    var action: () -> Void

    @Environment(\.colorScheme) private var scheme

    var body: some View {
        let theme = PixelTheme(scheme)
        Button(action: action) {
            PixelText(title, role: .smallLabel)
                .foregroundStyle(theme.background.color)
                .frame(maxWidth: .infinity, minHeight: PixelMetrics.minTapTarget)
                .pixelBox(fill: theme.accent, line: theme.accent, shadow: theme.shadow, border: PixelMetrics.thinBorder,
                          offset: PixelMetrics.smallShadow)
                .opacity(enabled ? 1 : 0.5)
        }
        .buttonStyle(.plain)
        .disabled(!enabled)
        .accessibilityLabel(title)
    }
}
