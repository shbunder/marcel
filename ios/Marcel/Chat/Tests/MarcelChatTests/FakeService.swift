import Foundation
import MarcelItems
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
    private(set) var sent: [(text: String, clientId: String, attachment: ItemAttachment?)] = []
    /// The agent's name as `GET /me` gives it.
    var agentName = "Marcel"
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

    func mainAgent() async throws -> MainAgent {
        if failing { throw ChatError.unreachable }
        return MainAgent(conversationId: Fx.conversation, name: agentName)
    }

    func send(text: String, clientId: String, conversationId: String, attachment: ItemAttachment?) async throws
        -> Message
    {
        sent.append((text, clientId, attachment))
        if holdSends { await withCheckedContinuation { held.append($0) } }
        if failing { throw ChatError("Not sent: can't reach the hub. Tap the message to try again.") }
        var extra: [String: Any] = ["client_id": clientId]
        if let attachment { extra["artifact_id"] = attachment.artifactId; extra["project_id"] = attachment.projectId }
        return Fx.decode(Fx.message("msg_\(clientId)", at: 100, role: "user", text: text, extra: extra))
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
