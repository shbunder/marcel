import Foundation
import MarcelKit
import OpenAPIRuntime

public typealias MemoryFile = Components.Schemas.MemoryFile

/// One file in `GET /memory`, its path already checked.
public struct MemoryEntry: Equatable, Sendable {
    public var path: MemoryPath
    public var size: Int
    public var updatedAt: Date

    public init(path: MemoryPath, size: Int, updatedAt: Date) {
        self.path = path
        self.size = size
        self.updatedAt = updatedAt
    }
}

/// The memory tree: the files the app will open, and how many the hub listed under a name the app refuses.
public struct MemoryListing: Equatable, Sendable {
    public var files: [MemoryEntry]
    public var refused: Int
}

/// What a save came back with.
public enum SaveOutcome: Equatable, Sendable {
    /// Saved and committed; the file as it now stands.
    case saved(MemoryFile)
    /// 409: changed elsewhere since it was read (or created elsewhere). The hub's current version; nothing was saved.
    case changedElsewhere(MemoryFile)
}

/// What the notebook asks of the hub. The live one talks to `contracts/app-api.yaml`; the tests use a fake hub.
@MainActor
public protocol NotebookService {
    /// `GET /memory`.
    func listing() async throws -> MemoryListing
    /// `GET /memory/files/{path}`.
    func file(_ path: MemoryPath) async throws -> MemoryFile
    /// `PUT /memory/files/{path}` with `If-Match: <etag>`, or with no `If-Match` to create a new file. The etag goes
    /// bare (the contract allows it): the generated client writes header values URI-encoded, so quotes would arrive
    /// as `%22`.
    func save(_ path: MemoryPath, content: String, ifMatch etag: String?) async throws -> SaveOutcome
    /// `GET /agents/{id}/brain/rollovers?limit=…`: Marcel's past rollovers, newest first (app API 0.6).
    func rollovers(limit: Int) async throws -> [Rollover]
    /// `POST /agents/{id}/brain/rollover`: a fresh start now. Returns once the hub has started it (202); the
    /// `brain.rollover` event follows when the new session has taken over.
    func rollOver() async throws
}

/// Why something in the notebook did not work, in words a family member reads on a phone.
public enum NotebookError: Error, Equatable, LocalizedError {
    /// A bare 404, 405 or 501: the contract has the route, this hub has not built it yet.
    case notAvailableYet
    /// 404 `memory_file_not_found` on a save with `If-Match`: the file was removed since it was opened.
    case removed
    /// 404 on reading a file.
    case notFound
    /// 400 `memory_file_too_big`.
    case tooBig
    /// 400 for another reason; the hub says what.
    case refused(String)
    /// The path from the hub is not one the app will use (see `MemoryPath`).
    case badPath
    case unauthorized
    /// 503 `memory_unavailable`: the hub is up, its memory store is not. The hub's own words follow.
    case storeDown(String?)
    /// Any other 5xx.
    case hubProblem
    case offline
    case unexpected(String)
    /// 409 `rollover_running`: a fresh start is already on its way.
    case rolloverRunning
    /// 503 `runner_unreachable` on a fresh start: the hub can't start a session just now. Nothing changed.
    case cannotStartSession
    /// The hub lists no agent for this phone (`GET /me`), or `agent_not_found`.
    case noAgent

    public var errorDescription: String? {
        switch self {
        case .notAvailableYet: "Not available yet: the hub can't show Marcel's notebook yet."
        case .removed: "This note was removed since you opened it. Your text is still here: save again to create it."
        case .notFound: "That note is gone. Pull down to refresh."
        case .tooBig: "This note is over 1 MB, too big to open or save on the phone."
        case .refused(let message): message
        case .badPath: "The hub named a note the app won't open."
        case .unauthorized: "The hub no longer accepts this phone. Pair it again."
        case .storeDown(let why): "Marcel's notebook can't be reached on the hub right now. Nothing was changed."
            + (why.map { " (\($0))" } ?? "")
        case .hubProblem: "The hub had a problem. Try again in a minute."
        case .offline: "Couldn't reach the hub. Check the connection and try again."
        case .unexpected(let what): "The hub sent something the app doesn't understand (\(what))."
        case .rolloverRunning: "A fresh start is already on its way. Marcel says hello in the chat when it's ready."
        case .cannotStartSession: "The hub can't start a new session for Marcel right now. Nothing changed. Try again "
            + "in a few minutes."
        case .noAgent: "The hub doesn't list Marcel for this phone. Pull down to refresh."
        }
    }

    static func words(for error: any Error) -> String {
        (error as? NotebookError)?.errorDescription ?? "Something went wrong talking to the hub. Try again in a moment."
    }

    static func undocumented(_ status: Int, _ what: String) -> NotebookError {
        if [404, 405, 501].contains(status) { return .notAvailableYet }
        if (500...599).contains(status) { return .hubProblem }
        return .unexpected("\(what), HTTP \(status)")
    }

    /// A 404 the contract lists: the hub's generic `not_found` is a route it does not serve yet (before S-06.7);
    /// `agent_not_found` means the agent is gone.
    static func missing(_ error: HubError?) -> NotebookError {
        error?.code == "agent_not_found" ? .noAgent : .notAvailableYet
    }

