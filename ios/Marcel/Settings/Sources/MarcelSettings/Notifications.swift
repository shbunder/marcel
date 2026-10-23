import Foundation
import MarcelKit
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

// MARK: Marcel's push relay (S-22.16)

/// Why the push relay could not be used. Any of these sends the phone to ntfy instead.
public enum RelayError: Error, Equatable {
    /// No answer, a timeout, or a 5xx.
    case unreachable
    /// 429: too many registrations from here, or for the whole relay today.
    case busy
    /// Any other refusal, with the relay's status and error code.
    case refused(Int, String?)
    /// An answer that is not the contract's `Registered`.
    case badAnswer
}

/// What the relay gave this phone (push-relay.yaml `Registered`), kept to hand to the hub and to forget again at sign
/// out. `apnsToken` is the token it was made for, so a launch with the same token does not register again.
public struct RelayRegistration: Codable, Equatable, Sendable {
    public var relayURL: String
    public var handle: String
    public var sendSecret: String
    public var apnsToken: String

    public init(relayURL: String, handle: String, sendSecret: String, apnsToken: String) {
        self.relayURL = relayURL
        self.handle = handle
        self.sendSecret = sendSecret
        self.apnsToken = apnsToken
    }
}

/// The relay's two phone routes (`contracts/push-relay.yaml`).
public protocol PushRelay: Sendable {
    /// `POST /v1/register`.
    func register(apnsToken: String, sandbox: Bool) async throws -> RelayRegistration
    /// `DELETE /v1/handles/{handle}`, with the handle's send secret as the bearer.
    func forget(_ registration: RelayRegistration) async throws
}

/// The live relay over HTTPS. `send` is `URLSession.shared` in the app and a fake in the tests. Nothing here logs:
/// the send secret and the handle never reach a log.
public struct LiveRelay: PushRelay {
    public typealias Send = @Sendable (URLRequest) async throws -> (Data, URLResponse)
    public let url: URL
    let send: Send

    public init(url: URL, send: Send? = nil) {
        self.url = url
        self.send = send ?? { try await URLSession.shared.data(for: $0) }
    }

    /// The build's relay address (`MARCEL_RELAY_URL` in `ios/Config/Relay.xcconfig`, written into Info.plist as
    /// `MarcelRelayURL`). Nil when the build has none, or it is not a plain `https://host[/path]` address.
    public static func configured(bundle: Bundle = .main) -> URL? {
        address(bundle.object(forInfoDictionaryKey: "MarcelRelayURL") as? String)
    }

    static func address(_ text: String?) -> URL? {
        guard var text = text?.trimmingCharacters(in: .whitespaces), !text.isEmpty else { return nil }
        while text.hasSuffix("/") { text.removeLast() }
        // The contract's `relay_url` pattern: `^https://[^/?#@]+(/[^?#]*)?$`.
        guard text.wholeMatch(of: /https:\/\/[^\/?#@\s]+(\/[^?#\s]*)?/) != nil, let url = URL(string: text),
              url.host() != nil
        else { return nil }
        return url
    }

    public func register(apnsToken: String, sandbox: Bool) async throws -> RelayRegistration {
        var request = URLRequest(url: url.appending(path: "v1/register"), timeoutInterval: 15)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: ["apns_token": apnsToken, "sandbox": sandbox])
        let data = try await answer(request, expecting: 201)
        guard let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let handle = json["handle"] as? String, Self.matches(handle, count: 22),
              let secret = json["send_secret"] as? String, Self.matches(secret, count: 43)
        else { throw RelayError.badAnswer }
        return RelayRegistration(relayURL: url.absoluteString, handle: handle, sendSecret: secret,
                                 apnsToken: apnsToken)
    }

    public func forget(_ registration: RelayRegistration) async throws {
        guard Self.matches(registration.handle, count: 22) else { return }
        var request = URLRequest(url: url.appending(path: "v1/handles/\(registration.handle)"), timeoutInterval: 10)
        request.httpMethod = "DELETE"
        request.setValue("Bearer \(registration.sendSecret)", forHTTPHeaderField: "Authorization")
        _ = try await answer(request, expecting: 204)
    }

    private func answer(_ request: URLRequest, expecting status: Int) async throws -> Data {
        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await send(request)
        } catch {
            throw RelayError.unreachable
        }
        guard let http = response as? HTTPURLResponse else { throw RelayError.badAnswer }
        switch http.statusCode {
        case status: return data
        case 429: throw RelayError.busy
        case 500...: throw RelayError.unreachable
        default:
            let code = (try? JSONSerialization.jsonObject(with: data) as? [String: Any])?["code"] as? String
            throw RelayError.refused(http.statusCode, code)
        }
    }

