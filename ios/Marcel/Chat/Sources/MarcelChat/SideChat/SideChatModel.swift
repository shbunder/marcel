import Foundation
import MarcelKit
import Observation

/// The message a side chat was opened on, as the sheet shows it at the top ("On: Marcel — 18° and dry…").
public struct SideChatParent: Identifiable, Hashable, Sendable {
    /// The message's id.
    public var id: String
    /// Who said it: "You", the agent's name, a quest's title.
    public var speaker: String
    public var text: String
    /// The thread it already has, as the hub said on the message (`thread.conversation_id`).
    public var threadId: String?
    /// A milestone or approval about a task: its thread is the task's own (replies steer the task).
    public var taskId: String?

    public init(id: String, speaker: String, text: String, threadId: String? = nil, taskId: String? = nil) {
        self.id = id
        self.speaker = speaker
        self.text = text
        self.threadId = threadId
        self.taskId = taskId
    }

    /// The parent for a row of the main feed; nil for a row that is not a stored message (one still on its way).
    public static func of(_ item: FeedItem, message: Message?, agentName: String) -> SideChatParent? {
        guard let message, message.id == item.id else { return nil }
        let speaker: String
        let text: String
        switch item {
        case .user(let line): (speaker, text) = ("You", line.text)
        case .marcel(let line): (speaker, text) = (agentName, line.body.text)
        case .quest(let line): (speaker, text) = (line.title, line.detail ?? "")
        case .approval(let line): (speaker, text) = ("Question", line.question)
        case .item(let line): (speaker, text) = ("Item", line.title)
        }
        let aboutTask = message.kind == .milestone || message.kind == .approval
        return SideChatParent(id: message.id, speaker: speaker, text: text,
                              threadId: message.thread?.conversationId, taskId: aboutTask ? message.taskId : nil)
    }
}

/// "n replies" under a message of the main feed.
public struct ThreadSummary: Equatable, Sendable {
    public var conversationId: String
    public var replies: Int

    /// "1 reply", "4 replies".
    public var label: String { replies == 1 ? "1 reply" : "\(replies) replies" }
    /// What VoiceOver reads for the button.
    public var accessibilityLabel: String { "Side chat, \(label)" }
}

/// What the app holds about side chats across sheets: the thread each message has (opened here, or announced by
/// `conversation.created`), and what you were typing in each one. Drafts are per thread: closing a sheet keeps
/// yours for when you reopen it.
@MainActor
@Observable
public final class SideChats {
    /// Thread id by the message it was opened on.
    public private(set) var threads: [String: String] = [:]
    /// Unsent text by the message the thread is on.
    public var drafts: [String: String] = [:]

    public init() {}

    /// Remembers a thread (opened here, or `conversation.created` from the live stream).
    public func learn(_ conversation: Conversation) {
        guard conversation.kind == .side, let parent = conversation.parentMessageId else { return }
        if threads[parent] != conversation.id { threads[parent] = conversation.id }
    }

    /// Follows the live stream: a thread opened on another device gets its "n replies" here too.
    public func receive(_ frame: EventFrame) {
        if case .conversationCreated(let conversation) = frame.event { learn(conversation) }
    }

    /// "n replies" for each message in the window that has a side thread. `counts` is how many messages the cache
    /// holds per side thread (the live stream adds each reply), so the count follows new replies without the hub
    /// re-sending the parent; the hub's own `reply_count` covers what the phone has not seen.
    public func summaries(for messages: [Message], counts: [String: Int]) -> [String: ThreadSummary] {
        var result: [String: ThreadSummary] = [:]
        for message in messages {
            guard let thread = message.thread?.conversationId ?? threads[message.id] else { continue }
            let replies = max(message.thread?.replyCount ?? 0, counts[thread] ?? 0)
            guard replies > 0 else { continue }
            result[message.id] = ThreadSummary(conversationId: thread, replies: replies)
        }
        return result
    }
}

/// What "Send result to main chat" will send, shown for confirmation first.
public struct SendToMainProposal: Equatable, Sendable {
    public var messageId: String
    /// The text the main chat will show, as a quote.
    public var preview: String
}

/// One side chat (wireframe 2B): the thread on a message, your replies on their way, and bringing a result back to the
/// main chat. The thread's messages come from the cache (`FeedInput` for the thread's conversation).
@MainActor
@Observable
public final class SideChatModel: Identifiable {
    public let parent: SideChatParent
    public nonisolated var id: String { parent.id }

    /// The thread, once the hub has answered.
    public private(set) var conversation: Conversation?
    /// The thread's id: known at once when the message (or this phone) already knew it, so the cache shows offline.
    public private(set) var conversationId: String?
    public private(set) var opening = false
    /// A problem in plain words, under the thread. Nil when all is well.
    public private(set) var problem: String?
    /// The hub said the task behind this thread has stopped or failed (409 `task_finished`).
    public private(set) var taskFinished = false
    /// The message or thread is gone from the hub (`*_not_found`).
    public private(set) var gone = false
    /// Your replies the cache does not hold yet, oldest first.
    public private(set) var pending: [PendingMessage] = []
    /// "Send result to main chat" waits for your OK on this.
    public private(set) var proposal: SendToMainProposal?
    public private(set) var sendingToMain = false
    /// The message last sent to the main chat, to say so ("Sent to the main chat.").
    public private(set) var sentToMain: String?

