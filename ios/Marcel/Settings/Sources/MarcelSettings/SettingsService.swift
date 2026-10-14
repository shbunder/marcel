import Foundation
import MarcelKit
import OpenAPIRuntime
import OpenAPIURLSession

public typealias Me = Components.Schemas.Me
public typealias Device = Components.Schemas.Device
public typealias Schedule = Components.Schemas.Schedule
public typealias Health = Components.Schemas.Health
public typealias Agent = Components.Schemas.Agent
public typealias IssuedPairingCode = Components.Schemas.IssuedPairingCode

/// What Settings, the status screen and the link router ask of the hub. The live one talks to
/// `contracts/app-api.yaml`; the tests use a fake hub.
@MainActor
public protocol SettingsService {
    /// `GET /me`: who this phone is, the agents, and which push backends the hub has.
    func me() async throws -> Me
    /// `PATCH /agents/{id}` with a new name.
    func rename(agentId: String, to name: String) async throws -> Agent
    /// `GET /devices`.
    func devices() async throws -> [Device]
    /// `DELETE /devices/{id}`.
    func revoke(deviceId: String) async throws
    /// `PUT /devices/{id}/push`.
    func setPush(deviceId: String, target: Components.Schemas.PushTarget) async throws -> Device
    /// `GET /schedules?agent_id=…`.
    func schedules(agentId: String) async throws -> [Schedule]
    /// `PATCH /schedules/{id}` with `paused`.
    func setPaused(scheduleId: String, _ paused: Bool) async throws -> Schedule
    /// `DELETE /schedules/{id}`.
    func delete(scheduleId: String) async throws
    /// `GET /usage`.
    func usage() async throws -> Usage
    /// `GET /health`.
    func health() async throws -> Health
    /// `GET /approvals`.
    func approvals() async throws -> [Approval]
    /// `GET /approvals/{id}`, in any state; nil when the hub has no such approval (404 `approval_not_found`).
    func approval(id: String) async throws -> Approval?
    /// `GET /messages/{id}`, in any of your conversations; nil when the hub has no such message (404
    /// `message_not_found`).
    func message(id: String) async throws -> Message?
    /// `GET /projects?agent_id=…`.
    func projects(agentId: String) async throws -> [Project]
    /// `POST /approvals/{id}`.
    func answer(approvalId: String, approve: Bool) async throws
    /// `GET /users`: everyone, owner first. Owner only.
    func users() async throws -> [User]
    /// `POST /users`: a family member and their helper (its look picked from the helper's name). Owner only.
    func addUser(name: String, helperName: String) async throws -> User
    /// `POST /users/{id}/pairing-codes`: a one-time code for their phone, ten minutes. Owner only.
    func pairingCode(userId: String) async throws -> IssuedPairingCode
    /// `DELETE /users/{id}`: their phones are signed out and their helper stops. Owner only.
    func removeUser(userId: String) async throws
}

/// Why something in Settings did not work, in words a family member reads on a phone.
public enum SettingsError: Error, Equatable, LocalizedError {
    /// The hub answered 404, 405 or 501 for a route the contract has but this hub has not built yet
    /// (schedules: S-09.1, usage: S-10.1, renaming the agent).
    case notAvailableYet
    /// 503 `no_reading` on `GET /usage`: the runner has not read the plan's usage since it started.
    case noReading
    /// 409 `schedule_builtin` on deleting a built-in digest: it can be paused, not deleted.
    case builtIn
    /// 404 `schedule_not_found`: the schedule was already deleted (elsewhere, or by Marcel).
    case scheduleGone
    /// An APNs token that is not 64 to 200 hex digits (the contract's pattern since app API 0.6): never sent, since the
    /// hub refuses it and Apple would answer `BadDeviceToken`.
    case badPushToken
    /// 409 on an approval: answered first (a terminal, another phone) or expired.
    case alreadyAnswered
    /// 403 `owner_only` (app API 0.7): a family member's phone tried an owner-only action, such as approving a
    /// change to Marcel itself. Carries the hub's sentence, which names the owner.
    case ownerOnly(String?)
    /// 400: the hub says what to change.
    case refused(String)
    case notFound
    case unauthorized
    /// A 5xx (other than 501): the hub is up but something on it failed.
    case hubProblem
    /// No hub address or no device token on this phone.
    case notPaired
    case offline
    case unexpected(String)

