import Foundation
import MarcelKit
import Observation

/// The chat's state beyond what the cache holds: which conversation, how much of it is on screen, and what the
/// person is doing right now. The rows themselves come from the cache (`FeedInput`).
@MainActor
@Observable
public final class ChatModel {
    /// How many messages one step up the feed loads from the cache.
    public static let pageSize = 50

    public private(set) var conversationId: String?
    /// How many of the newest messages the feed shows; grows by `pageSize` as you scroll up.
    public private(set) var limit = ChatModel.pageSize
    /// A problem to show above the composer, in plain words. Nil when all is well.
    public private(set) var problem: String?

    @ObservationIgnored let service: any ChatService
    @ObservationIgnored private let remember: (String) -> Void

    /// - Parameters:
    ///   - conversationId: the main conversation remembered from last time, so the cache shows at once offline.
    ///   - remember: keeps the conversation id for next launch.
    public init(service: any ChatService, conversationId: String? = nil, remember: @escaping (String) -> Void = { _ in }) {
        self.service = service
        self.conversationId = conversationId
        self.remember = remember
    }

    /// Asks the hub which conversation is the main one. Offline, the remembered one stays.
    public func start() async {
        do {
            let id = try await service.mainConversationId()
            if id != conversationId {
                conversationId = id
                remember(id)
            }
            problem = nil
        } catch {
            if conversationId == nil { problem = Self.say(error) }
        }
    }

    /// Shows `pageSize` more of the older messages, when the window is full (there may be more).
    public func loadEarlier(shown: Int) {
        guard shown >= limit else { return }
        limit += Self.pageSize
    }

    static func say(_ error: any Error) -> String {
        (error as? ChatError)?.message ?? ChatError.unreachable.message
    }
}
