import Foundation

/// What pairing leaves behind besides the token: which hub, and who this device is. None of it is secret.
public struct PairedHub: Codable, Equatable, Sendable {
    public var serverURL: URL
    public var deviceId: String
    public var userId: String

    public init(serverURL: URL, deviceId: String, userId: String) {
        self.serverURL = serverURL
        self.deviceId = deviceId
        self.userId = userId
    }
}

public protocol PairedHubStore: Sendable {
    func load() -> PairedHub?
    func save(_ hub: PairedHub) throws
    func clear()
}

/// Keeps `PairedHub` in UserDefaults. The token never goes here; it is in the Keychain.
public struct UserDefaultsPairedHubStore: PairedHubStore, @unchecked Sendable {
    public static let key = "marcel.pairedHub"
    private let defaults: UserDefaults

    public init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
    }

    public func load() -> PairedHub? {
        guard let data = defaults.data(forKey: Self.key) else { return nil }
        return try? JSONDecoder().decode(PairedHub.self, from: data)
    }

    public func save(_ hub: PairedHub) throws {
        defaults.set(try JSONEncoder().encode(hub), forKey: Self.key)
    }

    public func clear() {
        defaults.removeObject(forKey: Self.key)
    }
}
