@_exported import MarcelAPI

// Short names for the contract's schemas. The types themselves are generated from contracts/app-api.yaml.
public typealias Message = Components.Schemas.Message
public typealias Conversation = Components.Schemas.Conversation
public typealias HubTask = Components.Schemas.Task
public typealias TaskEvent = Components.Schemas.TaskEvent
public typealias Approval = Components.Schemas.Approval
public typealias Artifact = Components.Schemas.Artifact
public typealias ArtifactVersion = Components.Schemas.ArtifactVersion
public typealias Project = Components.Schemas.Project
public typealias AgentState = Components.Schemas.AgentState
public typealias Usage = Components.Schemas.Usage
public typealias HubError = Components.Schemas._Error
// app API 0.7.0: people, agent looks (Pixel Zoo), quoted replies and mentions.
public typealias Me = Components.Schemas.Me
public typealias User = Components.Schemas.User
public typealias UserRole = Components.Schemas.UserRole
public typealias Agent = Components.Schemas.Agent
public typealias Look = Components.Schemas.Look
public typealias LookAnimal = Components.Schemas.LookAnimal
public typealias LookColor = Components.Schemas.LookColor
public typealias LookPersonality = Components.Schemas.LookPersonality
public typealias Mention = Components.Schemas.Mention
public typealias MentionKind = Components.Schemas.MentionKind
public typealias NewLook = Components.Schemas.NewLook

/// A look the app can draw: every value is one this build knows.
public struct KnownLook: Equatable, Hashable, Sendable {
    public var animal: LookAnimal
    public var color: LookColor
    public var personality: LookPersonality

    public init(animal: LookAnimal, color: LookColor, personality: LookPersonality) {
        self.animal = animal
        self.color = color
        self.personality = personality
    }
}

extension Look {
    /// The look as values this build knows, or nil when any value is new to it (app API 0.7.1: the hub sends looks as
    /// open strings because the Pixel Zoo grows). Nil means: draw the default avatar, the bundled giraffe.
    public var known: KnownLook? {
        guard let animal = LookAnimal(rawValue: animal), let color = LookColor(rawValue: color),
              let personality = LookPersonality(rawValue: personality) else { return nil }
        return KnownLook(animal: animal, color: color, personality: personality)
    }
}

extension Agent {
    /// The look to draw, or nil for the default avatar: no look yet, or a look with a value this build does not know.
    public var drawnLook: KnownLook? { look?.known }
}
