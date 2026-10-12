import Foundation

/// One frame from `GET /ws`, written by hand from contracts/events.schema.json (generators do not read it).
public struct EventFrame: Sendable {
    /// Increases by one per event. `hello` and `resync.required` use 0 and are not part of the sequence.
    public let id: Int
    public let at: Date
    public let event: HubEvent
}

public enum HubEvent: Sendable {
    case hello(lastEventId: Int, replayed: Int)
    case resyncRequired(lastEventId: Int)
    case messageCreated(Message)
    case conversationCreated(Conversation)
    case taskCreated(HubTask)
    case taskUpdated(HubTask)
    case taskEvent(TaskEvent)
    case approvalCreated(Approval)
    case approvalResolved(Approval)
    case artifactCreated(Artifact)
    case agentState(agentId: String, state: AgentState)
    case usageUpdated(Usage)
    /// A type this build does not know yet. Kept so the event id still advances.
    case unknown(type: String)

    /// True for `hello` and `resync.required`, which sit outside the numbered sequence.
    public var isControl: Bool {
        switch self {
        case .hello, .resyncRequired: true
        default: false
        }
    }
}

extension EventFrame {
    private struct Head: Decodable {
        let id: Int
        let type: String
        let at: Date
    }

    private struct Body<Payload: Decodable>: Decodable {
        let data: Payload
    }

    private struct HelloData: Decodable {
        let last_event_id: Int
        let replayed: Int
    }

    private struct ResyncData: Decodable {
        let last_event_id: Int
    }

    private struct AgentStateData: Decodable {
        let agent_id: String
        let state: AgentState
    }

    public static func decode(_ data: Data) throws -> EventFrame {
        let decoder = HubDates.decoder()
        let head = try decoder.decode(Head.self, from: data)
        func payload<T: Decodable>(_: T.Type) throws -> T { try decoder.decode(Body<T>.self, from: data).data }
        let event: HubEvent
        switch head.type {
        case "hello":
            let hello = try payload(HelloData.self)
            event = .hello(lastEventId: hello.last_event_id, replayed: hello.replayed)
        case "resync.required":
            event = .resyncRequired(lastEventId: try payload(ResyncData.self).last_event_id)
        case "message.created": event = .messageCreated(try payload(Message.self))
        case "conversation.created": event = .conversationCreated(try payload(Conversation.self))
        case "task.created": event = .taskCreated(try payload(HubTask.self))
        case "task.updated": event = .taskUpdated(try payload(HubTask.self))
        case "task.event": event = .taskEvent(try payload(TaskEvent.self))
        case "approval.created": event = .approvalCreated(try payload(Approval.self))
        case "approval.resolved": event = .approvalResolved(try payload(Approval.self))
        case "artifact.created": event = .artifactCreated(try payload(Artifact.self))
        case "agent.state":
            let state = try payload(AgentStateData.self)
            event = .agentState(agentId: state.agent_id, state: state.state)
        case "usage.updated": event = .usageUpdated(try payload(Usage.self))
        default: event = .unknown(type: head.type)
        }
        return EventFrame(id: head.id, at: head.at, event: event)
    }
}
