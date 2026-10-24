import CryptoKit
import Foundation
import Security
import UserNotifications

// Pushes through Marcel's push relay (S-22.16, ADR-013 question 2). The hub seals each notification for this phone's
// X25519 key (app-api.yaml `SealedPush`); the relay passes the sealed bytes on to Apple inside an APNs payload whose
// visible alert is only "Marcel · You have news"; the Notification Service Extension opens them here with the private
// key from the Keychain. Nothing in this file logs: a push's words never reach a log.

/// A notification sealed for one phone (app-api.yaml `SealedPush`, the scheme `v: 1`).
public struct SealedPush: Codable, Equatable, Sendable {
    public var v: Int
    /// The hub's one-time X25519 public key, base64url.
    public var eph: String
    /// 12 bytes, base64url.
    public var nonce: String
    /// ChaCha20-Poly1305 ciphertext followed by its 16-byte tag, base64url.
    public var box: String

    public init(v: Int, eph: String, nonce: String, box: String) {
        self.v = v
        self.eph = eph
        self.nonce = nonce
        self.box = box
    }

    /// From an APNs payload's `sealed` key, as the relay delivers it.
    public init?(userInfo value: Any?) {
        guard let dict = value as? [String: Any], let v = dict["v"] as? Int, let eph = dict["eph"] as? String,
              let nonce = dict["nonce"] as? String, let box = dict["box"] as? String
        else { return nil }
        self.init(v: v, eph: eph, nonce: nonce, box: box)
    }
}

/// What a `SealedPush` holds: the notification the phone shows (app-api.yaml `SealedPushContent`).
public struct SealedPushContent: Codable, Equatable, Sendable {
    public var title: String
    public var body: String
    /// Where a tap goes (`PushNotification.deep_link`).
    public var link: String
    /// The buddy it is about; nil for a notice about Marcel as a whole.
    public var agentId: String?
    /// `approval`, `task`, `broke`, `digest` or `note`.
    public var category: String?

    enum CodingKeys: String, CodingKey {
        case title, body, link, category
        case agentId = "agent_id"
    }

    public init(title: String, body: String, link: String, agentId: String? = nil, category: String? = nil) {
        self.title = title
        self.body = body
        self.link = link
        self.agentId = agentId
        self.category = category
    }

    /// The contract's categories (`SealedPushContent.category`).
    public static let categories: Set<String> = ["approval", "task", "broke", "digest", "note"]

    /// True when every field is as the contract allows: title ≤ 80, body ≤ 300, a `marcel://` link of the
    /// contract's pattern, an id-shaped buddy, a known category.
    public var isValid: Bool {
        guard title.count <= 80, body.count <= 300, Self.isLink(link) else { return false }
        if let agentId, !Self.isId(agentId) { return false }
        if let category, !Self.categories.contains(category) { return false }
        return true
    }

    /// The contract's `link` pattern:
    /// `^marcel://((task|message|approval|artifact)/[A-Za-z0-9_-]+|project/[a-z0-9][a-z0-9-]{0,39}|status)$`.
    static func isLink(_ link: String) -> Bool {
        link.wholeMatch(of: /marcel:\/\/((task|message|approval|artifact)\/[A-Za-z0-9_\-]+|project\/[a-z0-9][a-z0-9\-]{0,39}|status)/) != nil
    }

    static func isId(_ id: String) -> Bool {
        (1...128).contains(id.utf8.count) && id.utf8.allSatisfy {
            (48...57).contains($0) || (65...90).contains($0) || (97...122).contains($0) || $0 == 95 || $0 == 45
        }
    }

    /// The id a link names when it is `marcel://<kind>/<id>`.
    func id(in kind: String) -> String? {
        let prefix = "marcel://\(kind)/"
        guard link.hasPrefix(prefix) else { return nil }
        return String(link.dropFirst(prefix.count))
    }
}

/// Opens a `SealedPush` (app-api.yaml `SealedPush`, steps 1–3, done with the phone's private key).
public enum PushSeal {
    /// The HKDF info and the AEAD's associated data: the ASCII bytes `marcel-push-v1`.
    public static let label = Data("marcel-push-v1".utf8)

