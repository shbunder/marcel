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
