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
    private var waiting: [CheckedContinuation<String, any Error>] = []

    public init() {}

    public func token() async throws -> String {
        let center = UNUserNotificationCenter.current()
        let allowed = (try? await center.requestAuthorization(options: [.alert, .sound, .badge])) ?? false
        guard allowed else { throw PushSetupError.notAllowed }
        return try await withCheckedThrowingContinuation { continuation in
            waiting.append(continuation)
            UIApplication.shared.registerForRemoteNotifications()
            // Apple answers within seconds, or not at all on a build without the entitlement.
            Task { [weak self] in
                try? await Task.sleep(for: .seconds(20))
                self?.finish(.failure(PushSetupError.apnsUnavailable))
            }
        }
    }

    public func didRegister(deviceToken: Data) {
        let token = deviceToken.map { String(format: "%02x", $0) }.joined()
        let isNew = token != lastToken
        lastToken = token
        if waiting.isEmpty, isNew { onToken?(token) }
        finish(.success(token))
    }

    public func didFail(_ error: any Error) {
        finish(.failure(PushSetupError.apnsUnavailable))
    }

    private func finish(_ result: Result<String, any Error>) {
        let all = waiting
        waiting = []
        all.forEach { $0.resume(with: result) }
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
