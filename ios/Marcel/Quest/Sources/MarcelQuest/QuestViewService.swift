import Foundation
import MarcelKit
import OpenAPIRuntime

/// What the quest view asks of the hub. The live one talks to `contracts/app-api.yaml`; the tests use a fake.
@MainActor
public protocol QuestViewService {
    /// `GET /tasks/{id}`.
    func task(id: String) async throws -> HubTask
    /// `GET /tasks/{id}/events?after=…&limit=200`: one page, oldest first.
    func events(taskId: String, after: Int?) async throws -> (events: [TaskEvent], hasMore: Bool)
    /// `POST /tasks/{id}/messages`: text typed into this quest only (B-11).
    func steer(taskId: String, text: String, clientId: String) async throws -> HubTask
    /// `POST /tasks/{id}/stop`.
    func stop(taskId: String) async throws -> HubTask
    /// `POST /tasks/{id}/handback`, with what you want Marcel to know.
    func handBack(taskId: String, note: String?) async throws -> HubTask
    /// `POST /tasks/{id}/move` (B-31).
    func move(taskId: String, to projectId: String) async throws -> HubTask
    /// `POST /approvals/{id}` (B-12). The first answer wins, from the app or a terminal.
    func answer(approvalId: String, approve: Bool) async throws -> Approval
}

/// Why something in the quest view did not work, in words a family member reads on a phone.
public enum QuestViewError: Error, Equatable, LocalizedError {
    /// 503 `runner_unreachable` on a stop: the quest was left running.
    case runnerUnreachable
    /// 409 `task_finished` on a message: a stopped or failed quest takes no more messages.
    case finished
    /// 409 `project_archived` on a move.
    case projectArchived
    /// 409 on an answer: someone answered first (a terminal, another phone) or it expired. Carries it as it stands.
    case alreadyAnswered(Approval)
    /// 403 `owner_only` (app API 0.7): only the owner may answer this (a change to Marcel itself). Carries the hub's
    /// sentence, which names the owner.
    case ownerOnly(String?)
    /// 400 on a message: the hub says what to change.
    case badMessage(String)
    case notFound
    case unauthorized
    case notPaired
    case offline
    case unexpected(String)

    public var errorDescription: String? {
        switch self {
        case .runnerUnreachable: "The NUC didn't answer, so the quest is still running. Try again in a minute."
        case .finished: "This quest has finished, so it can't take messages. Ask Marcel to try again instead."
        case .projectArchived: "That project is archived, so the quest stayed where it was."
        case .alreadyAnswered(let approval):
            switch ApprovalStatus(approval) {
            case .approved(true): "It was already approved in the terminal."
            case .denied(true): "It was already denied in the terminal."
            case .approved: "It was already approved."
            case .denied: "It was already denied."
            default: "The question went away before your answer arrived."
            }
        case .ownerOnly(let message): message ?? "Only the owner can answer this. Ask them to approve it on their phone."
        case .badMessage(let message): message
        case .notFound: "The hub no longer has this quest."
        case .unauthorized: "The hub no longer accepts this phone. Pair it again."
        case .notPaired: "This phone isn't paired with a hub yet."
        case .offline: "Couldn't reach the hub. Check the connection and try again."
        case .unexpected(let what): "The hub sent something the app doesn't understand (\(what))."
        }
    }

    /// Any error, as the screen says it.
    static func words(for error: any Error) -> String {
        (error as? QuestViewError)?.errorDescription
            ?? "Something went wrong talking to the hub. Try again in a moment."
    }
}

/// The hub's REST API, signed with this phone's device token.
public struct LiveQuestViewService: QuestViewService {
    let client: Client

    public init(client: Client) {
        self.client = client
    }

    /// The paired hub as pairing left it (address in UserDefaults, token in the Keychain); nil when not paired.
    public static func paired(defaults: UserDefaults = .standard) -> LiveQuestViewService? {
        guard let hub = UserDefaultsPairedHubStore(defaults: defaults).load() else { return nil }
        return LiveQuestViewService(client: HubClient.make(serverURL: hub.serverURL, tokenStore: KeychainTokenStore()))
    }

    public func task(id: String) async throws -> HubTask {
        switch try await call({ try await client.getTask(path: .init(taskId: id)) }) {
        case .ok(let ok): return try ok.body.json
        case .unauthorized: throw QuestViewError.unauthorized
        case .notFound: throw QuestViewError.notFound
        case .undocumented(let status, _): throw QuestViewError.unexpected("quest, HTTP \(status)")
        }
    }

