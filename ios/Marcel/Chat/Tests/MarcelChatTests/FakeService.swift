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
    /// Thrown by the next sends instead of storing them (until set back to nil).
    var sendError: ChatError?
    /// The agent's name as `GET /me` gives it.
    var agentName = "Marcel"
    /// Holds a send until the test lets it go, to see the message while it is on its way.
    var holdSends = false
    /// Side chats (S-12.4).
    let side = FakeSideHub()
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
        if let sendError { throw sendError }
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

/// The side chat's hub calls, answered from memory (S-12.4).
extension FakeService: SideChatService {
    func openThread(messageId: String) async throws -> Conversation {
        side.opened.append(messageId)
        if side.holdOpens { await withCheckedContinuation { side.heldOpens.append($0) } }
        if let error = side.openError { throw error }
        var json: [String: Any] = [
            "id": side.threadIds[messageId] ?? "cnv_side_\(messageId)", "agent_id": "agt_marcel", "kind": "side",
            "parent_message_id": messageId, "created_at": Fx.at(0),
        ]
        if let task = side.taskIds[messageId] { json["task_id"] = task }
        return Fx.decode(json)
    }

    func threadMessages(conversationId: String) async throws -> [Message] {
        if let error = side.openError { throw error }
        return side.history[conversationId] ?? []
    }

    func reply(text: String, clientId: String, conversationId: String) async throws -> Message {
        side.replies.append((text, clientId, conversationId))
        if let error = side.replyError { throw error }
        return Fx.decode(Fx.message("msg_\(clientId)", at: 200, role: "user", text: text,
                                    extra: ["client_id": clientId, "conversation_id": conversationId]))
    }

    func sendToMain(conversationId: String, messageId: String) async throws -> Message {
        side.sentToMain.append((conversationId, messageId))
        if let error = side.replyError { throw error }
        return Fx.decode(Fx.message("msg_quote_\(messageId)", at: 300, role: "user", kind: "quote", text: "Quoted",
                                    extra: ["quoted_message_id": messageId]))
    }
}

/// What the fake hub knows and was asked about side chats.
@MainActor
final class FakeSideHub {
    var threadIds: [String: String] = [:]
    var taskIds: [String: String] = [:]
    var history: [String: [Message]] = [:]
    var openError: SideChatError?
    var replyError: SideChatError?
    var opened: [String] = []
    /// Holds each open until `releaseOpens`, to act while the thread is still opening.
    var holdOpens = false
    var heldOpens: [CheckedContinuation<Void, Never>] = []

    func releaseOpens() {
        heldOpens.forEach { $0.resume() }
        heldOpens = []
    }
    var replies: [(text: String, clientId: String, conversationId: String)] = []
    var sentToMain: [(conversationId: String, messageId: String)] = []
}
