import Foundation
import OpenAPIRuntime
import OpenAPIURLSession

/// What the hub's pairing QR code holds: `marcel://pair?hub=<server URL>&code=<code>` (contract `PairingCode`).
public struct PairingCode: Equatable, Sendable {
    public let serverURL: URL
    public let code: String

    public init(scanned text: String) throws(PairingError) {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let parts = URLComponents(string: trimmed), parts.scheme == "marcel", parts.host == "pair",
            let items = parts.queryItems
        else { throw .notAPairingCode }
        guard let hub = items.first(where: { $0.name == "hub" })?.value,
            let url = URL(string: hub), let scheme = url.scheme, ["http", "https"].contains(scheme), url.host != nil
        else { throw .notAPairingCode }
        guard let code = items.first(where: { $0.name == "code" })?.value,
            (6...12).contains(code.count), code.allSatisfy({ $0.isASCII && ($0.isUppercase || $0.isNumber) })
        else { throw .notAPairingCode }
        self.serverURL = url
        self.code = code
    }
}

public enum PairingError: Error, LocalizedError, Equatable {
    case notAPairingCode
    case rejected(String)
    case hubUnreachable
    case unexpectedAnswer(Int)
    case couldNotStoreToken

    public var errorDescription: String? {
        switch self {
        case .notAPairingCode:
            "That is not a Marcel pairing code. Run `marcel-hub pair` on the hub and scan the code it shows."
        case .rejected(let message):
            message
        case .hubUnreachable:
            "Could not reach the hub. Check that it is running and that the phone is online, then scan again."
        case .unexpectedAnswer(let status):
            "The hub gave an answer the app does not understand (HTTP \(status)). Update the app or the hub."
        case .couldNotStoreToken:
            "Paired, but the phone's Keychain would not keep the sign-in. Scan the code again."
        }
    }
}

/// Pairs this phone with a hub: reads the QR code, sends the one-time code to `POST /pair`, then keeps the
/// device token in the token store (the Keychain in production) and the hub's address in the hub store.
public struct PairingService: Sendable {
    let tokenStore: any TokenStore
    let hubStore: any PairedHubStore
    let transport: any ClientTransport

    public init(
        tokenStore: any TokenStore,
        hubStore: any PairedHubStore,
        transport: any ClientTransport = URLSessionTransport()
    ) {
        self.tokenStore = tokenStore
        self.hubStore = hubStore
        self.transport = transport
    }

    @discardableResult
    public func pair(scanned text: String, deviceName: String) async throws(PairingError) -> PairedHub {
        let pairing = try PairingCode(scanned: text)
        let client = HubClient.make(serverURL: pairing.serverURL, transport: transport)
        let name = String(deviceName.prefix(80)).isEmpty ? "iPhone" : String(deviceName.prefix(80))
        let output: Operations.PairDevice.Output
        do {
            output = try await client.pairDevice(body: .json(.init(code: pairing.code, deviceName: name)))
        } catch {
            throw .hubUnreachable
        }
        switch output {
        case .created(let created):
            guard case .json(let answer) = created.body else { throw .unexpectedAnswer(201) }
            let hub = PairedHub(serverURL: pairing.serverURL, deviceId: answer.deviceId, userId: answer.userId)
            do {
                try tokenStore.save(answer.token)
                try hubStore.save(hub)
            } catch {
                throw .couldNotStoreToken
            }
            return hub
        case .badRequest(let bad):
            if case .json(let problem) = bad.body { throw .rejected(problem.message) }
            throw .unexpectedAnswer(400)
        case .tooManyRequests(let busy):
            if case .json(let problem) = busy.body { throw .rejected(problem.message) }
            throw .unexpectedAnswer(429)
        case .undocumented(let status, _):
            throw .unexpectedAnswer(status)
        }
    }

    /// Forgets the hub: removes the token and the hub address. Used when the hub refuses the token.
    public func unpair() {
        try? tokenStore.delete()
        hubStore.clear()
    }
}
