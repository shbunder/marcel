import Foundation
import MarcelKit

/// What getting into the app asks the hub.
public protocol WelcomeService: Sendable {
    /// Who this phone belongs to and their helper (`GET /me`).
    func me() async throws -> Me
    /// Onboarding's "Start chatting": the helper's name and look, and `onboarded: true`, in one `PATCH /agents/{id}`.
    func finish(agentId: String, name: String, look: KnownLook) async throws -> Agent
    /// "Sign out of this phone": the hub forgets this phone (`DELETE /devices/{id}`).
    func signOut(deviceId: String) async throws
}

/// Why something failed, in words for the phone.
public struct WelcomeError: Error, Equatable, Sendable {
    public let message: String

    public init(_ message: String) { self.message = message }

    public static let unreachable = WelcomeError("Can't reach the hub. Check the connection and try again.")
    public static let unpaired = WelcomeError("The hub doesn't know this phone any more.")
}

/// The hub through the generated client.
public struct LiveWelcomeService: WelcomeService {
    let client: Client

    public init(client: Client) { self.client = client }

    public func me() async throws -> Me {
        let output: Operations.GetMe.Output
        do { output = try await client.getMe() } catch { throw WelcomeError.unreachable }
        switch output {
        case .ok(let ok): return try ok.body.json
        case .unauthorized: throw WelcomeError.unpaired
        case .undocumented(let status, _): throw WelcomeError("The hub had a problem (\(status)).")
        }
    }

    public func finish(agentId: String, name: String, look: KnownLook) async throws -> Agent {
        let output: Operations.UpdateAgent.Output
        do {
            output = try await client.updateAgent(path: .init(agentId: agentId), body: .json(.init(
                name: name, look: .init(animal: look.animal, color: look.color, personality: look.personality),
                onboarded: true)))
        } catch { throw WelcomeError.unreachable }
        switch output {
        case .ok(let ok): return try ok.body.json
        case .badRequest(let bad): throw WelcomeError((try? bad.body.json.message) ?? "The hub didn't accept that.")
        case .unauthorized: throw WelcomeError.unpaired
        case .notFound: throw WelcomeError("Your helper isn't on the hub any more. Ask the owner to add you again.")
        case .undocumented(let status, _): throw WelcomeError("The hub had a problem (\(status)).")
        }
    }

    public func signOut(deviceId: String) async throws {
        let output: Operations.RevokeDevice.Output
        do { output = try await client.revokeDevice(path: .init(deviceId: deviceId)) } catch {
            throw WelcomeError.unreachable
        }
        switch output {
        case .noContent, .notFound, .unauthorized: return  // gone, or the hub had forgotten this phone already
        case .undocumented(let status, _): throw WelcomeError("The hub had a problem (\(status)).")
        }
    }
}
