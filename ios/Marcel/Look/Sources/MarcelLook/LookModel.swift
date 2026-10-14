import Foundation
import MarcelKit
import Observation

/// The look picker's state: the look being chosen, its previews from the hub, and Save.
@MainActor
@Observable
public final class LookModel {
    /// The helper whose look this is, and its name (for "Pick from my name"; onboarding renames it as you type).
    public let agentId: String
    public var name: String
    /// What the hub has now; nil when the helper has no look yet (a new helper, onboarding).
    public private(set) var saved: KnownLook?
    /// The look being chosen.
    public var look: KnownLook
    /// Previews already drawn by the hub, by look (the same look always draws the same image).
    public private(set) var previews: [KnownLook: Data] = [:]
    /// A preview the hub could not draw (offline): the picker shows the words instead of the animal.
    public private(set) var previewProblem: String?
    /// What went wrong saving, in plain words.
    public private(set) var problem: String?
    public private(set) var saving = false

    @ObservationIgnored private let service: any LookService
    @ObservationIgnored private var asked: Set<KnownLook> = []

    /// - Parameter current: the helper's look now; without one the picker starts from the one its name gives.
    public init(service: any LookService, agentId: String, name: String, current: KnownLook?) {
        self.service = service
        self.agentId = agentId
        self.name = name
        self.saved = current
        self.look = current ?? LookFromName.pick(name)
    }

    /// Save is worth pressing: a change, or a helper that has no look yet.
    public var canSave: Bool { !saving && look != saved }

    public func choose(animal: LookAnimal) { look.animal = animal }
    public func choose(color: LookColor) { look.color = color }
    public func choose(personality: LookPersonality) { look.personality = personality }

    /// "Pick from my name": the look the Pixel Zoo gives this name.
    public func pickFromName() { look = LookFromName.pick(name) }

    /// The look with one part changed: what an animal's cell in the grid shows.
    public func trying(animal: LookAnimal) -> KnownLook {
        KnownLook(animal: animal, color: look.color, personality: look.personality)
    }

    /// Asks the hub to draw `look` once. Offline it says so; asking again later tries again.
    public func loadPreview(_ look: KnownLook) async {
        guard previews[look] == nil, asked.insert(look).inserted else { return }
        do {
            previews[look] = try await service.preview(look)
            previewProblem = nil
        } catch {
            asked.remove(look)
            previewProblem = (error as? LookError)?.message ?? LookError.unreachable.message
        }
    }

    /// Saves the look. True when the hub took it; the hub's `agent.updated` then moves every avatar on screen.
    @discardableResult
    public func save() async -> Agent? {
        guard canSave else { return nil }
        saving = true
        defer { saving = false }
        do {
            let agent = try await service.save(look, agentId: agentId)
            saved = agent.drawnLook ?? look
            problem = nil
            return agent
        } catch {
            problem = (error as? LookError)?.message ?? LookError.unreachable.message
            return nil
        }
    }
}
