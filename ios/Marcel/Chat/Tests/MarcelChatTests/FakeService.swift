import Foundation
import MarcelKit

@testable import MarcelChat

/// A hub that answers from memory and records what the app asked of it.
@MainActor
final class FakeService: ChatService {
    var failing: Bool
    var approvalsOnHub: [String: Approval] = [:]
    var contents: [String: Data] = [:]
    private(set) var answers: [(id: String, approve: Bool)] = []
    private(set) var approvalFetches = 0

    init(failing: Bool = false) {
        self.failing = failing
    }

    func mainConversationId() async throws -> String {
        if failing { throw ChatError.unreachable }
        return Fx.conversation
    }

    func approvals() async throws -> [Approval] {
        approvalFetches += 1
        if failing { throw ChatError.unreachable }
        return Array(approvalsOnHub.values)
    }

    func answer(approvalId: String, approve: Bool) async throws -> Approval {
        answers.append((approvalId, approve))
        if failing { throw ChatError("Couldn't send your answer. Check the connection and try again.") }
        guard var approval = approvalsOnHub[approvalId] else { throw ChatError("That question is gone from the hub.") }
        if approval.state == .open { approval.state = approve ? .approved : .denied; approval.answeredVia = .app }
        approvalsOnHub[approvalId] = approval
        return approval
    }

    func artifactContent(id: String) async throws -> Data {
        guard let data = contents[id] else { throw ChatError("That item is gone from the hub.") }
        return data
    }
}
