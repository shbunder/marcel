import Foundation
import Security
import XCTest

@testable import MarcelKit

/// The device token is a password: it must land in the Keychain and never in UserDefaults.
@MainActor
final class KeychainTokenTests: XCTestCase {
    private var defaults: UserDefaults!
    private var suite: String!

    override func setUp() async throws {
        suite = "marcel.tests.\(UUID().uuidString)"
        defaults = UserDefaults(suiteName: suite)
        try KeychainTokenStore().delete()
    }

    override func tearDown() async throws {
        try KeychainTokenStore().delete()
        defaults.removePersistentDomain(forName: suite)
    }

    /// Reads the token straight from the Keychain, bypassing MarcelKit.
    private func keychainItem(service: String = KeychainTokenStore.defaultService) -> String? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: "device",
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne,
        ]
        var item: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &item) == errSecSuccess, let data = item as? Data else {
            return nil
        }
        return String(decoding: data, as: UTF8.self)
    }

    private func defaultsMention(_ secret: String, in defaults: UserDefaults) -> Bool {
        defaults.dictionaryRepresentation().values.contains { value in
            if let text = value as? String { return text.contains(secret) }
            if let data = value as? Data { return String(decoding: data, as: UTF8.self).contains(secret) }
            return String(describing: value).contains(secret)
        }
    }

    func testTheProductionConnectionKeepsTheTokenInTheKeychain() {
        let connection = HubConnection.live(cache: MarcelCache(container: try! MarcelCache.makeContainer(inMemory: true)), defaults: defaults)
        XCTAssertTrue(connection.tokenStore is KeychainTokenStore)
    }

    func testPairingThroughTheProductionPathPutsTheTokenInTheKeychainNotUserDefaults() async throws {
        let transport = StubTransport { _ in .json(201, PairingServiceTests.paired) }
        let cache = MarcelCache(container: try MarcelCache.makeContainer(inMemory: true))
        let connection = HubConnection.live(cache: cache, transport: transport, defaults: defaults)

        try await connection.pair(scanned: PairingServiceTests.scanned, deviceName: "Shaun's iPhone")
        connection.stop()

        XCTAssertEqual(keychainItem(), "mrc_secret_token")
        XCTAssertFalse(defaultsMention("mrc_secret_token", in: defaults), "token leaked into the app's UserDefaults")
        XCTAssertFalse(defaultsMention("mrc_secret_token", in: .standard), "token leaked into standard UserDefaults")
        XCTAssertNotNil(defaults.data(forKey: UserDefaultsPairedHubStore.key), "the hub's address is remembered")
    }

    func testTheKeychainStoreRoundTripsReplacesAndDeletes() throws {
        let store = KeychainTokenStore(service: "com.shbunder.marcel.tests.\(UUID().uuidString)")
        defer { try? store.delete() }

        XCTAssertNil(try store.token())
        try store.save("first")
        try store.save("second")
        XCTAssertEqual(try store.token(), "second")
        XCTAssertEqual(keychainItem(service: store.service), "second")
        try store.delete()
        XCTAssertNil(try store.token())
        XCTAssertNil(keychainItem(service: store.service))
    }

    func testUnpairingRemovesTheTokenFromTheKeychain() async throws {
        let transport = StubTransport { _ in .json(201, PairingServiceTests.paired) }
        let cache = MarcelCache(container: try MarcelCache.makeContainer(inMemory: true))
        let connection = HubConnection.live(cache: cache, transport: transport, defaults: defaults)
        try await connection.pair(scanned: PairingServiceTests.scanned, deviceName: "iPhone")

        connection.unpair()

        XCTAssertNil(keychainItem())
        XCTAssertNil(defaults.data(forKey: UserDefaultsPairedHubStore.key))
        XCTAssertEqual(connection.phase, .unpaired)
    }

    func testAPairedPhoneStaysPairedAcrossLaunches() async throws {
        let transport = StubTransport { _ in .json(201, PairingServiceTests.paired) }
        let first = HubConnection.live(
            cache: MarcelCache(container: try MarcelCache.makeContainer(inMemory: true)), transport: transport,
            defaults: defaults)
        try await first.pair(scanned: PairingServiceTests.scanned, deviceName: "iPhone")
        first.stop()

        let relaunched = HubConnection.live(
            cache: MarcelCache(container: try MarcelCache.makeContainer(inMemory: true)), transport: transport,
            defaults: defaults)

        guard case .paired(let hub) = relaunched.phase else { return XCTFail("should still be paired") }
        XCTAssertEqual(hub.deviceId, "dev_01J9ZQ4K2B")
    }
}
