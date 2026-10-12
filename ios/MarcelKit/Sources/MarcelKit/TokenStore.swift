import Foundation
import Security

/// Where the device token lives. The token is a password: production keeps it in the Keychain only.
public protocol TokenStore: Sendable {
    func token() throws -> String?
    func save(_ token: String) throws
    func delete() throws
}

public struct KeychainError: Error, LocalizedError, Equatable {
    public let status: OSStatus
    public var errorDescription: String? {
        "The phone's Keychain refused to keep the sign-in (error \(status)). Restart the app and pair again."
    }
}

/// The device token in the Keychain, readable only on this device once it has been unlocked.
public struct KeychainTokenStore: TokenStore {
    public static let defaultService = "com.shbunder.marcel.device-token"
    public let service: String
    public let account: String

    public init(service: String = KeychainTokenStore.defaultService, account: String = "device") {
        self.service = service
        self.account = account
    }

    private var query: [String: Any] {
        [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
    }

    public func token() throws -> String? {
        var request = query
        request[kSecReturnData as String] = true
        request[kSecMatchLimit as String] = kSecMatchLimitOne
        var item: CFTypeRef?
        let status = SecItemCopyMatching(request as CFDictionary, &item)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess, let data = item as? Data else { throw KeychainError(status: status) }
        return String(decoding: data, as: UTF8.self)
    }

    public func save(_ token: String) throws {
        let data = Data(token.utf8)
        let update = SecItemUpdate(query as CFDictionary, [kSecValueData as String: data] as CFDictionary)
        if update == errSecSuccess { return }
        guard update == errSecItemNotFound else { throw KeychainError(status: update) }
        var item = query
        item[kSecValueData as String] = data
        item[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        let status = SecItemAdd(item as CFDictionary, nil)
        guard status == errSecSuccess else { throw KeychainError(status: status) }
    }

    public func delete() throws {
        let status = SecItemDelete(query as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else { throw KeychainError(status: status) }
    }
}
