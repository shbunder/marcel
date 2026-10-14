import Foundation
import LocalAuthentication
import Observation

/// Face ID (or the passcode) before the app opens (S-17.5, B-39): on by default, asked at launch and again after
/// five minutes in the background. Not after four: a quick look at another app does not lock it.
@MainActor
@Observable
public final class AppLock {
    /// How long in the background before the app locks again.
    public static let after: TimeInterval = 5 * 60

    public private(set) var locked: Bool
    /// Why the last unlock did not work, in plain words.
    public private(set) var problem: String?
    /// On by default; Settings turns it off (S-17.6).
    public var enabled: Bool {
        get { defaults.object(forKey: Self.enabledKey) as? Bool ?? true }
        set {
            defaults.set(newValue, forKey: Self.enabledKey)
            if !newValue { locked = false }
        }
    }

    @ObservationIgnored private let authenticator: any Authenticator
    @ObservationIgnored private let now: () -> TimeInterval
    @ObservationIgnored private let defaults: UserDefaults
    @ObservationIgnored private var leftAt: TimeInterval?

    static let enabledKey = "lock.enabled"

    /// - Parameter now: seconds on a clock that keeps running while the phone sleeps and that changing the phone's
    ///   time does not move (`AppLock.uptime`), so setting the clock back cannot dodge the lock.
    public init(authenticator: any Authenticator = DeviceAuthenticator(), now: @escaping () -> TimeInterval = AppLock.uptime,
                defaults: UserDefaults = .standard) {
        self.authenticator = authenticator
        self.now = now
        self.defaults = defaults
        let on = defaults.object(forKey: Self.enabledKey) as? Bool ?? true
        locked = on && authenticator.canAuthenticate()
    }

    /// The app went to the background.
    public func wentAway() {
        if leftAt == nil { leftAt = now() }
    }

    /// The app is back in front: locked again when it was away five minutes or more.
    public func cameBack() {
        defer { leftAt = nil }
        guard let leftAt, enabled, authenticator.canAuthenticate() else { return }
        if now() - leftAt >= Self.after { locked = true }
    }

    /// Seconds since this clock's start; `ContinuousClock` keeps counting while the phone sleeps.
    public static let uptime: @Sendable () -> TimeInterval = {
        let start = ContinuousClock.now
        return {
            let elapsed = ContinuousClock.now - start
            return Double(elapsed.components.seconds) + Double(elapsed.components.attoseconds) / 1e18
        }
    }()

    /// The person just signed in on this phone: no lock straight after pairing.
    public func signedIn() {
        locked = false
        leftAt = nil
    }

    /// Asks for Face ID or the passcode.
    public func unlock() async {
        guard locked else { return }
        if await authenticator.authenticate(reason: "Open Marcel") {
            locked = false
            problem = nil
        } else {
            problem = "Not unlocked. Try again."
        }
    }
}

/// Face ID, Touch ID or the passcode; tests pass a fake.
public protocol Authenticator: Sendable {
    /// False on a phone with no passcode: there is nothing to lock with.
    func canAuthenticate() -> Bool
    func authenticate(reason: String) async -> Bool
}

/// The phone's own: Face ID, falling back to the passcode (`deviceOwnerAuthentication`).
public struct DeviceAuthenticator: Authenticator {
    public init() {}

    public func canAuthenticate() -> Bool {
        LAContext().canEvaluatePolicy(.deviceOwnerAuthentication, error: nil)
    }

    public func authenticate(reason: String) async -> Bool {
        (try? await LAContext().evaluatePolicy(.deviceOwnerAuthentication, localizedReason: reason)) ?? false
    }
}
