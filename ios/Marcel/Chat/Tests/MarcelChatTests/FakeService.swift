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
    private(set) var sent: [(text: String, clientId: String)] = []
    /// Holds a send until the test lets it go, to see the message while it is on its way.
    var holdSends = false
    private var held: [CheckedContinuation<Void, Never>] = []

    func release() {
        held.forEach { $0.resume() }
        held = []
    }

    init(failing: Bool = false) {
        self.failing = failing
    }

    func mainConversationId() async throws -> String {
        if failing { throw ChatError.unreachable }
        return Fx.conversation
    }

    func send(text: String, clientId: String, conversationId: String) async throws -> Message {
        sent.append((text, clientId))
        if holdSends { await withCheckedContinuation { held.append($0) } }
        if failing { throw ChatError("Not sent: can't reach the hub. Tap the message to try again.") }
        return Fx.decode(Fx.message("msg_\(clientId)", at: 100, role: "user", text: text,
                                    extra: ["client_id": clientId]))
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