    public var errorDescription: String? {
        switch self {
        case .notAvailableYet: "Not available yet: the hub doesn't do this yet."
        case .noReading: "Usage unavailable: the hub hasn't read the plan's usage yet. It shows here once it has."
        case .builtIn: "Built-in digests can't be deleted, only paused. A paused digest stops until you resume it."
        case .scheduleGone: "That schedule was already deleted."
        case .badPushToken: "Apple gave this phone a push token the hub can't use, so notifications weren't turned on. "
            + "Try again in a minute."
        case .alreadyAnswered: "That question was already answered, or it expired."
        case .ownerOnly(let message): message ?? "Only the owner can answer this. Ask them to approve it on their phone."
        case .refused(let message): message
        case .notFound: "The hub no longer has that. Pull down to refresh."
        case .unauthorized: "The hub no longer accepts this phone. Pair it again."
        case .hubProblem: "The hub had a problem. Try again in a minute."
        case .notPaired: "This phone isn't paired with a hub."
        case .offline: "Couldn't reach the hub. Check the connection and try again."
        case .unexpected(let what): "The hub sent something the app doesn't understand (\(what))."
        }
    }

    static func words(for error: any Error) -> String {
        (error as? SettingsError)?.errorDescription
            ?? "Something went wrong talking to the hub. Try again in a moment."
    }

    /// A 404 the contract lists: the hub's generic `not_found` is a route it does not serve yet (an older hub);
    /// any specific code (`device_not_found`, …) means the thing is gone.
    static func missing(_ error: HubError?) -> SettingsError {
        error?.code == "not_found" ? .notAvailableYet : .notFound
    }

    /// A status the contract does not list for this route. 404, 405 and 501 mean the hub has not built it yet;
    /// any other 5xx means the hub hit a problem of its own.
    static func undocumented(_ status: Int, _ what: String) -> SettingsError {
        if [404, 405, 501].contains(status) { return .notAvailableYet }
        if (500...599).contains(status) { return .hubProblem }
        return .unexpected("\(what), HTTP \(status)")
    }
}

/// The hub's REST API, signed with this phone's device token.
public struct LiveSettingsService: SettingsService {
    let client: Client

    public init(client: Client) {
        self.client = client
    }

    /// The hub this phone is paired with, as pairing left it (address in `hubStore`, token in `tokenStore`); nil
    /// when either is missing. Read at the moment it is needed, so it also works when iOS starts the app in the
    /// background for a notification action, before any screen exists.
    public static func paired(hubStore: any PairedHubStore, tokenStore: any TokenStore,
                              transport: any ClientTransport = HubClient.transport) -> (LiveSettingsService, PairedHub)? {
        guard let hub = hubStore.load(), (try? tokenStore.token()) != nil else { return nil }
        return (LiveSettingsService(client: HubClient.make(serverURL: hub.serverURL, tokenStore: tokenStore,
                                                           transport: transport)), hub)
    }

    public func me() async throws -> Me {
        switch try await call({ try await client.getMe() }) {
        case .ok(let ok): return try ok.body.json
        case .unauthorized: throw SettingsError.unauthorized
        case .undocumented(let status, _): throw SettingsError.undocumented(status, "who you are")
        }
    }

    public func rename(agentId: String, to name: String) async throws -> Agent {
        let answer = try await call {
            try await client.updateAgent(path: .init(agentId: agentId), body: .json(.init(name: name)))
        }
        switch answer {
        case .ok(let ok): return try ok.body.json
        case .badRequest(let bad):
            throw SettingsError.refused((try? bad.body.json.message) ?? "The hub didn't accept that name.")
        case .unauthorized: throw SettingsError.unauthorized
        case .notFound(let missing): throw SettingsError.missing(try? missing.body.json)
        case .undocumented(let status, _): throw SettingsError.undocumented(status, "rename")
        }
    }

    public func devices() async throws -> [Device] {
        switch try await call({ try await client.listDevices() }) {
        case .ok(let ok): return try ok.body.json
        case .unauthorized: throw SettingsError.unauthorized
        case .undocumented(let status, _): throw SettingsError.undocumented(status, "devices")
        }
    }

    public func revoke(deviceId: String) async throws {
        switch try await call({ try await client.revokeDevice(path: .init(deviceId: deviceId)) }) {
        case .noContent: return
        case .unauthorized: throw SettingsError.unauthorized
        case .notFound(let missing): throw SettingsError.missing(try? missing.body.json)
        case .undocumented(let status, _): throw SettingsError.undocumented(status, "revoke")
        }
    }

