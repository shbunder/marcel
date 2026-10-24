import CryptoKit
import Foundation
import UserNotifications
import XCTest

@testable import MarcelKit

/// S-22.16: the phone opens pushes sealed for it (app-api.yaml `SealedPush`), with the contract's own known-answer
/// vector (`contracts/tests/fixtures/sealed-push/vector.json`, read from the repo, not copied), and anything that does
/// not open shows "You have news" and leads nowhere.
final class PushKeyTests: XCTestCase {
    struct Vector: Decodable {
        var device_private_key: String
        var device_key: String
        var plaintext: String
        var content: SealedPushContent
        var sealed: SealedPush
    }

    static let vectorURL = URL(fileURLWithPath: #filePath)
        .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
        .deletingLastPathComponent().deletingLastPathComponent()
        .appending(path: "contracts/tests/fixtures/sealed-push/vector.json")

    static func vector() throws -> Vector {
        try JSONDecoder().decode(Vector.self, from: Data(contentsOf: vectorURL))
    }

    static func key(_ v: Vector) throws -> Curve25519.KeyAgreement.PrivateKey {
        try Curve25519.KeyAgreement.PrivateKey(rawRepresentation: XCTUnwrap(Base64URL.decode(v.device_private_key)))
    }

    /// A key store holding one fixed key, in memory.
    final class FixedKey: PushKeyStore, @unchecked Sendable {
        var key: Curve25519.KeyAgreement.PrivateKey?
        var failing = false
        init(_ key: Curve25519.KeyAgreement.PrivateKey?) { self.key = key }
        func publicKey() throws -> String { Base64URL.encode(key!.publicKey.rawRepresentation) }
        func privateKey() throws -> Curve25519.KeyAgreement.PrivateKey? {
            if failing { throw KeychainError(status: errSecInteractionNotAllowed) }
            return key
        }
        func delete() throws { key = nil }
    }

    /// The relay's APNs payload around a sealed push (push-relay.yaml): only "Marcel · You have news" shows.
    static func delivered(_ sealed: [String: Any]?) -> UNMutableNotificationContent {
        let content = UNMutableNotificationContent()
        content.title = "Marcel"
        content.body = "You have news"
        var info: [AnyHashable: Any] = ["aps": ["alert": ["title": "Marcel", "body": "You have news"],
                                                "mutable-content": 1]]
        if let sealed { info["sealed"] = sealed }
        content.userInfo = info
        return content
    }

    static func json(_ sealed: SealedPush) -> [String: Any] {
        ["v": sealed.v, "eph": sealed.eph, "nonce": sealed.nonce, "box": sealed.box]
    }

    // MARK: The known-answer vector

    func testTheContractsVectorOpensWithItsFixedKey() throws {
        let v = try Self.vector()
        let key = try Self.key(v)
        XCTAssertEqual(Base64URL.encode(key.publicKey.rawRepresentation), v.device_key, "the vector's own key pair")
        XCTAssertEqual(try PushSeal.open(v.sealed, with: key), v.content)
        XCTAssertEqual(v.content, SealedPushContent(title: "Pip needs you", body: "Allow `npm install` in garden-planner?",
                                                    link: "marcel://approval/apr_01J9ZR2", agentId: "agt_01JA3E2",
                                                    category: "approval"))
    }

    func testTheExtensionShowsTheRealTitleBodyAndLink() throws {
        let v = try Self.vector()
        let content = Self.delivered(Self.json(v.sealed))
        XCTAssertEqual(SealedNotification.open(content, keys: FixedKey(try Self.key(v))), .opened)
        XCTAssertEqual(content.title, "Pip needs you")
        XCTAssertEqual(content.body, "Allow `npm install` in garden-planner?")
        XCTAssertEqual(content.categoryIdentifier, "approval", "so Approve and Deny show")
        XCTAssertEqual(content.userInfo["deep_link"] as? String, "marcel://approval/apr_01J9ZR2")
        XCTAssertEqual(content.userInfo["agent_id"] as? String, "agt_01JA3E2")
        XCTAssertEqual(content.userInfo["approval_id"] as? String, "apr_01J9ZR2", "what Approve answers")
        XCTAssertNil(content.userInfo["sealed"], "the box is not kept")
        XCTAssertNotNil(content.userInfo["aps"])
    }

    // MARK: Anything that does not open

    func testACorruptedBoxFallsBackToYouHaveNews() throws {
        let v = try Self.vector()
        var box = Array(v.sealed.box)
        box[10] = box[10] == "A" ? "B" : "A"
        var corrupted = v.sealed
        corrupted.box = String(box)
        XCTAssertThrowsError(try PushSeal.open(corrupted, with: try Self.key(v))) {
            XCTAssertEqual($0 as? PushSeal.Failure, .doesNotOpen)
        }
        let content = Self.delivered(Self.json(corrupted))
        content.userInfo["deep_link"] = "marcel://approval/apr_forged"
        content.userInfo["approval_id"] = "apr_forged"
        content.categoryIdentifier = "approval"
        XCTAssertEqual(SealedNotification.open(content, keys: FixedKey(try Self.key(v))), .fallback)
        XCTAssertEqual(content.title, "Marcel")
        XCTAssertEqual(content.body, "You have news")
        XCTAssertEqual(content.categoryIdentifier, "", "no Approve or Deny on a push that did not open")
        for key in ["deep_link", "approval_id", "task_id", "agent_id", "sealed"] {
            XCTAssertNil(content.userInfo[key], "\(key): a push that did not open leads nowhere")
        }
    }

    func testEveryOtherFailureFallsBackToo() throws {
        let v = try Self.vector()
        let key = try Self.key(v)
        var otherVersion = v.sealed
        otherVersion.v = 2
        var shortNonce = v.sealed
        shortNonce.nonce = "LMmDNiZF6G2z"
        var notBase64 = v.sealed
        notBase64.eph = "v7px1ONb5JHvx+TbJRP/rbBg-ZXscLCXlhlyV4X-jBc"
        var truncated = v.sealed
        truncated.box = String(v.sealed.box.prefix(20))
        let cases: [(String, [String: Any]?, any PushKeyStore)] = [
            ("another phone's key", Self.json(v.sealed), FixedKey(Curve25519.KeyAgreement.PrivateKey())),
            ("no key on this phone (signed out)", Self.json(v.sealed), FixedKey(nil)),
            ("a version this build does not know", Self.json(otherVersion), FixedKey(key)),
            ("a short nonce", Self.json(shortNonce), FixedKey(key)),
            ("not base64url", Self.json(notBase64), FixedKey(key)),
            ("a box shorter than its tag", Self.json(truncated), FixedKey(key)),
            ("not an object", nil, FixedKey(key)),
        ]
        for (name, sealed, keys) in cases {
            let content = Self.delivered(sealed)
            if sealed == nil { content.userInfo["sealed"] = "garbage" }
            XCTAssertEqual(SealedNotification.open(content, keys: keys), .fallback, name)
            XCTAssertEqual(content.body, "You have news", name)
        }
        let locked = FixedKey(key)
        locked.failing = true
        let content = Self.delivered(Self.json(v.sealed))
        XCTAssertEqual(SealedNotification.open(content, keys: locked), .fallback, "the Keychain refused")
    }

    /// A box that opens but holds what the contract does not allow (a link elsewhere, too long, an unknown category).
    func testContentTheContractDoesNotAllowFallsBack() throws {
        let key = Curve25519.KeyAgreement.PrivateKey()
        let bad: [String: Any] = ["title": "Hi", "body": "Tap", "link": "https://evil.example/"]
        let sealed = try Self.seal(bad, for: key.publicKey)
        XCTAssertThrowsError(try PushSeal.open(sealed, with: key)) { XCTAssertEqual($0 as? PushSeal.Failure, .badContent) }
        for content in [
            ["title": String(repeating: "x", count: 81), "body": "b", "link": "marcel://status"],
            ["title": "t", "body": String(repeating: "x", count: 301), "link": "marcel://status"],
            ["title": "t", "body": "b", "link": "marcel://status", "category": "phish"],
            ["title": "t", "body": "b", "link": "marcel://pair?hub=x"],
            ["title": "t", "body": "b", "link": "marcel://status", "agent_id": "../x"],
            ["title": "t", "body": "b"],
        ] {
            XCTAssertThrowsError(try PushSeal.open(try Self.seal(content, for: key.publicKey), with: key), "\(content)")
        }
    }

    func testAPushThatIsNotSealedIsLeftAsItCame() {
        let content = UNMutableNotificationContent()
        content.title = "Done: invoices"
        content.body = "All 12 filed."
        content.userInfo = ["deep_link": "marcel://task/tsk_1", "task_id": "tsk_1"]
        XCTAssertEqual(SealedNotification.open(content, keys: FixedKey(nil)), .notSealed, "a direct APNs push")
        XCTAssertEqual(content.title, "Done: invoices")
        XCTAssertEqual(content.userInfo["task_id"] as? String, "tsk_1")
    }

    /// Sealed the way the hub seals (app-api.yaml `SealedPush` steps 1–3), for a key this phone made: a round trip
    /// that also gives a task link its `task_id`.
    func testARoundTripWithAKeyThePhoneMade() throws {
        let key = Curve25519.KeyAgreement.PrivateKey()
        let content: [String: Any] = ["title": "Done: invoices", "body": "All 12 filed.", "link": "marcel://task/tsk_9",
                                      "category": "task"]
        let notification = Self.delivered(Self.json(try Self.seal(content, for: key.publicKey)))
        XCTAssertEqual(SealedNotification.open(notification, keys: FixedKey(key)), .opened)
        XCTAssertEqual(notification.title, "Done: invoices")
        XCTAssertEqual(notification.userInfo["task_id"] as? String, "tsk_9")
        XCTAssertNil(notification.userInfo["agent_id"], "a notice about Marcel as a whole")
    }

    static func seal(_ content: [String: Any], for device: Curve25519.KeyAgreement.PublicKey) throws -> SealedPush {
        let eph = Curve25519.KeyAgreement.PrivateKey()
        let shared = try eph.sharedSecretFromKeyAgreement(with: device)
        let key = shared.hkdfDerivedSymmetricKey(
            using: SHA256.self, salt: eph.publicKey.rawRepresentation + device.rawRepresentation,
            sharedInfo: PushSeal.label, outputByteCount: 32)
        let box = try ChaChaPoly.seal(try JSONSerialization.data(withJSONObject: content), using: key,
                                      authenticating: PushSeal.label)
        return SealedPush(v: 1, eph: Base64URL.encode(eph.publicKey.rawRepresentation),
                          nonce: Base64URL.encode(box.nonce.withUnsafeBytes { Data($0) }),
                          box: Base64URL.encode(box.ciphertext + box.tag))
    }

    // MARK: The key in the Keychain

    /// The key pair is made once, kept in the Keychain, and its private half never lands in UserDefaults.
    func testTheKeyPairIsMadeOnceAndKeptInTheKeychain() throws {
        let store = KeychainPushKeyStore(service: "test.push-key.\(UUID())", accessGroup: nil)
        defer { try? store.delete() }
        XCTAssertNil(try store.privateKey())
        let first = try store.publicKey()
        XCTAssertEqual(try store.publicKey(), first, "made once")
        let again = KeychainPushKeyStore(service: store.service, accessGroup: nil)
        let privateKey = try XCTUnwrap(try again.privateKey(), "a second reader (the extension) finds it")
        XCTAssertEqual(Base64URL.encode(privateKey.publicKey.rawRepresentation), first)
        XCTAssertEqual(Base64URL.decode(first)?.count, 32)
        XCTAssertNotNil(first.wholeMatch(of: /[A-Za-z0-9_\-]{42}[AEIMQUYcgkosw048]/), "PushTarget.device_key's pattern")
        let secret = Base64URL.encode(privateKey.rawRepresentation)
        let defaults = UserDefaults.standard.dictionaryRepresentation().values.map { "\($0)" }.joined()
        XCTAssertFalse(defaults.contains(secret), "never in UserDefaults")
        try store.delete()
        XCTAssertNil(try store.privateKey(), "gone after sign out")
        XCTAssertNotEqual(try store.publicKey(), first, "a new pair after a sign out")
    }

    /// In the group the app shares with its extension: written by one reader, read by another with the same group.
    /// The test runner holds `MARCELTESTS.marcel.push` the way the app holds `<team>.marcel.push`.
    func testTheKeyIsInTheSharedAccessGroup() throws {
        let group = "MARCELTESTS.marcel.push"
        let app = KeychainPushKeyStore(service: "test.push-key.\(UUID())", accessGroup: group)
        defer { try? app.delete() }
        let made = try app.publicKey()
        let ext = KeychainPushKeyStore(service: app.service, accessGroup: group)
        XCTAssertEqual(try ext.privateKey().map { Base64URL.encode($0.publicKey.rawRepresentation) }, made)
        let notShared = KeychainPushKeyStore(service: app.service, accessGroup: "MARCELTESTS.com.shbunder.marcel.tests")
        XCTAssertNil(try notShared.privateKey(), "not in the app's own group")
    }

    func testTheSharedGroupNeedsTheTeamPrefix() {
        XCTAssertEqual(KeychainPushKeyStore.group(from: "AB12CD34EF.marcel.push"), "AB12CD34EF.marcel.push")
        XCTAssertNil(KeychainPushKeyStore.group(from: "marcel.push"), "an unsigned build: no team")
        XCTAssertNil(KeychainPushKeyStore.group(from: ".marcel.push"))
        XCTAssertNil(KeychainPushKeyStore.group(from: "$(AppIdentifierPrefix)marcel"))
        XCTAssertNil(KeychainPushKeyStore.group(from: nil))
    }
}
