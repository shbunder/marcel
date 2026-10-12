import Foundation
import MarcelKit
import OpenAPIRuntime

/// What the chat asks the hub for over REST. Everything else it shows comes from the cache, which the live event
/// stream keeps current. Tests use a fake; the app uses `LiveChatService`.
@MainActor
public protocol ChatService {
    /// The agent's main conversation (`GET /me`).
    func mainConversationId() async throws -> String
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
    public init(_ message: String) { self.message = message }
    public var errorDescription: String? { message }

    static let unreachable = ChatError("Can't reach the hub right now.")
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

    public func mainConversationId() async throws -> String {
        let output: Operations.GetMe.Output
        do { output = try await client.getMe() } catch { throw ChatError.unreachable }
        switch output {
        case .ok(let ok):
            guard let agent = try ok.body.json.agents.first else { throw ChatError("This hub has no agent yet.") }
            return agent.mainConversationId
        case .unauthorized: throw ChatError.unpaired
        case .undocumented(let status, _): throw ChatError.unexpected("me, HTTP \(status)")
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
