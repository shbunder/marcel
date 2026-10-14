import Foundation
import HTTPTypes
import OpenAPIRuntime
import OpenAPIURLSession

/// Builds the generated REST client for a hub, signing every request with the device token.
public enum HubClient {
    /// The hub's transport: a session without the system's HTTP cache. The app keeps its own cache (SwiftData); an
    /// HTTP cache keyed by URL alone mixes two bodies served at one URL by `Accept` (the avatar's JSON and PNG share an
    /// ETag), and answers a fresh request with "not modified".
    public static var transport: URLSessionTransport {
        URLSessionTransport(configuration: .init(session: session))
    }

    /// One session for every hub request: no URL cache, never a cached answer.
    public static let session: URLSession = {
        let configuration = URLSessionConfiguration.default
        configuration.urlCache = nil
        configuration.requestCachePolicy = .reloadIgnoringLocalCacheData
        return URLSession(configuration: configuration)
    }()

    public static func make(
        serverURL: URL,
        tokenStore: (any TokenStore)? = nil,
        transport: any ClientTransport = HubClient.transport
    ) -> Client {
        Client(
            serverURL: serverURL,
            configuration: Configuration(dateTranscoder: HubDateTranscoder()),
            transport: transport,
            middlewares: [ETagMiddleware()] + (tokenStore.map { [DeviceTokenMiddleware(tokenStore: $0)] } ?? [])
        )
    }
}

/// Adds `Authorization: Bearer <device token>` when a token is stored.
struct DeviceTokenMiddleware: ClientMiddleware {
    let tokenStore: any TokenStore

    func intercept(
        _ request: HTTPRequest,
        body: HTTPBody?,
        baseURL: URL,
        operationID: String,
        next: @Sendable (HTTPRequest, HTTPBody?, URL) async throws -> (HTTPResponse, HTTPBody?)
    ) async throws -> (HTTPResponse, HTTPBody?) {
        var request = request
        if let token = try tokenStore.token() {
            request.headerFields[.authorization] = "Bearer \(token)"
        }
        return try await next(request, body, baseURL)
    }
}

/// Sends `If-None-Match` exactly as the hub's `ETag` said it. The generated client percent-encodes header values, so
/// `"lk-fox-teal-cool"` went out as `%22lk-fox-teal-cool%22`, never matched, and the hub never answered 304.
struct ETagMiddleware: ClientMiddleware {
    func intercept(
        _ request: HTTPRequest,
        body: HTTPBody?,
        baseURL: URL,
        operationID: String,
        next: @Sendable (HTTPRequest, HTTPBody?, URL) async throws -> (HTTPResponse, HTTPBody?)
    ) async throws -> (HTTPResponse, HTTPBody?) {
        var request = request
        if let tag = request.headerFields[.ifNoneMatch], let plain = tag.removingPercentEncoding {
            request.headerFields[.ifNoneMatch] = plain
        }
        return try await next(request, body, baseURL)
    }
}
