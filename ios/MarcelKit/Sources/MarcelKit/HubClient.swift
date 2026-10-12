import Foundation
import HTTPTypes
import OpenAPIRuntime
import OpenAPIURLSession

/// Builds the generated REST client for a hub, signing every request with the device token.
public enum HubClient {
    public static func make(
        serverURL: URL,
        tokenStore: (any TokenStore)? = nil,
        transport: any ClientTransport = URLSessionTransport()
    ) -> Client {
        Client(
            serverURL: serverURL,
            configuration: Configuration(dateTranscoder: HubDateTranscoder()),
            transport: transport,
            middlewares: tokenStore.map { [DeviceTokenMiddleware(tokenStore: $0)] } ?? []
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
