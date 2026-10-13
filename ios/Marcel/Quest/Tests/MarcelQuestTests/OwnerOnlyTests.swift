import Foundation
import HTTPTypes
import MarcelKit
import OpenAPIRuntime
import XCTest

@testable import MarcelQuest

/// app API 0.7: answering an approval only the owner may answer (a change to Marcel itself) shows the hub's sentence.
@MainActor
final class OwnerOnlyTests: XCTestCase {
    private final class Hub403: ClientTransport, @unchecked Sendable {
        let body: String
        init(_ body: String) { self.body = body }
        func send(_ request: HTTPRequest, body: HTTPBody?, baseURL: URL, operationID: String) async throws -> (
            HTTPResponse, HTTPBody?
        ) {
            var fields = HTTPFields()
            fields[.contentType] = "application/json"
            return (HTTPResponse(status: .init(code: 403), headerFields: fields), HTTPBody(self.body))
        }
    }

    private func answer(_ body: String) async -> QuestViewError? {
        let service = LiveQuestViewService(client: HubClient.make(
            serverURL: URL(string: "http://hub.test/api")!, transport: Hub403(body)))
        do {
            _ = try await service.answer(approvalId: "apr_1", approve: true)
            return nil
        } catch {
            return error as? QuestViewError
        }
    }

    func testTheHubsSentenceIsShown() async {
        let error = await answer(#"{"code":"owner_only","message":"Only Shaun can approve changes to Marcel. Ask Shaun to do it."}"#)
        XCTAssertEqual(error, .ownerOnly("Only Shaun can approve changes to Marcel. Ask Shaun to do it."))
        XCTAssertEqual(error?.errorDescription, "Only Shaun can approve changes to Marcel. Ask Shaun to do it.")
    }

    func testThePlainFallbackWithoutAHubSentence() {
        XCTAssertEqual(QuestViewError.ownerOnly(nil).errorDescription,
                       "Only the owner can answer this. Ask them to approve it on their phone.")
    }
}
