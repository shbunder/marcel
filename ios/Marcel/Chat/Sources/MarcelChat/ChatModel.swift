import Foundation
import MarcelItems
import MarcelKit
import Observation

/// The chat's state beyond what the cache holds: which conversation, how much of it is on screen, and what the
/// person is doing right now. The rows themselves come from the cache (`FeedInput`).
@MainActor
@Observable
public final class ChatModel {
    /// How many messages one step up the feed loads from the cache.
    public static let pageSize = 50
    /// How far back a message link looks in the cache before giving up: 20 pages.
    public static let focusDepth = 1000

    public private(set) var conversationId: String?
    /// How many of the newest messages the feed shows; grows by `pageSize` as you scroll up.
    public private(set) var limit = ChatModel.pageSize
    /// A problem to show above the composer, in plain words. Nil when all is well.
    public private(set) var problem: String?
    /// Approvals, answers on their way, open details and item previews (see `ChatLocalState`).
    public private(set) var local = ChatLocalState()

    /// What you are typing.
    public var draft = ""
    /// An item going with your next message ("Ask about this" on an item); shown above the composer until sent.
    public var attachment: ItemAttachment?
    /// The agent's name for the header, as the hub last said it (`GET /me`, or Settings after a rename).
    public var agentName: String?
    /// A message to bring into view (a `marcel://message/<id>` link). The feed scrolls to it, loading older pages
    /// from the cache if needed, then clears it.
    public var focus: String?
    /// The side chat open over the feed (B-08), if any.
    public var sideChat: SideChatModel?
    /// Threads and drafts of every side chat, kept while the app runs.
    public let sideChats = SideChats()

    @ObservationIgnored private var refreshing = false
    /// Every side chat opened while the app runs, by the message it is on.
    @ObservationIgnored private var sideChatModels: [String: SideChatModel] = [:]
    @ObservationIgnored private var lastAsked: Set<String> = []
    @ObservationIgnored private var previewsAsked: Set<String> = []

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
            let agent = try await service.mainAgent()
            agentName = agent.name
            let id = agent.conversationId
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

    // MARK: Sending