    public func setPush(deviceId: String, target: Components.Schemas.PushTarget) async throws -> Device {
        if target.kind == .apns, !APNsToken.isValid(target.apnsToken) { throw SettingsError.badPushToken }
        let answer = try await call {
            try await client.setDevicePush(path: .init(deviceId: deviceId), body: .json(target))
        }
        switch answer {
        case .ok(let ok): return try ok.body.json
        case .badRequest(let bad):
            throw SettingsError.refused((try? bad.body.json.message) ?? "The hub didn't accept that.")
        case .unauthorized: throw SettingsError.unauthorized
        case .notFound(let missing): throw SettingsError.missing(try? missing.body.json)
        case .undocumented(let status, _): throw SettingsError.undocumented(status, "notifications")
        }
    }

    public func schedules(agentId: String) async throws -> [Schedule] {
        switch try await call({ try await client.listSchedules(query: .init(agentId: agentId)) }) {
        case .ok(let ok): return try ok.body.json
        case .unauthorized: throw SettingsError.unauthorized
        case .undocumented(let status, _): throw SettingsError.undocumented(status, "schedules")
        }
    }

    public func setPaused(scheduleId: String, _ paused: Bool) async throws -> Schedule {
        let answer = try await call {
            try await client.updateSchedule(path: .init(scheduleId: scheduleId), body: .json(.init(paused: paused)))
        }
        switch answer {
        case .ok(let ok): return try ok.body.json
        case .badRequest(let bad):
            throw SettingsError.refused((try? bad.body.json.message) ?? "The hub didn't accept that.")
        case .unauthorized: throw SettingsError.unauthorized
        case .notFound(let missing): throw SettingsError.missing(try? missing.body.json)
        case .undocumented(let status, _): throw SettingsError.undocumented(status, "schedule")
        }
    }

    public func delete(scheduleId: String) async throws {
        switch try await call({ try await client.deleteSchedule(path: .init(scheduleId: scheduleId)) }) {
        case .noContent: return
        case .unauthorized: throw SettingsError.unauthorized
        case .notFound(let missing):
            let error = try? missing.body.json
            throw error?.code == "schedule_not_found" ? SettingsError.scheduleGone : SettingsError.missing(error)
        case .conflict: throw SettingsError.builtIn
        case .undocumented(let status, _): throw SettingsError.undocumented(status, "schedule")
        }
    }

    public func usage() async throws -> Usage {
        switch try await call({ try await client.getUsage() }) {
        case .ok(let ok): return try ok.body.json
        case .unauthorized: throw SettingsError.unauthorized
        case .serviceUnavailable(let unavailable):
            // `no_reading` is the one 503 the contract lists; any other is the hub's own trouble.
            throw (try? unavailable.body.json.code) == "no_reading" ? SettingsError.noReading : SettingsError.hubProblem
        case .undocumented(let status, _): throw SettingsError.undocumented(status, "usage")
        }
    }

    public func health() async throws -> Health {
        switch try await call({ try await client.getHealth() }) {
        case .ok(let ok): return try ok.body.json
        case .undocumented(let status, _): throw SettingsError.undocumented(status, "health")
        }
    }

    public func approvals() async throws -> [Approval] {
        switch try await call({ try await client.listApprovals() }) {
        case .ok(let ok): return try ok.body.json
        case .unauthorized: throw SettingsError.unauthorized
        case .undocumented(let status, _): throw SettingsError.undocumented(status, "questions")
        }
    }

    public func approval(id: String) async throws -> Approval? {
        switch try await call({ try await client.getApproval(path: .init(approvalId: id)) }) {
        case .ok(let ok): return try ok.body.json
        // Only `approval_not_found` means gone; the hub's `not_found` is a route it does not serve yet.
        case .notFound(let missing): return try Self.gone(missing.body.json, code: "approval_not_found")
        case .unauthorized: throw SettingsError.unauthorized
        case .undocumented(let status, _): throw SettingsError.undocumented(status, "question")
        }
    }

    public func message(id: String) async throws -> Message? {
        switch try await call({ try await client.getMessage(path: .init(messageId: id)) }) {
        case .ok(let ok): return try ok.body.json
        // Only `message_not_found` means gone; the hub's `not_found` is a route it does not serve yet.
        case .notFound(let missing): return try Self.gone(missing.body.json, code: "message_not_found")
        case .unauthorized: throw SettingsError.unauthorized
        case .undocumented(let status, _): throw SettingsError.undocumented(status, "message")
        }
    }