    static func bad(_ error: HubError?, fallback: String) -> NotebookError {
        if error?.code == "memory_file_too_big" { return .tooBig }
        if error?.code == "invalid_memory_path" { return .badPath }
        return .refused(error?.message ?? fallback)
    }
}

/// The hub's REST API, signed with this phone's device token.
public struct LiveNotebookService: NotebookService {
    let client: Client

    public init(client: Client) {
        self.client = client
    }

    public func listing() async throws -> MemoryListing {
        switch try await call({ try await client.getMemoryTree() }) {
        case .ok(let ok):
            let files = try ok.body.json.files
            let entries = files.compactMap { file in
                MemoryPath(file.path).map { MemoryEntry(path: $0, size: file.sizeBytes, updatedAt: file.updatedAt) }
            }
            return MemoryListing(files: entries, refused: files.count - entries.count)
        case .unauthorized: throw NotebookError.unauthorized
        case .serviceUnavailable(let down): throw NotebookError.storeDown(try? down.body.json.message)
        case .undocumented(let status, _): throw NotebookError.undocumented(status, "the notebook")
        }
    }

    public func file(_ path: MemoryPath) async throws -> MemoryFile {
        // The generated client percent-encodes the whole path as one segment (`/` becomes `%2F`).
        switch try await call({ try await client.getMemoryFile(path: .init(path: path.raw)) }) {
        case .ok(let ok): return try checked(ok.body.json, path)
        case .badRequest(let bad): throw NotebookError.bad(try? bad.body.json, fallback: "The hub won't open that note.")
        case .unauthorized: throw NotebookError.unauthorized
        case .notFound: throw NotebookError.notFound
        case .serviceUnavailable(let down): throw NotebookError.storeDown(try? down.body.json.message)
        case .undocumented(let status, _): throw NotebookError.undocumented(status, "a note")
        }
    }

    public func save(_ path: MemoryPath, content: String, ifMatch etag: String?) async throws -> SaveOutcome {
        let answer = try await call {
            try await client.putMemoryFile(path: .init(path: path.raw), headers: .init(ifMatch: etag),
                                           body: .json(.init(content: content)))
        }
        switch answer {
        case .ok(let ok): return .saved(try checked(ok.body.json, path))
        case .conflict(let conflict): return .changedElsewhere(try checked(conflict.body.json, path))
        case .badRequest(let bad): throw NotebookError.bad(try? bad.body.json, fallback: "The hub didn't save that.")
        case .unauthorized: throw NotebookError.unauthorized
        case .notFound: throw NotebookError.removed
        case .serviceUnavailable(let down): throw NotebookError.storeDown(try? down.body.json.message)
        case .undocumented(let status, _): throw NotebookError.undocumented(status, "saving a note")
        }
    }

    public func rollovers(limit: Int) async throws -> [Rollover] {
        let agentId = try await agent()
        switch try await call({ try await client.listBrainRollovers(path: .init(agentId: agentId),
                                                                    query: .init(limit: limit)) }) {
        case .ok(let ok): return try ok.body.json.map(Rollover.init)
        case .unauthorized: throw NotebookError.unauthorized
        case .notFound(let missing): throw NotebookError.missing(try? missing.body.json)
        case .undocumented(let status, _): throw NotebookError.undocumented(status, "past fresh starts")
        }
    }

    public func rollOver() async throws {
        let agentId = try await agent()
        switch try await call({ try await client.rollOverBrain(path: .init(agentId: agentId)) }) {
        case .accepted: return
        case .unauthorized: throw NotebookError.unauthorized
        case .notFound(let missing): throw NotebookError.missing(try? missing.body.json)
        case .conflict: throw NotebookError.rolloverRunning
        case .serviceUnavailable: throw NotebookError.cannotStartSession
        case .undocumented(let status, _): throw NotebookError.undocumented(status, "a fresh start")
        }
    }

    /// The agent the notebook is about: the first in `GET /me` (a household has one agent per person).
    private func agent() async throws -> String {
        switch try await call({ try await client.getMe() }) {
        case .ok(let ok):
            guard let id = try ok.body.json.agents.first?.id else { throw NotebookError.noAgent }
            return id
        case .unauthorized: throw NotebookError.unauthorized
        case .undocumented(let status, _): throw NotebookError.undocumented(status, "who you are")
        }
    }

    /// The hub's answer must be about the file that was asked for.
    private func checked(_ file: MemoryFile, _ path: MemoryPath) throws -> MemoryFile {
        guard file.path == path.raw else { throw NotebookError.unexpected("a different note came back") }
        return file
    }

    /// Runs a request; a transport failure becomes `.offline`. A hub that has not built a route answers a bare 404 /
    /// 405 (`{"detail": "Not Found"}`, not the contract's `Error`), which fails to decode: "not available yet".
    private func call<T>(_ request: () async throws -> T) async throws -> T {
        do {
            return try await request()
        } catch let error as ClientError where [404, 405, 501].contains(error.response?.status.code ?? 0) {
            throw NotebookError.notAvailableYet
        } catch let error as ClientError where (500...599).contains(error.response?.status.code ?? 0) {
            throw NotebookError.hubProblem
        } catch let error as ClientError where error.underlyingError is URLError {
            throw NotebookError.offline
        } catch is URLError {
            throw NotebookError.offline
        }
    }
}
