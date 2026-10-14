import Foundation
import MarcelKit
import MarcelLook
import Observation

/// "Hi Emma! Meet your helper": the look picker and the helper's name, then "Start chatting", which saves both and
/// `onboarded: true` in one `PATCH /agents/{id}`.
@MainActor
@Observable
public final class OnboardingModel {
    /// The person's own name, from `GET /me`.
    public let personName: String
    public let agentId: String
    /// The helper's name, as typed; "Pick from my name" follows it.
    public var helperName: String {
        didSet { look.name = helperName }
    }
    public let look: LookModel
    public private(set) var problem: String?
    public private(set) var starting = false

    @ObservationIgnored private let service: any WelcomeService

    public init(me: Me, agent: Agent, service: any WelcomeService, looks: any LookService) {
        personName = me.name
        agentId = agent.id
        helperName = agent.name
        self.service = service
        look = LookModel(service: looks, agentId: agent.id, name: agent.name, current: agent.drawnLook)
    }

    /// "Hi Emma! Meet your helper".
    public var greeting: String { "Hi \(personName)! Meet your helper" }

    /// The name as it would be saved: trimmed, at most 40 characters (the contract's limit).
    var cleanName: String { String(helperName.trimmingCharacters(in: .whitespacesAndNewlines).prefix(40)) }

    public var canStart: Bool { !starting && !cleanName.isEmpty }

    /// Saves the name, the look and "onboarded". True when the hub took them; the app then opens the chat.
    public func startChatting() async -> Bool {
        guard canStart else {
            if cleanName.isEmpty { problem = "Give your helper a name first." }
            return false
        }
        starting = true
        defer { starting = false }
        do {
            _ = try await service.finish(agentId: agentId, name: cleanName, look: look.look)
            problem = nil
            return true
        } catch {
            problem = (error as? WelcomeError)?.message ?? WelcomeError.unreachable.message
            return false
        }
    }
}
