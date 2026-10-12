import Foundation
import MarcelKit
import SwiftData

/// The router's lookups against this phone's cache and the paired hub.
@MainActor
public struct LiveLinkLookup: LinkLookup {
    let cache: MarcelCache
    let service: any SettingsService
    let main: () -> String?

    /// - Parameters:
    ///   - cache: the app's one cache (`connection.cache`).
    ///   - mainConversationId: the chat's main conversation, read when a link arrives.
    public init(cache: MarcelCache, service: any SettingsService, mainConversationId: @escaping () -> String?) {
        self.cache = cache
        self.service = service
        self.main = mainConversationId
    }

    public var mainConversationId: String? { main() }

    public func taskId(forApproval id: String) async throws -> String? {
        try await service.approvals().first { $0.id == id }?.taskId
    }

    public func projectId(slug: String) async throws -> String? {
        let context = cache.container.mainContext
        var fetch = FetchDescriptor<CachedProject>(predicate: #Predicate { $0.slug == slug && !$0.archived })
        fetch.fetchLimit = 1
        if let row = try context.fetch(fetch).first { return row.id }
        // Not cached yet (a project made a moment ago): ask the hub, for every agent this phone sees.
        for agent in try await service.me().agents {
            if let project = try await service.projects(agentId: agent.id).first(where: {
                $0.slug == slug && !$0.archived
            }) {
                try? cache.upsert(project)
                try? cache.save()
                return project.id
            }
        }
        return nil
    }

    public func message(id: String) -> (conversationId: String, taskId: String?)? {
        var fetch = FetchDescriptor<CachedMessage>(predicate: #Predicate { $0.id == id })
        fetch.fetchLimit = 1
        guard let row = try? cache.container.mainContext.fetch(fetch).first else { return nil }
        return (row.conversationId, row.taskId)
    }
}