    public enum Failure: Error, Equatable {
        /// `v` is not 1: a scheme this build does not know.
        case unknownVersion
        /// `eph`, `nonce` or `box` is not base64url of the right length.
        case badEncoding
        /// The box does not open with this key (corrupted, or sealed for another phone).
        case doesNotOpen
        /// It opened, but what is inside is not a `SealedPushContent` the contract allows.
        case badContent
    }

    public static func open(_ sealed: SealedPush, with key: Curve25519.KeyAgreement.PrivateKey) throws
        -> SealedPushContent
    {
        guard sealed.v == 1 else { throw Failure.unknownVersion }
        guard let eph = Base64URL.decode(sealed.eph), eph.count == 32,
              let nonceBytes = Base64URL.decode(sealed.nonce), nonceBytes.count == 12,
              let box = Base64URL.decode(sealed.box), box.count >= 16,
              let ephKey = try? Curve25519.KeyAgreement.PublicKey(rawRepresentation: eph),
              let nonce = try? ChaChaPoly.Nonce(data: nonceBytes)
        else { throw Failure.badEncoding }
        let plain: Data
        do {
            let shared = try key.sharedSecretFromKeyAgreement(with: ephKey)
            let symmetric = shared.hkdfDerivedSymmetricKey(
                using: SHA256.self, salt: eph + key.publicKey.rawRepresentation, sharedInfo: label,
                outputByteCount: 32)
            let sealedBox = try ChaChaPoly.SealedBox(nonce: nonce, ciphertext: box.dropLast(16), tag: box.suffix(16))
            plain = try ChaChaPoly.open(sealedBox, using: symmetric, authenticating: label)
        } catch {
            throw Failure.doesNotOpen
        }
        guard let content = try? JSONDecoder().decode(SealedPushContent.self, from: plain), content.isValid else {
            throw Failure.badContent
        }
        return content
    }
}

/// base64url without padding, as the contract writes keys, nonces and boxes.
public enum Base64URL {
    public static func encode(_ data: Data) -> String {
        data.base64EncodedString().replacingOccurrences(of: "+", with: "-").replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
    }

    public static func decode(_ text: String) -> Data? {
        guard text.utf8.allSatisfy({
            (48...57).contains($0) || (65...90).contains($0) || (97...122).contains($0) || $0 == 95 || $0 == 45
        }) else { return nil }
        var base64 = text.replacingOccurrences(of: "-", with: "+").replacingOccurrences(of: "_", with: "/")
        base64 += String(repeating: "=", count: (4 - base64.count % 4) % 4)
        return Data(base64Encoded: base64)
    }
}

/// Where this phone's push key pair lives. The private half never leaves the Keychain; only the public half
/// (`PushTarget.device_key`) goes to the hub.
public protocol PushKeyStore: Sendable {
    /// The public key as base64url, making the key pair the first time.
    func publicKey() throws -> String
    /// The private key, or nil when this phone has none (never turned relay pushes on, or signed out).
    func privateKey() throws -> Curve25519.KeyAgreement.PrivateKey?
    /// Forgets the key pair (sign out).
    func delete() throws
}

/// The key pair in the Keychain, in the access group the app shares with its Notification Service Extension
/// (`$(AppIdentifierPrefix)marcel.push`, `Config/Marcel.entitlements`), readable after the first unlock and on this
/// device only, so a push that arrives while the phone is locked still opens and the key is never in a backup.
public struct KeychainPushKeyStore: PushKeyStore {
    public static let defaultService = "com.shbunder.marcel.push-key"
    /// The Info.plist key that holds the shared group (`$(AppIdentifierPrefix)marcel.push`, set at build time).
    public static let groupInfoKey = "MarcelPushKeychainGroup"

    public let service: String
    public let account: String
    /// The Keychain access group; nil uses the app's own default group.
    public let accessGroup: String?

    public init(service: String = KeychainPushKeyStore.defaultService, account: String = "x25519",
                accessGroup: String? = KeychainPushKeyStore.sharedGroup()) {
        self.service = service
        self.account = account
        self.accessGroup = accessGroup
    }