    public func events(taskId: String, after: Int?) async throws -> (events: [TaskEvent], hasMore: Bool) {
        let answer = try await call {
            try await client.listTaskEvents(path: .init(taskId: taskId), query: .init(after: after, limit: 200))
        }
        switch answer {
        case .ok(let ok):
            let page = try ok.body.json
            return (page.items, page.hasMore)
        case .badRequest: throw QuestViewError.unexpected("transcript, HTTP 400")
        case .unauthorized: throw QuestViewError.unauthorized
        case .notFound: throw QuestViewError.notFound
        case .undocumented(let status, _): throw QuestViewError.unexpected("transcript, HTTP \(status)")
        }
    }

    public func steer(taskId: String, text: String, clientId: String) async throws -> HubTask {
        let answer = try await call {
            try await client.steerTask(path: .init(taskId: taskId), body: .json(.init(text: text, clientId: clientId)))
        }
        switch answer {
        case .accepted(let accepted): return try accepted.body.json
        case .badRequest(let bad):
            throw QuestViewError.badMessage((try? bad.body.json.message) ?? "The hub didn't accept that message.")
        case .unauthorized: throw QuestViewError.unauthorized
        case .notFound: throw QuestViewError.notFound
        // 409 `task_finished`: the only conflict a message has.
        case .conflict: throw QuestViewError.finished
        case .undocumented(let status, _): throw QuestViewError.unexpected("message, HTTP \(status)")
        }
    }

    public func stop(taskId: String) async throws -> HubTask {
        switch try await call({ try await client.stopTask(path: .init(taskId: taskId)) }) {
        case .ok(let ok): return try ok.body.json
        case .unauthorized: throw QuestViewError.unauthorized
        case .notFound: throw QuestViewError.notFound
        case .serviceUnavailable: throw QuestViewError.runnerUnreachable
        case .undocumented(let status, _): throw QuestViewError.unexpected("stop, HTTP \(status)")
        }
    }

    public func handBack(taskId: String, note: String?) async throws -> HubTask {
        let answer = try await call {
            try await client.handBackTask(path: .init(taskId: taskId), body: .json(.init(note: note)))
        }
        switch answer {
        case .accepted(let accepted): return try accepted.body.json
        case .badRequest: throw QuestViewError.unexpected("hand back, HTTP 400")
        case .unauthorized: throw QuestViewError.unauthorized
        case .notFound: throw QuestViewError.notFound
        case .undocumented(let status, _): throw QuestViewError.unexpected("hand back, HTTP \(status)")
        }
    }

    public func move(taskId: String, to projectId: String) async throws -> HubTask {
        let answer = try await call {
            try await client.moveTask(path: .init(taskId: taskId), body: .json(.init(projectId: projectId)))
        }
        switch answer {
        case .ok(let ok): return try ok.body.json
        case .conflict: throw QuestViewError.projectArchived
        case .notFound: throw QuestViewError.notFound
        case .unauthorized: throw QuestViewError.unauthorized
        case .badRequest: throw QuestViewError.unexpected("move, HTTP 400")
        case .undocumented(let status, _): throw QuestViewError.unexpected("move, HTTP \(status)")
        }
    }

    public func answer(approvalId: String, approve: Bool) async throws -> Approval {
        let answer = try await call {
            try await client.answerApproval(path: .init(approvalId: approvalId),
                                            body: .json(.init(decision: approve ? .approve : .deny)))
        }
        switch answer {
        case .ok(let ok): return try ok.body.json
        // 409: answered first somewhere else, or expired. The body is the approval as it stands.
        case .conflict(let conflict): throw QuestViewError.alreadyAnswered(try conflict.body.json)
        case .forbidden(let forbidden): throw QuestViewError.ownerOnly(try? forbidden.body.json.message)
        case .badRequest: throw QuestViewError.unexpected("answer, HTTP 400")
        case .unauthorized: throw QuestViewError.unauthorized
        case .notFound: throw QuestViewError.notFound
        case .undocumented(let status, _): throw QuestViewError.unexpected("answer, HTTP \(status)")
        }
    }

    /// Runs a request; a transport failure (no network, hub down) becomes `.offline`.
    private func call<T>(_ request: () async throws -> T) async throws -> T {
        do {
            return try await request()
        } catch let error as ClientError where error.underlyingError is URLError {
            throw QuestViewError.offline
        } catch is URLError {
            throw QuestViewError.offline
        }
    }
}
