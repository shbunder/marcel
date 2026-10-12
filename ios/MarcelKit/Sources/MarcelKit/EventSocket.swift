import Foundation

/// One open WebSocket to `GET /ws`. Production uses `URLSessionWebSocketTask`; tests use a scripted fake.
public protocol EventSocket: Sendable {
    /// The next text frame, as bytes. Throws when the connection drops.
    func receive() async throws -> Data
    func close()
}

/// Opens the stream, resuming after `since` (the last event id the app processed; nil on first connect).
public protocol EventSocketConnector: Sendable {
    func connect(since: Int?) async throws -> any EventSocket
}

public enum EventStreamError: Error, Equatable {
    /// The hub refused the device token (403 on the upgrade). The app treats it as unpaired.
    case unauthorized
    /// No device token is stored.
    case notPaired
}

public struct URLSessionEventSocketConnector: EventSocketConnector {
    let serverURL: URL
    let tokenStore: any TokenStore
    let session: URLSession

    public init(serverURL: URL, tokenStore: any TokenStore, session: URLSession = .shared) {
        self.serverURL = serverURL
        self.tokenStore = tokenStore
        self.session = session
    }

    /// `<server URL>/ws?since=<id>`, with `http(s)` turned into `ws(s)`.
    public static func streamURL(serverURL: URL, since: Int?) -> URL {
        var parts = URLComponents(url: serverURL.appending(path: "ws"), resolvingAgainstBaseURL: false)!
        parts.scheme = parts.scheme == "https" ? "wss" : (parts.scheme == "http" ? "ws" : parts.scheme)
        if let since { parts.queryItems = [URLQueryItem(name: "since", value: String(since))] }
        return parts.url!
    }

    public func connect(since: Int?) async throws -> any EventSocket {
        guard let token = try tokenStore.token() else { throw EventStreamError.notPaired }
        var request = URLRequest(url: Self.streamURL(serverURL: serverURL, since: since))
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        let task = session.webSocketTask(with: request)
        task.resume()
        return URLSessionEventSocket(task: task)
    }
}

final class URLSessionEventSocket: EventSocket, @unchecked Sendable {
    private let task: URLSessionWebSocketTask

    init(task: URLSessionWebSocketTask) {
        self.task = task
    }

    func receive() async throws -> Data {
        do {
            let task = task
            let message = try await withTaskCancellationHandler {
                try await task.receive()
            } onCancel: {
                task.cancel(with: .goingAway, reason: nil)
            }
            switch message {
            case .string(let text): return Data(text.utf8)
            case .data(let data): return data
            @unknown default: return Data()
            }
        } catch {
            if let response = task.response as? HTTPURLResponse, [401, 403].contains(response.statusCode) {
                throw EventStreamError.unauthorized
            }
            throw error
        }
    }

    func close() {
        task.cancel(with: .normalClosure, reason: nil)
    }
}
