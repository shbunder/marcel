import PixelKit
import SwiftUI
import UIKit

/// What covers the app (S-17.5): the lock while locked, a plain cover while the app is not in front (so the app
/// switcher's picture shows nothing private), or nothing. Only while paired: the welcome screen has nothing to hide.
public enum LockCover: Equatable, Sendable {
    case none
    case lock
    case privacy

    public static func of(locked: Bool, paired: Bool, phase: ScenePhase) -> LockCover {
        guard paired else { return .none }
        if locked { return .lock }
        return phase == .active ? .none : .privacy
    }
}

extension View {
    /// Covers the app with the lock in a window of its own, above every sheet and full-screen cover the app shows
    /// (an overlay would sit under them). Follows the scene: the lock counts the time away, and a plain cover hides
    /// the app in the app switcher.
    public func appLock(_ lock: AppLock, paired: Bool) -> some View {
        modifier(AppLockModifier(lock: lock, paired: paired))
    }
}

struct AppLockModifier: ViewModifier {
    var lock: AppLock
    var paired: Bool
    @Environment(\.scenePhase) private var phase
    @State private var window = LockWindow()

    func body(content: Content) -> some View {
        let cover = LockCover.of(locked: lock.locked, paired: paired, phase: phase)
        content
            // VoiceOver reads nothing underneath while covered.
            .accessibilityHidden(cover != .none)
            .onChange(of: phase) { _, phase in
                switch phase {
                case .background: lock.wentAway()
                case .active: lock.cameBack()
                default: break
                }
            }
            .onChange(of: cover, initial: true) { _, cover in window.show(cover, lock: lock) }
    }
}

/// The window the lock lives in, above the app's own (`windowLevel` alert + 1).
@MainActor
final class LockWindow {
    private(set) var window: UIWindow?

    func show(_ cover: LockCover, lock: AppLock) {
        guard cover != .none else {
            window?.isHidden = true
            window = nil
            return
        }
        let window = window ?? Self.make()
        let view: AnyView = cover == .lock ? AnyView(LockView(lock: lock)) : AnyView(PrivacyCover())
        window.rootViewController = UIHostingController(rootView: view)
        window.isHidden = false
        if cover == .lock { window.makeKey() }
        self.window = window
    }

    private static func make() -> UIWindow {
        let scene = UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }.first
        let window = scene.map(UIWindow.init(windowScene:)) ?? UIWindow(frame: UIScreen.main.bounds)
        window.windowLevel = .alert + 1
        window.accessibilityViewIsModal = true
        return window
    }
}

/// While the app is not in front: the background and the avatar, nothing private.
struct PrivacyCover: View {
    @Environment(\.colorScheme) private var scheme

    var body: some View {
        WelcomeAvatar(still: true)
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .background(ChatPalette(scheme).well.color.ignoresSafeArea())
    }
}
