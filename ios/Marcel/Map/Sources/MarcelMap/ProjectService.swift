import Foundation
import MarcelKit
import OpenAPIRuntime

/// What the map and the project rooms ask of the hub. The live one talks to `contracts/app-api.yaml`; the tests use a
/// fake. Everything else on screen comes from the cache, which the live event stream keeps current.
@MainActor
public protocol ProjectService {
    /// `GET /me` then `GET /projects` per agent: the live projects (the map, B-30).
    func projects() async throws -> [Project]
    /// `GET /artifacts?project_id=…`: a project's items and its board.
    func artifacts(projectId: String) async throws -> [Artifact]
    /// `GET /artifacts/{id}/content`: the board's JSON, for its rows.
    func artifactContent(id: String) async throws -> Data
    /// `GET /memory/files/memory%2Fprojects%2F<slug>.md`: the project's notes; nil when it has none yet.
    func notes(slug: String) async throws -> String?
    /// The agent's main conversation (`GET /me`): a room's messages go there (B-32).
    func mainConversationId() async throws -> String
    /// `POST /conversations/{id}/messages` with `project_id` set.
    func send(text: String, clientId: String, conversationId: String, projectId: String) async throws -> Message
    /// `POST /tasks/{id}/move` (B-31): the task, filed under the other project.
    func move(taskId: String, to projectId: String) async throws -> HubTask
}

/// Why something in a room did not work, in words a family member reads on a phone.
public enum MapError: Error, Equatable, LocalizedError {
    /// 409 `project_archived` on a move: the quest stayed where it was.
    case projectArchived
    /// 404 on a move: the hub no longer has the quest.
    case questGone
    /// 400 on a send: the hub does not know the project (any more).
    case unknownProject
    case unauthorized
    case notPaired
    case offline
    case unexpected(String)

    public var errorDescription: String? {
        switch self {
        case .projectArchived: "That project is archived, so the quest stayed where it was."
        case .questGone: "The hub no longer has this quest."
        case .unknownProject: "The hub doesn't know this project any more. Go back to the map and try again."
        case .unauthorized: "The hub no longer accepts this phone. Pair it again."
        case .notPaired: "This phone isn't paired with a hub yet."
        case .offline: "Couldn't reach the hub. Check the connection and try again."
        case .unexpected(let what): "The hub sent something the app doesn't understand (\(what))."
        }
    }

    /// Any error, as the screen says it.
    static func words(for error: any Error) -> String {
        (error as? MapError)?.errorDescription ?? "Something went wrong talking to the hub. Try again in a moment."
    }
}

/// The hub's REST API, signed with this phone's device token.
public struct LiveProjectService: ProjectService {
    let client: Client

    public init(client: Client) {
        self.client = client
    }

    /// The paired hub as pairing left it (address in UserDefaults, token in the Keychain); nil when not paired.
    public static func paired(defaults: UserDefaults = .standard) -> LiveProjectService? {
        guard let hub = UserDefaultsPairedHubStore(defaults: defaults).load() else { return nil }
        return LiveProjectService(client: HubClient.make(serverURL: hub.serverURL, tokenStore: KeychainTokenStore()))
    }

    private func me() async throws -> Components.Schemas.Me {
        switch try await call({ try await client.getMe() }) {
        case .ok(let ok): return try ok.body.json
        case .unauthorized: throw MapError.unauthorized
        case .undocumented(let status, _): throw MapError.unexpected("me, HTTP \(status)")
        }
    }

    public func projects() async throws -> [Project] {
        var projects: [Project] = []
        // The buddy this phone shows (B-49).
        for agent in [await CurrentBuddy.chosen(from: try await me().agents)].compactMap({ $0 }) {
            switch try await call({ try await client.listProjects(query: .init(agentId: agent.id)) }) {
            case .ok(let ok): projects += try ok.body.json
            case .unauthorized: throw MapError.unauthorized
            case .undocumented(let status, _): throw MapError.unexpected("projects, HTTP \(status)")
            }
        }
        return projects
    }

    public func artifacts(projectId: String) async throws -> [Artifact] {
        switch try await call({ try await client.listArtifacts(query: .init(projectId: projectId, limit: 200)) }) {
        case .ok(let ok): return try ok.body.json.items
        case .unauthorized: throw MapError.unauthorized
        case .badRequest: throw MapError.unexpected("items, HTTP 400")
        case .undocumented(let status, _): throw MapError.unexpected("items, HTTP \(status)")
        }
    }

    public func artifactContent(id: String) async throws -> Data {
        switch try await call({ try await client.getArtifactContent(path: .init(artifactId: id)) }) {
        case .ok(let ok):
            switch ok.body {
            case .any(let body): return try await Data(collecting: body, upTo: 256 * 1024)
            }
        case .unauthorized: throw MapError.unauthorized
        case .notFound: throw MapError.unexpected("board, HTTP 404")
        case .badRequest: throw MapError.unexpected("board, HTTP 400")
        case .undocumented(let status, _): throw MapError.unexpected("board, HTTP \(status)")
        }
    }

    public func notes(slug: String) async throws -> String? {
        let path = "memory/projects/\(slug).md"
        switch try await call({ try await client.getMemoryFile(path: .init(path: path)) }) {
        case .ok(let ok): return try ok.body.json.content
        case .notFound: return nil
        case .unauthorized: throw MapError.unauthorized
        case .badRequest: throw MapError.unexpected("notes, HTTP 400")
        case .serviceUnavailable: throw MapError.unexpected("notes, HTTP 503")
        case .undocumented(let status, _): throw MapError.unexpected("notes, HTTP \(status)")
        }
    }

    public func mainConversationId() async throws -> String {
        guard let agent = await CurrentBuddy.chosen(from: try await me().agents) else {
            throw MapError.unexpected("no agent")
        }
        return agent.mainConversationId
    }

    public func send(text: String, clientId: String, conversationId: String, projectId: String) async throws
        -> Message
    {
        let answer = try await call {
            try await client.sendMessage(
                path: .init(conversationId: conversationId),
                body: .json(.init(text: text, clientId: clientId, projectId: projectId)))
        }
        switch answer {
        case .created(let created): return try created.body.json
        case .badRequest: throw MapError.unknownProject
        case .unauthorized: throw MapError.unauthorized
        case .notFound: throw MapError.unexpected("send, HTTP 404")
        case .conflict: throw MapError.unexpected("send, HTTP 409")
        case .undocumented(let status, _): throw MapError.unexpected("send, HTTP \(status)")
        }
    }

    public func move(taskId: String, to projectId: String) async throws -> HubTask {
        let answer = try await call {
            try await client.moveTask(path: .init(taskId: taskId), body: .json(.init(projectId: projectId)))
        }
        switch answer {
        case .ok(let ok): return try ok.body.json
        // 409 `project_archived`: the only conflict a move has.
        case .conflict: throw MapError.projectArchived
        case .notFound: throw MapError.questGone
        case .unauthorized: throw MapError.unauthorized
        case .badRequest: throw MapError.unexpected("move, HTTP 400")
        case .undocumented(let status, _): throw MapError.unexpected("move, HTTP \(status)")
        }
    }

    /// Runs a request; a transport failure (no network, hub down) becomes `.offline`.
    private func call<T>(_ request: () async throws -> T) async throws -> T {
        do {
            return try await request()
        } catch let error as ClientError where error.underlyingError is URLError {
            throw MapError.offline
        } catch is URLError {
            throw MapError.offline
        }
    }
}
