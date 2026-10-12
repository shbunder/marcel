import Foundation
import UIKit
import UserNotifications

/// Gets an APNs device token: asks the person for permission, registers with Apple, and waits for the answer the app
/// delegate passes on. Fails when this build has no push entitlement (no Apple developer account yet, B-18).
@MainActor
public protocol PushTokenSource {
    func token() async throws -> String
}

/// Why notifications could not be turned on.
public enum PushSetupError: Error, Equatable, LocalizedError {
    /// The person said no (or turned them off in iOS Settings).
    case notAllowed
    /// Apple's push service refused this build (no entitlement) or did not answer.
    case apnsUnavailable

    public var errorDescription: String? {
        switch self {
        case .notAllowed: "Notifications are off for Marcel. Turn them on in the iPhone's Settings app, then try again."
        case .apnsUnavailable: "This build of the app can't use Apple's push service."
        }
    }
}

/// The live token source. The app delegate forwards `didRegisterForRemoteNotificationsWithDeviceToken` and
/// `didFailToRegisterForRemoteNotificationsWithError` here.
@MainActor
public final class APNsRegistrar: PushTokenSource {
    public static let shared = APNsRegistrar()
    /// The last token Apple gave this phone, hex.
    public private(set) var lastToken: String?
    /// Called with every new token after the first registration, so the hub always has the current one.
    public var onToken: ((String) -> Void)?
    /// Attempts waiting for Apple's answer, each with its own id so its timeout fails only that attempt.
    private var waiting: [UUID: CheckedContinuation<String, any Error>] = [:]

    private let authorize: @MainActor () async -> Bool
    private let register: @MainActor () -> Void
    private let timeout: Duration

    /// - Parameters:
    ///   - authorize: asks the person for permission (the system prompt by default).
    ///   - register: asks Apple for a token (`registerForRemoteNotifications` by default).
    ///   - timeout: how long one attempt waits. Apple answers within seconds, or not at all on a build without the
    ///     push entitlement.
    public init(authorize: (@MainActor () async -> Bool)? = nil, register: (@MainActor () -> Void)? = nil,
                timeout: Duration = .seconds(20)) {
        self.authorize = authorize ?? {
            (try? await UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound, .badge]))
                ?? false
        }
        self.register = register ?? { UIApplication.shared.registerForRemoteNotifications() }
        self.timeout = timeout
    }

    public func token() async throws -> String {
        guard await authorize() else { throw PushSetupError.notAllowed }
        let attempt = UUID()
        let timeout = timeout
        return try await withCheckedThrowingContinuation { continuation in
            waiting[attempt] = continuation
            register()
            Task { [weak self] in
                try? await Task.sleep(for: timeout)
                // Only this attempt: an earlier attempt's timer must never fail a later one.
                self?.waiting.removeValue(forKey: attempt)?.resume(throwing: PushSetupError.apnsUnavailable)
            }
        }
    }

    public func didRegister(deviceToken: Data) {
        let token = APNsToken.hex(deviceToken)
        let isNew = token != lastToken
        lastToken = token
        if waiting.isEmpty, isNew { onToken?(token) }
        finish(.success(token))
    }

    public func didFail(_ error: any Error) {
        finish(.failure(PushSetupError.apnsUnavailable))
    }

    private func finish(_ result: Result<String, any Error>) {
        let all = waiting.values
        waiting = [:]
        all.forEach { $0.resume(with: result) }
    }
}

/// The APNs device token as the hub takes it (`PushTarget.apns_token`, app API 0.6): hex, 64 to 200 digits.
public enum APNsToken {
    /// Apple's token bytes as lower-case hex, two digits a byte.
    public static func hex(_ token: Data) -> String {
        token.map { String(format: "%02x", $0) }.joined()
    }

    /// Matches the contract's pattern `^[0-9a-fA-F]{64,200}$`, as the hub checks it.
    public static func isValid(_ token: String?) -> Bool {
        guard let token, (64...200).contains(token.utf8.count) else { return false }
        return token.utf8.allSatisfy { (48...57).contains($0) || (65...70).contains($0) || (97...102).contains($0) }
    }
}

/// The ntfy topic this phone listens on (B-18). Made once, long and random: anyone who knows a topic can read what is
/// sent to it, so it is never guessable and only shown in Settings.
public struct NtfyTopicStore: @unchecked Sendable {
    public static let key = "settings.ntfyTopic"
    private let defaults: UserDefaults

    public init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
    }

    public var saved: String? { defaults.string(forKey: Self.key) }

    /// Forgets the topic (on unpairing): the next hub gets a new one.
    public func clear() {
        defaults.removeObject(forKey: Self.key)
    }

    public func topic() -> String {
        if let saved { return saved }
        let made = Self.make()
        defaults.set(made, forKey: Self.key)
        return made
    }

    /// `marcel-` and 24 random lower-case letters and digits (about 124 bits).
    public static func make() -> String {
        let alphabet = Array("abcdefghijklmnopqrstuvwxyz0123456789")
        var generator = SystemRandomNumberGenerator()
        return "marcel-" + String((0..<24).map { _ in alphabet.randomElement(using: &generator)! })
    }
}

/// Copies the ntfy topic for pasting into the ntfy app: this device only (never Universal Clipboard to a Mac or
/// iPad) and gone after two minutes, since anyone with the topic can read the notifications.
public enum TopicClipboard {
    public static let lifetime: TimeInterval = 120

    public static func options(now: Date = .now) -> [UIPasteboard.OptionsKey: Any] {
        [.localOnly: true, .expirationDate: now.addingTimeInterval(lifetime)]
    }

    public static func copy(_ topic: String, to pasteboard: UIPasteboard = .general, now: Date = .now) {
        pasteboard.setItems([["public.utf8-plain-text": topic]], options: options(now: now))
    }
}