    @ObservationIgnored let service: any SideChatService
    @ObservationIgnored let chats: SideChats
    /// Where the thread's messages are put when fetched; the live stream adds the rest.
    @ObservationIgnored public var cache: MarcelCache?

    public init(parent: SideChatParent, service: any SideChatService, chats: SideChats, cache: MarcelCache? = nil) {
        self.parent = parent
        self.service = service
        self.chats = chats
        self.cache = cache
        conversationId = parent.threadId ?? chats.threads[parent.id]
    }

    /// The task this thread belongs to (replies steer it), from the hub's answer or, before it, from the message.
    public var taskId: String? { conversation?.taskId ?? parent.taskId }

    /// What you are typing, kept per thread.
    public var draft: String {
        get { chats.drafts[parent.id] ?? "" }
        set { chats.drafts[parent.id] = newValue.isEmpty ? nil : newValue }
    }

    /// Opens (or reopens) the thread and fetches its messages into the cache.
    public func open() async {
        guard !opening else { return }
        opening = true
        defer { opening = false }
        do {
            let thread = try await service.openThread(messageId: parent.id)
            conversation = thread
            conversationId = thread.id
            chats.learn(thread)
            let messages = try await service.threadMessages(conversationId: thread.id)
            if let cache {
                for message in messages { try? cache.upsert(message) }
                try? cache.save()
            }
            problem = nil
        } catch {
            say(error)
        }
    }

    // MARK: Replying

    /// Posts what you typed. It shows at once ("Sending…"); if it does not get through it stays, "Not sent".
    public func send() async {
        let text = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }
        draft = ""
        let clientId = UUID().uuidString.lowercased()
        pending.append(PendingMessage(clientId: clientId, text: text, failed: false))
        await deliver(clientId)
    }

    /// Sends a reply that did not get through again, with the same client id (the hub keeps one copy).
    public func retry(clientId: String) async {
        guard let index = pending.firstIndex(where: { $0.clientId == clientId }), pending[index].failed else { return }
        pending[index].failed = false
        await deliver(clientId)
    }

    /// Forgets replies the live stream has delivered (they show from the cache now).
    public func prune(_ messages: [Message]) {
        let arrived = Set(messages.compactMap(\.clientId))
        if pending.contains(where: { arrived.contains($0.clientId) }) {
            pending.removeAll { arrived.contains($0.clientId) }
        }
    }

    /// "Start a new one?" after a stopped task: the words you could not send (your failed replies, then your draft),
    /// for the main chat's composer, where the agent can start a new task. The failed replies leave the thread.
    public func startOver() -> String {
        let words = pending.filter(\.failed).map(\.text) + [draft].filter { !$0.isEmpty }
        pending.removeAll(where: \.failed)
        draft = ""
        problem = nil
        return words.joined(separator: "\n")
    }

    private func deliver(_ clientId: String) async {
        guard let reply = pending.first(where: { $0.clientId == clientId }) else { return }
        if conversationId == nil || conversation == nil { await open() }
        guard let conversationId, !gone else {
            mark(clientId, failed: true)
            return
        }
        do {
            _ = try await service.reply(text: reply.text, clientId: clientId, conversationId: conversationId)
            if let index = pending.firstIndex(where: { $0.clientId == clientId }) { pending[index].stored = true }
            problem = nil
        } catch {
            mark(clientId, failed: true)
            say(error)
        }
    }

    private func mark(_ clientId: String, failed: Bool) {
        if let index = pending.firstIndex(where: { $0.clientId == clientId }) { pending[index].failed = failed }
    }

    // MARK: Send to main

    /// The thread's result: its newest message from the agent or a task (not one of yours).
    public static func result(in messages: [Message]) -> SendToMainProposal? {
        for message in messages.reversed() where message.role != .user {
            let text = message.text ?? message.milestone.map { $0.summary ?? $0.taskTitle }
            if let text, !text.isEmpty { return SendToMainProposal(messageId: message.id, preview: text) }
        }
        return nil
    }

    /// "Send result to main chat": asks first, showing what will be sent.
    public func proposeSendToMain(_ messages: [Message]) {
        guard let result = Self.result(in: messages) else {
            problem = "Nothing to send yet: no answer in this side chat."
            return
        }
        proposal = result
    }

    public func cancelSendToMain() {
        proposal = nil
    }

    /// Sends what was confirmed. It shows in the main chat as a quote, through the live stream.
    public func confirmSendToMain() async {
        guard let proposal, let conversationId, !sendingToMain else { return }
        sendingToMain = true
        defer { sendingToMain = false }
        do {
            _ = try await service.sendToMain(conversationId: conversationId, messageId: proposal.messageId)
            self.proposal = nil
            sentToMain = proposal.messageId
            problem = nil
        } catch {
            say(error)
        }
    }

    private func say(_ error: any Error) {
        let failure = error as? SideChatError ?? .offline
        switch failure.kind {
        case .taskFinished: taskFinished = true
        case .gone: gone = true
        default: break
        }
        problem = failure.message
    }
}