    /// The shared group as the build wrote it into Info.plist. Nil when the build had no team (an unsigned simulator
    /// build: `$(AppIdentifierPrefix)` is empty, and a group without its team prefix is not one the app has).
    public static func sharedGroup(bundle: Bundle = .main) -> String? {
        group(from: bundle.object(forInfoDictionaryKey: groupInfoKey) as? String)
    }

    static func group(from value: String?) -> String? {
        guard let value, value.hasSuffix(".marcel.push"), value.count > ".marcel.push".count else { return nil }
        return value
    }

    private var query: [String: Any] {
        var query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
        if let accessGroup { query[kSecAttrAccessGroup as String] = accessGroup }
        return query
    }

    public func privateKey() throws -> Curve25519.KeyAgreement.PrivateKey? {
        var request = query
        request[kSecReturnData as String] = true
        request[kSecMatchLimit as String] = kSecMatchLimitOne
        var item: CFTypeRef?
        let status = SecItemCopyMatching(request as CFDictionary, &item)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess, let data = item as? Data else { throw KeychainError(status: status) }
        return try Curve25519.KeyAgreement.PrivateKey(rawRepresentation: data)
    }

    public func publicKey() throws -> String {
        if let existing = try privateKey() { return Base64URL.encode(existing.publicKey.rawRepresentation) }
        let made = Curve25519.KeyAgreement.PrivateKey()
        var item = query
        item[kSecValueData as String] = made.rawRepresentation
        item[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        let status = SecItemAdd(item as CFDictionary, nil)
        guard status == errSecSuccess else { throw KeychainError(status: status) }
        return Base64URL.encode(made.publicKey.rawRepresentation)
    }

    public func delete() throws {
        let status = SecItemDelete(query as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else { throw KeychainError(status: status) }
    }
}

/// What the Notification Service Extension does with a push (`ios/NotificationService/`). Here so it is tested.
public enum SealedNotification {
    /// What a relay push shows when it cannot be opened: the relay's own visible alert.
    public static let fallbackTitle = "Marcel"
    public static let fallbackBody = "You have news"

    /// The payload keys the app reads (`PushPayload`) that only an opened box may set.
    static let routedKeys = ["deep_link", "approval_id", "task_id", "agent_id", "level", "sealed"]

    public enum Outcome: Equatable, Sendable {
        /// Not a relay push (no `sealed` key): left exactly as it came.
        case notSealed
        /// Opened: the real title, body, link, buddy and category.
        case opened
        /// A relay push that did not open: "Marcel · You have news", leading nowhere.
        case fallback
    }

    /// Fills `content` from the sealed push in its `userInfo`. On any failure it shows "You have news" and drops every
    /// key that routes a tap, so a push that is not sealed for this phone can neither speak nor lead anywhere.
    @discardableResult
    public static func open(_ content: UNMutableNotificationContent, keys: any PushKeyStore) -> Outcome {
        guard let raw = content.userInfo["sealed"] else { return .notSealed }
        guard let sealed = SealedPush(userInfo: raw), let key = try? keys.privateKey(),
              let opened = try? PushSeal.open(sealed, with: key)
        else {
            content.title = fallbackTitle
            content.subtitle = ""
            content.body = fallbackBody
            content.categoryIdentifier = ""
            content.userInfo = strip(content.userInfo)
            return .fallback
        }
        content.title = opened.title
        content.body = opened.body
        content.categoryIdentifier = opened.category ?? ""
        var info = strip(content.userInfo)
        info["deep_link"] = opened.link
        if let agentId = opened.agentId { info["agent_id"] = agentId }
        // Approve and Deny answer the approval the link names (`PushPayload.approvalId`).
        if let approvalId = opened.id(in: "approval") { info["approval_id"] = approvalId }
        if let taskId = opened.id(in: "task") { info["task_id"] = taskId }
        content.userInfo = info
        return .opened
    }

    private static func strip(_ info: [AnyHashable: Any]) -> [AnyHashable: Any] {
        info.filter { key, _ in !routedKeys.contains(where: { AnyHashable($0) == key }) }
    }
}