    /// Sends what you typed. The composer is never locked: you can send while Marcel or any thread is busy
    /// (B-02). The message shows at once; if the hub cannot be reached it stays, marked "not sent".
    public func send() async {
        let text = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }
        draft = ""
        let clientId = UUID().uuidString.lowercased()
        local.pending.append(PendingMessage(clientId: clientId, text: text, failed: false, attachment: attachment))
        attachment = nil
        await deliver(clientId)
    }

    /// "Ask about this" on an item: the item goes with your next message. What you were typing stays.
    public func attach(_ item: ItemAttachment) {
        attachment = item
    }

    /// Puts a suggested message in the composer ("+ New" on the map) without losing what you were typing: an empty
    /// draft takes it, anything else keeps your words and adds it on a new line.
    public func propose(_ text: String) {
        let typed = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        if typed.isEmpty {
            draft = text
        } else if !draft.contains(text) {
            draft = typed + "\n\n" + text
        }
    }

    /// Sends a message that did not get through again, with the same client id (the hub keeps one copy). One whose
    /// attached item is gone from the hub goes without the item: with it, it would fail the same way every time.
    public func retry(clientId: String) async {
        guard let index = local.pending.firstIndex(where: { $0.clientId == clientId }), local.pending[index].failed
        else { return }
        local.pending[index].failed = false
        if local.pending[index].itemGone {
            local.pending[index].attachment = nil
            local.pending[index].itemGone = false
        }
        await deliver(clientId)
    }

    /// Forgets messages the hub's stream has delivered (they show from the cache now).
    public func prune(_ input: FeedInput) {
        let arrived = Set(input.messages.compactMap(\.clientId))
        if local.pending.contains(where: { arrived.contains($0.clientId) }) {
            local.pending.removeAll { arrived.contains($0.clientId) }
        }
    }

    private func deliver(_ clientId: String) async {
        guard let message = local.pending.first(where: { $0.clientId == clientId }) else { return }
        guard let conversationId else {
            mark(clientId, failed: true)
            problem = "Not sent: the conversation has not loaded yet. Tap the message to try again."
            return
        }
        do {
            _ = try await service.send(text: message.text, clientId: clientId, conversationId: conversationId,
                                       attachment: message.attachment)
            if let index = local.pending.firstIndex(where: { $0.clientId == clientId }) {
                local.pending[index].stored = true
            }
            problem = nil
        } catch {
            mark(clientId, failed: true)
            if (error as? ChatError)?.itemGone == true,
               let index = local.pending.firstIndex(where: { $0.clientId == clientId }) {
                local.pending[index].itemGone = true
            }
            problem = Self.say(error)
        }
    }

    private func mark(_ clientId: String, failed: Bool) {
        if let index = local.pending.firstIndex(where: { $0.clientId == clientId }) {
            local.pending[index].failed = failed
        }
    }

    // MARK: Side chats

    /// True when the hub can open side chats (the service does them): long-press then offers "Reply in thread".
    public var canReplyInThread: Bool { service is any SideChatService }

    /// "Reply in thread" on a message, or a tap on its "n replies": opens its side chat. The sheet opens the thread
    /// on the hub; your draft for it is still there.
    public func openSideChat(_ parent: SideChatParent) {
        guard let service = service as? any SideChatService else { return }
        // One model per message: a second tap (or reopening) shows the same side chat, with its replies on their way,
        // and the sheet's open runs against it.
        if let existing = sideChatModels[parent.id] {
            if sideChat !== existing { sideChat = existing }
            return
        }
        let model = SideChatModel(parent: parent, service: service, chats: sideChats)
        sideChatModels[parent.id] = model
        sideChat = model
    }

    // MARK: Approvals

    /// Keeps the approvals on screen current. The live stream fills the cache with messages and tasks, not
    /// approvals, so fetch them when an approval message shows one we do not have, or when a task no longer lists
    /// an approval we hold as open (it was answered, here or in a terminal, or it expired).
    public func reconcile(_ input: FeedInput) async {
        let shown = Set(input.messages.compactMap { $0.kind == .approval ? $0.approvalId : nil })
        let missing = shown.subtracting(local.approvals.keys)
        let settled = local.approvals.values.filter { approval in
            guard approval.state == .open, shown.contains(approval.id), let task = input.tasks[approval.taskId]
            else { return false }
            return !(task.approvalIds ?? []).contains(approval.id)
        }.map(\.id)
        let ask = Set(missing.map { "missing \($0)" } + settled.map { "settled \($0)" })
        // Ask once per reason: an approval the hub does not have is not fetched in a loop, but one that was
        // missing and is now settled is fetched again.
        guard !ask.isEmpty, ask != lastAsked, !refreshing else { return }
        lastAsked = ask
        // A failed fetch is forgotten, so the next change (or a reconnect) asks again.
        if await !refreshApprovals() { lastAsked = [] }
    }

    /// The live stream is back after a drop: what was missed may include answers, so ask again.
    public func reconnected(_ input: FeedInput) async {
        lastAsked = []
        guard input.messages.contains(where: { $0.kind == .approval }), !refreshing else { return }
        await refreshApprovals()
    }

    /// Fetches every approval. False when the hub could not be asked.
    @discardableResult
    public func refreshApprovals() async -> Bool {
        refreshing = true
        defer { refreshing = false }
        do {
            for approval in try await service.approvals() where !local.answering.contains(approval.id) {
                local.approvals[approval.id] = approval
            }
            return true
        } catch {
            problem = Self.say(error)
            return false
        }
    }

    /// Sends your answer. Only an approval that is open, and not already being answered, can be answered: a
    /// resolved card is greyed out and a second tap sends nothing.
    public func answer(approvalId: String, approve: Bool) async {
        guard local.approvals[approvalId]?.state == .open, !local.answering.contains(approvalId) else { return }
        local.answering.insert(approvalId)
        defer { local.answering.remove(approvalId) }
        do {
            local.approvals[approvalId] = try await service.answer(approvalId: approvalId, approve: approve)
            problem = nil
        } catch {
            problem = Self.say(error)
        }
    }

    /// "Details": shows or hides the full command under the question.
    public func toggleDetails(approvalId: String) {
        if local.expanded.remove(approvalId) == nil { local.expanded.insert(approvalId) }
    }

    // MARK: Item previews

    /// Fetches an item's first lines once per version. Without them the card shows what the hub says about it.
    public func loadPreview(_ artifact: Artifact) async {
        let key = ArtifactPreview.key(artifact)
        guard ArtifactPreview.fetchesContent(artifact), previewsAsked.insert(key).inserted else { return }
        guard let data = try? await service.artifactContent(id: artifact.id),
              let rows = ArtifactPreview.contentRows(artifact, data: data) else { return }
        local.previews[key] = rows
    }

    static func say(_ error: any Error) -> String {
        (error as? ChatError)?.message ?? ChatError.unreachable.message
    }
}
