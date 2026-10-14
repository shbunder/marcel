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

/// The welcome screen (S-17.5, wireframe Q6): the avatar, "Welcome to Marcel" in bold, how to get in, the scanner in
/// a dashed rose frame, a paste-a-link fallback, and what to do without a code. `pair` gets what was scanned or pasted and throws the reason it did not work.
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
        let palette = ChatPalette(scheme)
        Scrolling(still: still) {
            VStack(spacing: PixelMetrics.steps(5)) {
                WelcomeAvatar(still: still)
                WelcomeTitle(text: "Welcome to Marcel")
                Text("Scan the code shown on the phone of the person who runs Marcel at home.")
                    .font(.subheadline)
                    .multilineTextAlignment(.center)
                    .foregroundStyle(palette.muted.color)
                    .frame(maxWidth: 300)
                Group {
                    if still {
                        palette.card.color
                    } else {
                        QRScannerView { code in submit(code) }
                    }
                }
                .frame(width: 220, height: 220)
                .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: 16, style: .continuous)
                    .strokeBorder(ChatCardKind.done.frame(in: palette).color,
                                  style: StrokeStyle(lineWidth: 2, dash: [6, 4])))
                .accessibilityLabel("Camera, looking for the code")
                VStack(alignment: .leading, spacing: PixelMetrics.steps(2)) {
                    Text("Or paste the link you were sent")
                        .font(.subheadline)
                        .foregroundStyle(palette.muted.color)
                    WelcomeField(placeholder: "marcel://pair?…", text: $pasted, still: still) { submit(pasted) }
                    WelcomeButton(title: working ? "Signing in…" : "Sign in", enabled: !working && !pasted.isEmpty) {
                        submit(pasted)
                    }
                }
                if let problem {
                    Text(problem)
                        .font(.subheadline)
                        .foregroundStyle(ChatCardKind.failed.label(in: palette).color)
                        .multilineTextAlignment(.center)
                }
                Text("No code? Ask the owner to add you.")
                    .font(.footnote)
                    .foregroundStyle(palette.muted.color)
                    .multilineTextAlignment(.center)
            }
            .padding(PixelMetrics.padding)
        }
        .background(palette.well.color.ignoresSafeArea())
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

/// "Hi Emma! Meet your helper" (S-17.5, wireframe Q6): the greeting in bold, the helper's name, the look picker, and
/// "Start chatting" as a rose pill.
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
        let palette = ChatPalette(scheme)
        Scrolling(still: still) {
            VStack(alignment: .leading, spacing: PixelMetrics.steps(5)) {
                WelcomeTitle(text: model.greeting)
                Text("Your helper does the work for you. Pick how it looks and what it's called; you can change "
                    + "both later in Settings.")
                    .font(.subheadline)
                    .foregroundStyle(palette.muted.color)
                    .multilineTextAlignment(.center)
                    .frame(maxWidth: .infinity)
                VStack(alignment: .leading, spacing: PixelMetrics.steps(2)) {
                    PixelText("Name", role: .smallLabel)
                        .foregroundStyle(palette.muted.color)
                        .accessibilityAddTraits(.isHeader)
                    WelcomeField(placeholder: "Your helper's name", text: $model.helperName, still: still) {}
                        .accessibilityLabel("Your helper's name")
                }
                LookPicker(model: model.look, showsSave: false)
                if let problem = model.problem {
                    Text(problem)
                        .font(.subheadline)
                        .foregroundStyle(ChatCardKind.failed.label(in: palette).color)
                }
                WelcomeButton(title: model.starting ? "Starting…" : "Start chatting", enabled: model.canStart) {
                    Task { if await model.startChatting() { started() } }
                }
            }
            .padding(PixelMetrics.padding)
        }
        .background(palette.well.color.ignoresSafeArea())
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
        let palette = ChatPalette(scheme)
        VStack(spacing: PixelMetrics.steps(5)) {
            Spacer()
            WelcomeAvatar(still: still)
            WelcomeTitle(text: "Marcel is locked")
            if let problem = lock.problem {
                Text(problem)
                    .font(.subheadline)
                    .foregroundStyle(ChatCardKind.failed.label(in: palette).color)
            }
            WelcomeButton(title: "Unlock", enabled: true) { Task { await lock.unlock() } }
                .frame(maxWidth: 240)
            Spacer()
        }
        .padding(PixelMetrics.padding)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(palette.well.color.ignoresSafeArea())
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

/// A screen's title in bold, centred (wireframe Q6: "Welcome to Marcel").
struct WelcomeTitle: View {
    var text: String

    @Environment(\.colorScheme) private var scheme

    var body: some View {
        Text(text)
            .font(.title3.weight(.bold))
            .foregroundStyle(ChatPalette(scheme).ink.color)
            .multilineTextAlignment(.center)
            .frame(maxWidth: .infinity)
            .accessibilityAddTraits(.isHeader)
    }
}

/// A text field in the chat look; plain text in snapshots.
struct WelcomeField: View {
    var placeholder: String
    @Binding var text: String
    var still: Bool
    var submit: () -> Void

    @Environment(\.colorScheme) private var scheme

    var body: some View {
        let palette = ChatPalette(scheme)
        Group {
            if still {
                Text(text.isEmpty ? placeholder : text)
                    .foregroundStyle((text.isEmpty ? palette.muted : palette.ink).color)
                    .frame(maxWidth: .infinity, alignment: .leading)
            } else {
                TextField(placeholder, text: $text)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .onSubmit(submit)
                    .foregroundStyle(palette.ink.color)
            }
        }
        .padding(.horizontal, 14)
        .frame(minHeight: PixelMetrics.minTapTarget)
        .background(Capsule().fill(palette.card.color))
        .overlay(Capsule().strokeBorder(palette.line.color, lineWidth: 1))
    }
}

/// The screen's main button (wireframe Q6): a rose pill with bold white words, at least 44 pt tall.
struct WelcomeButton: View {
    var title: String
    var enabled: Bool
    var action: () -> Void

    @Environment(\.colorScheme) private var scheme

    var body: some View {
        let palette = ChatPalette(scheme)
        Button(action: action) {
            Text(title)
                .font(.body.weight(.bold))
                .foregroundStyle(ChatCardKind.done.buttonText(in: palette).color)
                .padding(.horizontal, 24)
                .frame(minHeight: PixelMetrics.minTapTarget)
                .background(ChatCardKind.done.label(in: palette).color, in: Capsule())
                .opacity(enabled ? 1 : 0.5)
                .frame(maxWidth: .infinity)
        }
        .buttonStyle(.plain)
        .disabled(!enabled)
        .accessibilityLabel(title)
    }
}
