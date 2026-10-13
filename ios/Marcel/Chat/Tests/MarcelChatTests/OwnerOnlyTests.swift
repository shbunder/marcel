import Foundation
import MarcelKit
import XCTest

@testable import MarcelChat

/// app API 0.7: answering an approval only the owner may answer (a change to Marcel itself) shows the hub's sentence.
@MainActor
final class OwnerOnlyTests: XCTestCase {
    private func answer(status: Int, body: String) async -> String? {
        let service = LiveChatService(client: HubClient.make(
            serverURL: URL(string: "http://hub.test/api")!, transport: StubHub(status: status, body: body)))
        do {
            _ = try await service.answer(approvalId: "apr_1", approve: true)
            return nil
        } catch let error as ChatError {
            return error.message
        } catch {
            return "\(error)"
        }
    }

    func testTheHubsSentenceIsShown() async {
        let words = await answer(status: 403, body: #"{"code":"owner_only","message":"Only Shaun can approve changes to Marcel. Ask Shaun to do it."}"#)
        XCTAssertEqual(words, "Only Shaun can approve changes to Marcel. Ask Shaun to do it.")
    }
}
