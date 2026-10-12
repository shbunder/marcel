import Foundation
import MarcelKit
import OpenAPIRuntime

/// What the chat asks the hub for over REST. Everything else it shows comes from the cache, which the live event
/// stream keeps current. Tests use a fake; the app uses `LiveChatService`.
@MainActor
public protocol ChatService {
    /// The agent's main conversation (`GET /me`).
    func mainConversationId() async throws -> String
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
}