    /// A 404 by its code: `code` means the thing is gone (nil); anything else (the hub's generic `not_found` for a
    /// route an older hub does not serve) is "not available yet", so the caller falls back.
    private static func gone<T>(_ error: HubError, code: String) throws -> T? {
        guard error.code == code else { throw SettingsError.notAvailableYet }
        return nil
    }

    public func projects(agentId: String) async throws -> [Project] {
        switch try await call({ try await client.listProjects(query: .init(agentId: agentId)) }) {
        case .ok(let ok): return try ok.body.json
        case .unauthorized: throw SettingsError.unauthorized
        case .undocumented(let status, _): throw SettingsError.undocumented(status, "projects")
        }
    }

    public func answer(approvalId: String, approve: Bool) async throws {
        let answer = try await call {
            try await client.answerApproval(path: .init(approvalId: approvalId),
                                            body: .json(.init(decision: approve ? .approve : .deny)))
        }
        switch answer {
        case .ok: return
        case .conflict: throw SettingsError.alreadyAnswered
        case .forbidden(let forbidden): throw SettingsError.ownerOnly(try? forbidden.body.json.message)
        case .badRequest: throw SettingsError.unexpected("answer, HTTP 400")
        case .unauthorized: throw SettingsError.unauthorized
        case .notFound: throw SettingsError.alreadyAnswered
        case .undocumented(let status, _): throw SettingsError.undocumented(status, "answer")
        }
    }

    // MARK: People (B-37)

    public func users() async throws -> [User] {
        switch try await call({ try await client.listUsers() }) {
        case .ok(let ok): return try ok.body.json
        case .unauthorized: throw SettingsError.unauthorized
        case .forbidden(let no): throw SettingsError.ownerOnly(try? no.body.json.message)
        case .undocumented(let status, _): throw SettingsError.undocumented(status, "people")
        }
    }

    public func addUser(name: String, helperName: String) async throws -> User {
        let answer = try await call {
            try await client.addUser(body: .json(.init(name: name, agent: .init(name: helperName))))
        }
        switch answer {
        case .created(let created): return try created.body.json
        case .badRequest(let bad):
            throw SettingsError.refused((try? bad.body.json.message) ?? "The hub didn't accept those names.")
        case .unauthorized: throw SettingsError.unauthorized
        case .forbidden(let no): throw SettingsError.ownerOnly(try? no.body.json.message)
        case .undocumented(let status, _): throw SettingsError.undocumented(status, "add person")
        }
    }

    public func pairingCode(userId: String) async throws -> IssuedPairingCode {
        switch try await call({ try await client.createPairingCode(path: .init(userId: userId)) }) {
        case .created(let created): return try created.body.json
        case .unauthorized: throw SettingsError.unauthorized
        case .forbidden(let no): throw SettingsError.ownerOnly(try? no.body.json.message)
        case .notFound: throw SettingsError.notFound
        case .undocumented(let status, _): throw SettingsError.undocumented(status, "pairing code")
        }
    }

    public func removeUser(userId: String) async throws {
        switch try await call({ try await client.removeUser(path: .init(userId: userId)) }) {
        case .noContent, .notFound: return  // gone, or already gone
        case .unauthorized: throw SettingsError.unauthorized
        case .forbidden(let no): throw SettingsError.ownerOnly(try? no.body.json.message)
        case .conflict(let no): throw SettingsError.refused((try? no.body.json.message) ?? "The owner can't be removed.")
        case .undocumented(let status, _): throw SettingsError.undocumented(status, "remove person")
        }
    }

    /// Runs a request; a transport failure (no network, hub down) becomes `.offline`. A hub that has not built a
    /// route answers a bare 404 / 405 (`{"detail": "Not Found"}`, not the contract's `Error`), which fails to decode:
    /// that, or a 501, is "not available yet".
    private func call<T>(_ request: () async throws -> T) async throws -> T {
        do {
            return try await request()
        } catch let error as ClientError where [404, 405, 501].contains(error.response?.status.code ?? 0) {
            throw SettingsError.notAvailableYet
        } catch let error as ClientError where (500...599).contains(error.response?.status.code ?? 0) {
            throw SettingsError.hubProblem
        } catch let error as ClientError where error.underlyingError is URLError {
            throw SettingsError.offline
        } catch is URLError {
            throw SettingsError.offline
        }
    }
}
