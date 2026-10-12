import Foundation

public enum RefreshError: Error, LocalizedError, Equatable {
    case unauthorized
    case unexpectedAnswer(String)

    public var errorDescription: String? {
        switch self {
        case .unauthorized: "The hub no longer accepts this phone. Pair it again."
        case .unexpectedAnswer(let what): "The hub sent something the app does not understand (\(what))."
        }
    }
}

/// Re-fetches the newest page of every conversation, the tasks and the artifacts over REST.
@MainActor
public struct RESTCacheRefresher: CacheRefresher {
    let client: Client

    public init(client: Client) {
        self.client = client
    }

    public func refresh(into cache: MarcelCache) async throws {
        let me: Components.Schemas.Me
        switch try await client.getMe() {
        case .ok(let ok): me = try ok.body.json
        case .unauthorized: throw RefreshError.unauthorized
        case .undocumented(let status, _): throw RefreshError.unexpectedAnswer("me, HTTP \(status)")
        }
        for agent in me.agents {
            switch try await client.listConversations(query: .init(agentId: agent.id)) {
            case .ok(let ok):
                for conversation in try ok.body.json {
                    if case .ok(let page) = try await client.listMessages(path: .init(conversationId: conversation.id)) {
                        for message in try page.body.json.items { try cache.upsert(message) }
                    }
                }
            case .unauthorized: throw RefreshError.unauthorized
            case .undocumented(let status, _): throw RefreshError.unexpectedAnswer("conversations, HTTP \(status)")
            }
            if case .ok(let ok) = try await client.listTasks(query: .init(agentId: agent.id)) {
                for task in try ok.body.json.items { try cache.upsert(task) }
            }
        }
        if case .ok(let ok) = try await client.listArtifacts() {
            for artifact in try ok.body.json.items { try cache.upsert(artifact) }
        }
        try cache.save()
    }
}
