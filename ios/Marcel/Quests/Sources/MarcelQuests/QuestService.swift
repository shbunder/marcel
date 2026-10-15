import Foundation
import MarcelKit
import OpenAPIRuntime

/// What the quest log asks of the hub. The live one talks to `contracts/app-api.yaml`; the tests use a fake.
@MainActor
public protocol QuestService {
    /// `POST /tasks/{id}/stop`: the task as it now stands.
    func stop(taskId: String) async throws -> HubTask
    /// `POST /tasks/{id}/archive`: the task, now archived.
    func archive(taskId: String) async throws -> HubTask
    /// `GET /tasks?archived=true` for every agent: the archived tasks, which the live stream does not replay.
    func archivedTasks() async throws -> [HubTask]
}

/// Why a swipe did not work, in words a family member reads on a phone.
public enum QuestActionError: Error, Equatable, LocalizedError {
    /// 503 `runner_unreachable`: the task was left running.
    case runnerUnreachable
    /// 409: only a done, failed or stopped quest can be archived.
    case notFinished
    /// 404: the hub no longer has it.
    case notFound
    case unauthorized
    case notPaired
    case offline
    case unexpected(String)

    public var errorDescription: String? {
        switch self {
        case .runnerUnreachable: "The NUC didn't answer, so the quest is still running. Try again in a minute."
        case .notFinished: "Only a finished quest can be archived."
        case .notFound: "The hub no longer has this quest."
        case .unauthorized: "The hub no longer accepts this phone. Pair it again."
        case .notPaired: "This phone isn't paired with a hub yet."
        case .offline: "Couldn't reach the hub. Check the connection and try again."
        case .unexpected(let what): "The hub sent something the app doesn't understand (\(what))."
        }
    }
}

/// The hub's REST API, signed with this phone's device token.
public struct LiveQuestService: QuestService {
    let client: Client

    public init(client: Client) {
        self.client = client
    }

    /// The paired hub as pairing left it (address in UserDefaults, token in the Keychain); nil when not paired.
    public static func paired(defaults: UserDefaults = .standard) -> LiveQuestService? {
        guard let hub = UserDefaultsPairedHubStore(defaults: defaults).load() else { return nil }
        return LiveQuestService(client: HubClient.make(serverURL: hub.serverURL, tokenStore: KeychainTokenStore()))
    }

    public func stop(taskId: String) async throws -> HubTask {
        let answer = try await call { try await client.stopTask(path: .init(taskId: taskId)) }
        switch answer {
        case .ok(let ok): return try ok.body.json
        case .unauthorized: throw QuestActionError.unauthorized
        case .notFound: throw QuestActionError.notFound
        // 503 runner_unreachable: the runner did not answer, so the task was left as it was.
        case .serviceUnavailable: throw QuestActionError.runnerUnreachable
        case .undocumented(let status, _): throw QuestActionError.unexpected("stop, HTTP \(status)")
        }
    }

    public func archive(taskId: String) async throws -> HubTask {
        let answer = try await call { try await client.archiveTask(path: .init(taskId: taskId)) }
        switch answer {
        case .ok(let ok): return try ok.body.json
        case .unauthorized: throw QuestActionError.unauthorized
        case .notFound: throw QuestActionError.notFound
        case .conflict: throw QuestActionError.notFinished
        case .undocumented(let status, _): throw QuestActionError.unexpected("archive, HTTP \(status)")
        }
    }

    public func archivedTasks() async throws -> [HubTask] {
        let me: Components.Schemas.Me
        switch try await call({ try await client.getMe() }) {
        case .ok(let ok): me = try ok.body.json
        case .unauthorized: throw QuestActionError.unauthorized
        case .undocumented(let status, _): throw QuestActionError.unexpected("me, HTTP \(status)")
        }
        var tasks: [HubTask] = []
        // The buddy this phone shows (B-49).
        for agent in [await CurrentBuddy.chosen(from: me.agents)].compactMap({ $0 }) {
            var cursor: String?
            // Pages of 200; ten pages is far more than a family archives, and keeps a bad cursor from looping.
            for _ in 0..<10 {
                let answer = try await call {
                    try await client.listTasks(query: .init(agentId: agent.id, archived: true, cursor: cursor, limit: 200))
                }
                switch answer {
                case .ok(let ok):
                    let page = try ok.body.json
                    tasks += page.items
                    cursor = page.nextCursor
                case .unauthorized: throw QuestActionError.unauthorized
                case .badRequest: throw QuestActionError.unexpected("archived tasks, HTTP 400")
                case .undocumented(let status, _): throw QuestActionError.unexpected("archived tasks, HTTP \(status)")
                }
                if cursor == nil { break }
            }
        }
        return tasks
    }

    /// Runs a request; a transport failure (no network, hub down) becomes `.offline`.
    private func call<T>(_ request: () async throws -> T) async throws -> T {
        do {
            return try await request()
        } catch let error as ClientError where error.underlyingError is URLError {
            throw QuestActionError.offline
        } catch is URLError {
            throw QuestActionError.offline
        }
    }
}
