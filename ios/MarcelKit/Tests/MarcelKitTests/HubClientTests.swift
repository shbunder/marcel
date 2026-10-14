import Foundation
import HTTPTypes
import OpenAPIRuntime
import XCTest

@testable import MarcelKit

/// The hub's session keeps no HTTP cache. With one, the avatar's PNG request (same URL as its JSON, other `Accept`)
/// went out with the JSON's fresh ETag as `If-None-Match`, the hub said "not modified", and a new look never showed
/// (S-19.4's mock-hub test, `testPickingALookOnTheMockHubShowsInTheHeader`).
final class HubClientTests: XCTestCase {
    func testTheHubSessionKeepsNoHTTPCache() {
        let configuration = HubClient.session.configuration
        XCTAssertNil(configuration.urlCache, "no URL cache: the app keeps its own")
        XCTAssertEqual(configuration.requestCachePolicy, .reloadIgnoringLocalCacheData)
    }

    /// A hub that remembers the request headers it got.
    final class Recorder: ClientTransport, @unchecked Sendable {
        var headers: [HTTPFields] = []

        func send(_ request: HTTPRequest, body: HTTPBody?, baseURL: URL, operationID: String) async throws -> (
            HTTPResponse, HTTPBody?
        ) {
            headers.append(request.headerFields)
            return (HTTPResponse(status: .notModified), nil)
        }
    }

    func testTheETagGoesBackExactlyAsTheHubSentIt() async throws {
        let hub = Recorder()
        let client = HubClient.make(serverURL: URL(string: "http://hub.test/api")!, transport: hub)
        _ = try await client.getAgentAvatar(.init(path: .init(agentId: "agt_1"),
                                                  headers: .init(ifNoneMatch: #""lk-fox-teal-cool""#)))
        XCTAssertEqual(hub.headers.last?[.ifNoneMatch], #""lk-fox-teal-cool""#, "not %22lk-fox-teal-cool%22")
    }
}
