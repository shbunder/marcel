import Foundation
import MarcelKit
import OpenAPIRuntime

/// What the side chat asks the hub for (B-08, wireframe 2B). The thread's messages themselves come from the cache,
/// which the live stream keeps current; these calls open the thread, fill the cache once, post a reply and bring a
/// result back. Tests use a fake; the app uses `LiveChatService`.
@MainActor
public protocol SideChatService {
    /// Opens the side thread on a message, or returns the one it already has (`POST /messages/{id}/thread`).
    func openThread(messageId: String) async throws -> Conversation
    /// The newest messages of a thread, newest first (`GET /conversations/{id}/messages`).
    func threadMessages(conversationId: String) async throws -> [Message]
    /// Posts a reply in the thread (`POST /conversations/{id}/messages`). The same `clientId` sent again returns the
    /// same message. In a task's own thread the reply goes to the task; a stopped or failed task answers
    /// `SideChatError.taskFinished`.
    func reply(text: String, clientId: String, conversationId: String) async throws -> Message
    /// Quotes a thread message into the main conversation (`POST /conversations/{id}/send-to-main`).
    func sendToMain(conversationId: String, messageId: String) async throws -> Message
}

/// A side-chat failure, said for the person holding the phone.
public struct SideChatError: Error, Equatable, LocalizedError {
    public enum Kind: Equatable, Sendable {
        /// No answer from the hub: the network, or the hub is down. Worth trying again.
        case offline
        /// 401: this phone is no longer paired.
        case unpaired
        /// 409 `task_finished`: the task behind this thread has stopped or failed and takes no more messages.
        case taskFinished
        /// 404 with a `*_not_found` code: the message or the thread is gone from the hub.
        case gone
        /// 404 `not_found` / 405, or a body that is not the contract's: this hub has not built side chats yet.
        case notAvailable
        /// 5xx or anything else the app does not expect.
        case hubProblem
    }

    public var kind: Kind
    public var message: String

    public init(_ kind: Kind, _ message: String) {
        self.kind = kind
        self.message = message
    }

    public var errorDescription: String? { message }

    static let offline = SideChatError(.offline, "Can't reach the hub right now. Check the connection and try again.")
    static let unpaired = SideChatError(.unpaired, "The hub no longer accepts this phone. Pair it again.")
    static let taskFinished = SideChatError(.taskFinished, "This task has stopped; start a new one?")
    static let notAvailable = SideChatError(.notAvailable, "This hub can't do side chats yet. Update the hub.")
    static let hubProblem = SideChatError(.hubProblem, "The hub had a problem. Try again in a moment.")

    /// A 404: only a `*_not_found` code means the thing is gone; the generic `not_found` is a route the hub has not
    /// built (see project/lessons/ios-screen-packages.md).
    static func missing(_ code: String?) -> SideChatError {
        switch code {
        case "message_not_found": SideChatError(.gone, "That message is gone from the hub, so it has no side chat.")
        case "conversation_not_found": SideChatError(.gone, "This side chat is gone from the hub.")
        case let code? where code.hasSuffix("_not_found"): SideChatError(.gone, "That is gone from the hub.")
        default: notAvailable
        }
    }

    /// Any other status, by its class.
    static func status(_ code: Int) -> SideChatError {
        switch code {
        case 401: unpaired
        case 404, 405, 501: notAvailable
        default: hubProblem
        }
    }
}

extension LiveChatService: SideChatService {
    public func openThread(messageId: String) async throws -> Conversation {
        switch try await sideCall({ try await client.openSideThread(path: .init(messageId: messageId)) }) {
        case .ok(let ok): return try ok.body.json
        case .created(let created): return try created.body.json
        case .unauthorized: throw SideChatError.unpaired
        case .notFound(let missing): throw SideChatError.missing(try? missing.body.json.code)
        case .undocumented(let status, _): throw SideChatError.status(status)
        }
    }

    public func threadMessages(conversationId: String) async throws -> [Message] {
        let output = try await sideCall({
            try await client.listMessages(path: .init(conversationId: conversationId), query: .init(limit: 100))
        })
        switch output {
        case .ok(let ok): return try ok.body.json.items
        case .unauthorized: throw SideChatError.unpaired
        case .notFound(let missing): throw SideChatError.missing(try? missing.body.json.code)
        case .badRequest: throw SideChatError.hubProblem
        case .undocumented(let status, _): throw SideChatError.status(status)
        }
    }

    public func reply(text: String, clientId: String, conversationId: String) async throws -> Message {
        let output = try await sideCall({
            try await client.sendMessage(path: .init(conversationId: conversationId),
                                         body: .json(.init(text: text, clientId: clientId)))
        })
        switch output {
        case .created(let created): return try created.body.json
        case .unauthorized: throw SideChatError.unpaired
        case .notFound(let missing): throw SideChatError.missing(try? missing.body.json.code)
        case .conflict: throw SideChatError.taskFinished
        case .badRequest: throw SideChatError(.hubProblem, "The hub refused the reply.")
        case .undocumented(let status, _): throw SideChatError.status(status)
        }
    }

    public func sendToMain(conversationId: String, messageId: String) async throws -> Message {
        let output = try await sideCall({
            try await client.sendToMain(path: .init(conversationId: conversationId),
                                        body: .json(.init(messageId: messageId)))
        })
        switch output {
        case .created(let created): return try created.body.json
        case .unauthorized: throw SideChatError.unpaired
        case .notFound(let missing): throw SideChatError.missing(try? missing.body.json.code)
        case .badRequest: throw SideChatError(.hubProblem, "The hub refused to send that to the main chat.")
        case .undocumented(let status, _): throw SideChatError.status(status)
        }
    }

    /// Runs a request. No answer becomes `.offline`; a body that is not the contract's (a hub that has not built the
    /// route answers FastAPI's bare 404) is read by its status.
    private func sideCall<T>(_ request: () async throws -> T) async throws -> T {
        do {
            return try await request()
        } catch let error as SideChatError {
            throw error
        } catch let error as ClientError {
            if let status = error.response?.status.code { throw SideChatError.status(status) }
            throw SideChatError.offline
        } catch {
            throw SideChatError.offline
        }
    }
}
