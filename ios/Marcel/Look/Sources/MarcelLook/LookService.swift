import Foundation
import MarcelKit

/// What the picker asks the hub.
public protocol LookService: Sendable {
    /// One 32 × 32 PNG of any look (`GET /avatar-previews`).
    func preview(_ look: KnownLook) async throws -> Data
    /// Saves the agent's look (`PATCH /agents/{id}` with `look`). The hub then sends `agent.updated`, and every
    /// screen that shows the avatar fetches the new sheet.
    func save(_ look: KnownLook, agentId: String) async throws -> Agent
}

/// Why the hub said no, in words for the phone.
public struct LookError: Error, Equatable, Sendable {
    public let message: String

    public init(_ message: String) { self.message = message }

    public static let unreachable = LookError("Can't reach the hub. Check the connection and try again.")
    public static let unpaired = LookError("This phone isn't paired any more. Pair it again in Settings.")
}

/// The hub through the generated client.
public struct LiveLookService: LookService {
    let client: Client

    public init(client: Client) { self.client = client }

    public func preview(_ look: KnownLook) async throws -> Data {
        let output: Operations.GetAvatarPreview.Output
        do {
            output = try await client.getAvatarPreview(query: .init(
                animal: look.animal, color: look.color, personality: look.personality))
        } catch { throw LookError.unreachable }
        switch output {
        case .ok(let ok): return try await Data(collecting: try ok.body.png, upTo: 256 * 1024)
        case .badRequest: throw LookError("The hub doesn't know that look yet.")
        case .unauthorized: throw LookError.unpaired
        case .undocumented(let status, _): throw LookError("The hub had a problem drawing the preview (\(status)).")
        }
    }

    public func save(_ look: KnownLook, agentId: String) async throws -> Agent {
        let output: Operations.UpdateAgent.Output
        do {
            output = try await client.updateAgent(path: .init(agentId: agentId), body: .json(.init(
                look: .init(animal: look.animal, color: look.color, personality: look.personality))))
        } catch { throw LookError.unreachable }
        switch output {
        case .ok(let ok): return try ok.body.json
        case .badRequest(let bad): throw LookError((try? bad.body.json.message) ?? "The hub didn't accept that look.")
        case .unauthorized: throw LookError.unpaired
        case .notFound: throw LookError("This helper isn't on the hub any more.")
        case .undocumented(let status, _): throw LookError("The hub had a problem saving the look (\(status)).")
        }
    }
}
