import Foundation
import MarcelKit
import PixelKit

/// One row of the chat, ready to draw. Built from the cache by `ChatFeed.items`, so what the feed shows is a pure
/// function of what the hub sent: a task that changes changes its card, and never adds a row.
public enum FeedItem: Identifiable, Equatable, Sendable {
    /// Something you wrote.
    case user(UserLine)
    /// Marcel speaking: a dialogue box with a Markdown body.
    case marcel(MarcelLine)

    public var id: String {
        switch self {
        case .user(let line): line.id
        case .marcel(let line): line.id
        }
    }
}

public struct UserLine: Equatable, Sendable {
    public var id: String
    public var text: String
}

public struct MarcelLine: Equatable, Sendable {
    public var id: String
    /// The Markdown body as the pixel font shows it (see `ChatMarkdown`).
    public var body: ChatMarkdown.Rendered
}

public enum ChatFeed {
    /// The rows for a window of the conversation, oldest first.
    public static func items(_ input: FeedInput) -> [FeedItem] {
        var items: [FeedItem] = []
        for message in input.messages {
            if let item = item(for: message, input: input) { items.append(item) }
        }
        return items
    }

    static func item(for message: Message, input: FeedInput) -> FeedItem? {
        switch message.kind {
        case .text, .quote:
            let text = message.text ?? ""
            if message.role == .user {
                return .user(UserLine(id: message.id, text: text))
            }
            return .marcel(MarcelLine(id: message.id, body: ChatMarkdown.render(text)))
        default:
            return nil
        }
    }
}