    /// base64url characters, exactly `count` of them (`handle`: 22, `send_secret`: 43).
    static func matches(_ text: String, count: Int) -> Bool {
        text.utf8.count == count && text.utf8.allSatisfy {
            (48...57).contains($0) || (65...90).contains($0) || (97...122).contains($0) || $0 == 95 || $0 == 45
        }
    }
}

/// The relay registration this phone holds, in the Keychain (the send secret is a password).
public struct RelayRegistrationStore: Sendable {
    let store: any TokenStore

    public init(store: any TokenStore = KeychainTokenStore(service: "com.shbunder.marcel.push-relay")) {
        self.store = store
    }

    public func load() -> RelayRegistration? {
        guard let text = try? store.token() else { return nil }
        return try? JSONDecoder().decode(RelayRegistration.self, from: Data(text.utf8))
    }

    public func save(_ registration: RelayRegistration) throws {
        try store.save(String(decoding: try JSONEncoder().encode(registration), as: UTF8.self))
    }

    public func delete() {
        try? store.delete()
    }
}

/// Everything the app needs to get pushes through the relay: the relay, whether this build gets Apple's sandbox
/// pushes, the phone's key pair and its saved registration.
public struct RelaySetup: Sendable {
    public let relay: any PushRelay
    /// True for a development build (APNs sandbox); TestFlight and App Store builds are false.
    public let sandbox: Bool
    public let keys: any PushKeyStore
    public let saved: RelayRegistrationStore

    public init(relay: any PushRelay, sandbox: Bool, keys: any PushKeyStore = KeychainPushKeyStore(),
                saved: RelayRegistrationStore = RelayRegistrationStore()) {
        self.relay = relay
        self.sandbox = sandbox
        self.keys = keys
        self.saved = saved
    }

    /// The live setup, or nil when this build has no relay address (`MARCEL_RELAY_URL` empty).
    public static func live(sandbox: Bool, bundle: Bundle = .main) -> RelaySetup? {
        LiveRelay.configured(bundle: bundle).map { RelaySetup(relay: LiveRelay(url: $0), sandbox: sandbox) }
    }

    /// Registers with the relay and gives the hub the target (`PUT /devices/{id}/push`, `kind: relay`). A relay
    /// failure throws `RelayError`; a hub failure throws its `SettingsError`, and the new handle is forgotten again.
    /// The key pair is made before anything is sent, so a Keychain failure spends no registration.
    @MainActor
    public func connect(apnsToken: String, deviceId: String, service: any SettingsService) async throws -> Device {
        let deviceKey = try keys.publicKey()
        let registration = try await relay.register(apnsToken: apnsToken, sandbox: sandbox)
        let device: Device
        do {
            device = try await service.setPush(deviceId: deviceId, target: .init(
                kind: .relay, relayUrl: registration.relayURL, handle: registration.handle,
                sendSecret: registration.sendSecret, deviceKey: deviceKey))
        } catch {
            try? await relay.forget(registration)
            throw error
        }
        let old = saved.load()
        try? saved.save(registration)
        if let old, old.handle != registration.handle { try? await relay.forget(old) }
        return device
    }

    /// Apple gave a running app a new token. Registers again only when this phone uses the relay and the token is not
    /// the one its registration was made for, so a normal launch spends no registration. False when it did nothing.
    @MainActor
    @discardableResult
    public func tokenChanged(_ token: String, deviceId: String, service: any SettingsService) async -> Bool {
        guard let saved = saved.load(), saved.apnsToken != token else { return false }
        return (try? await connect(apnsToken: token, deviceId: deviceId, service: service)) != nil
    }

    /// True when this phone registered with the relay (and has not signed out since).
    public var inUse: Bool { saved.load() != nil }

    /// Sign out: forgets the handle at the relay (best effort: a relay that is down never stops a sign out), the
    /// registration and the key pair.
    public func signOut() async {
        if let registration = saved.load() { try? await relay.forget(registration) }
        saved.delete()
        try? keys.delete()
    }
}
