import MarcelKit
import SwiftUI

/// An item attached to a message ("Ask about this", B-21): the hub stores it as the message's `artifact_id`. The Chat
/// tab shows it above the composer and in the sent bubble.
public struct ItemAttachment: Hashable, Sendable {
    public var artifactId: String
    public var title: String
    /// The item's project: the message is tagged with it (`project_id`), as a room's messages are.
    public var projectId: String

    public init(artifactId: String, title: String, projectId: String) {
        self.artifactId = artifactId
        self.title = title
        self.projectId = projectId
    }

    public init(_ artifact: Artifact) {
        self.init(artifactId: artifact.id, title: artifact.title, projectId: artifact.projectId)
    }
}

extension EnvironmentValues {
    /// "Ask about this" in the viewer: opens Chat with the item attached. The app sets it (and the Chat tab for the
    /// viewers it opens itself). Without it, the viewer asks in its own question box.
    @Entry public var askInChat: ((ItemAttachment) -> Void)?
}
