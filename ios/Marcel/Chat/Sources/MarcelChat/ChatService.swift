import Foundation
import MarcelItems
import MarcelKit
import OpenAPIRuntime

/// Who the chat is with: the agent's main conversation and its name (the header's title; Settings can rename it).
public struct MainAgent: Equatable, Sendable {
    public var conversationId: String
    public var name: String

    public init(conversationId: String, name: String) {
        self.conversationId = conversationId
        self.name = name
    }
}

/// What the chat asks the hub for over REST. Everything else it shows comes from the cache, which the live event
/// stream keeps current. Tests use a fake; the app uses `LiveChatService`.
@MainActor
public protocol ChatService {
    /// The agent's main conversation and its name (`GET /me`).
    func mainAgent() async throws -> MainAgent
    /// Stores your message (`POST /conversations/{id}/messages`). It never waits for Marcel; the same
    /// `clientId` sent again returns the same message, so a retry never doubles it. An attached item goes as
    /// `artifact_id`, tagged with the item's project (`project_id`).
    func send(text: String, clientId: String, conversationId: String, attachment: ItemAttachment?) async throws
        -> Message
    /// Every approval, open ones first (`GET /approvals`).
    func approvals() async throws -> [Approval]
    /// Approve or deny. An approval already answered elsewhere comes back as it stands.
    func answer(approvalId: String, approve: Bool) async throws -> Approval
    /// An artifact's current bytes, for its preview.
    func artifactContent(id: String) async throws -> Data
}

/// A failure said for the person holding the phone.
public struct ChatError: Error, Equatable, LocalizedError {
    public var message: String
    /// The item attached to the message is gone from the hub (404 `artifact_not_found`): sending it again would fail
    /// the same way, so the message is offered without it.
    public var itemGone = false
    public init(_ message: String, itemGone: Bool = false) {
        self.message = message
        self.itemGone = itemGone
    }
    public var errorDescription: String? { message }

    static let unreachable = ChatError("Can't reach the hub right now.")
    static let itemGone = ChatError(
        "Not sent: the item you attached isn't on the hub any more. Tap the message to send it without the item.",
        itemGone: true)
    static let noConversation = ChatError("Not sent: the hub does not know this conversation.")
    static let unpaired = ChatError("The hub no longer accepts this phone. Pair it again.")
    static func unexpected(_ what: String) -> ChatError {
        ChatError("The hub answered something the app does not understand (\(what)).")
    }
}

/// The real hub, through the generated client.
public struct LiveChatService: ChatService {
    let client: Client

    public init(client: Client) {
        self.client = client
    }

    public func mainAgent() async throws -> MainAgent {
        let output: Operations.GetMe.Output
        do { output = try await client.getMe() } catch { throw ChatError.unreachable }
        switch output {
        case .ok(let ok):
            guard let agent = try ok.body.json.agents.first else { throw ChatError("This hub has no agent yet.") }
            return MainAgent(conversationId: agent.mainConversationId, name: agent.name)
        case .unauthorized: throw ChatError.unpaired
        case .undocumented(let status, _): throw ChatError.unexpected("me, HTTP \(status)")
        }
    }

    public func send(text: String, clientId: String, conversationId: String, attachment: ItemAttachment?) async throws
        -> Message
    {
        let output: Operations.SendMessage.Output
        do {
            output = try await client.sendMessage(
                path: .init(conversationId: conversationId),
                body: .json(.init(text: text, clientId: clientId, projectId: attachment?.projectId,
                                  artifactId: attachment?.artifactId)))
        } catch {
            throw ChatError("Not sent: can't reach the hub. Tap the message to try again.")
        }
        switch output {
        case .created(let created): return try created.body.json
        case .unauthorized: throw ChatError.unpaired
        case .notFound(let missing):
            // Which thing is gone, by its code: the attached item, or the conversation itself.
            throw (try? missing.body.json.code) == "artifact_not_found" ? ChatError.itemGone : ChatError.noConversation
        case .badRequest: throw ChatError("Not sent: the hub refused the message.")
        case .conflict: throw ChatError("Not sent: that thread has finished.")
        case .undocumented(let status, _): throw ChatError.unexpected("send, HTTP \(status)")
        }
    }

    public func approvals() async throws -> [Approval] {
        let output: Operations.ListApprovals.Output
        do { output = try await client.listApprovals() } catch { throw ChatError.unreachable }
        switch output {
        case .ok(let ok): return try ok.body.json
        case .unauthorized: throw ChatError.unpaired
        case .undocumented(let status, _): throw ChatError.unexpected("approvals, HTTP \(status)")
        }
    }

    public func answer(approvalId: String, approve: Bool) async throws -> Approval {
        let output: Operations.AnswerApproval.Output
        do {
            output = try await client.answerApproval(
                path: .init(approvalId: approvalId), body: .json(.init(decision: approve ? .approve : .deny)))
        } catch {
            throw ChatError("Couldn't send your answer. Check the connection and try again.")
        }
        switch output {
        case .ok(let ok): return try ok.body.json
        case .conflict(let conflict): return try conflict.body.json  // answered elsewhere, or expired
        case .notFound: throw ChatError("That question is gone from the hub.")
        case .unauthorized: throw ChatError.unpaired
        case .badRequest: throw ChatError.unexpected("answer, HTTP 400")
        case .undocumented(let status, _): throw ChatError.unexpected("answer, HTTP \(status)")
        }
    }

    public func artifactContent(id: String) async throws -> Data {
        let output: Operations.GetArtifactContent.Output
        do { output = try await client.getArtifactContent(path: .init(artifactId: id)) } catch {
            throw ChatError.unreachable
        }
        switch output {
        case .ok(let ok):
            switch ok.body {
            case .any(let body): return try await Data(collecting: body, upTo: 256 * 1024)
            }
        case .unauthorized: throw ChatError.unpaired
        case .notFound: throw ChatError("That item is gone from the hub.")
        case .badRequest: throw ChatError.unexpected("item content, HTTP 400")
        case .undocumented(let status, _): throw ChatError.unexpected("item content, HTTP \(status)")
        }
    }
}
