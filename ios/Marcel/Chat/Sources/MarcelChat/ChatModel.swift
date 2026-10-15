import AvatarKit
import Foundation
import MarcelItems
import MarcelKit
import MarcelLook
import Observation
import PixelKit

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
    /// The quests, items and projects picked with `@` and `#` for the next message (B-42).
    public private(set) var mentions: [Mention] = []
    /// The `@word` or `#word` being typed, and what the hub found for it.
    public private(set) var mentionTrigger: MentionTrigger?
    public private(set) var mentionSuggestions: [Mention] = []
    /// The hub could not be asked what matches: the suggestions say so instead of "no match".
    public private(set) var mentionsUnreachable = false
    /// An item going with your next message ("Ask about this" on an item); shown above the composer until sent.
    public var attachment: ItemAttachment?
    /// The agent's name for the header, as the hub last said it (`GET /me`, or Settings after a rename).
    public var agentName: String?
    /// What the header's giraffe shows, as the hub last said it (`GET /me`, then every `agent.state` event). Nil
    /// until the hub has said; the header then works it out from the cached tasks.
    public private(set) var agentState: AvatarState?
    /// What the header shows the agent doing (`MarcelAction`): `agentState` refined by the last tool call and a
    /// recent problem, each pose held at least `PoseGate.hold`. Nil until the hub has said.
    public private(set) var action: MarcelAction?
    /// The mood loop the header plays over `action`'s pose (`MoodBook`): a one-pass flourish (a quest done after a
    /// long run, your message arriving) or the mood picked for a held state (needs you, done, a problem). Nil plays
    /// the pose's own loop.
    public private(set) var mood: String?
    /// The helper's personality, from its look; it weights the mood picks.
    public private(set) var personality: LookPersonality?
    /// Your bubbles' colour: the agent's look colour (`GET /me`); nil for Marcel's rose.
    public private(set) var bubbleColor: PixelColor?
    /// The agent this chat is with; `agent.state` events for another agent are not for this header.
    public private(set) var agentId: String?
    /// This phone (`Me.device_id`): its buddy is the one `PATCH /devices/{id}` switches.
    public private(set) var deviceId: String?
    /// The buddy sheet's rows (B-49), as last loaded.
    public private(set) var buddies: [BuddyChoice] = []
    /// The buddy this phone shows, shared with every tab (`CurrentBuddy`).
    @ObservationIgnored public let buddy: CurrentBuddy
    /// A message to bring into view (a `marcel://message/<id>` link). The feed scrolls to it, loading older pages
    /// from the cache if needed, then clears it.
    public var focus: String?
    /// Your message a "re:" line pointed at, outlined for a moment after the jump (B-41).
    public private(set) var highlighted: String?
    /// How long that outline stays.
    static let highlightFor: Duration = .seconds(2.5)
    /// The side chat open over the feed (B-08), if any.
    public var sideChat: SideChatModel?
    /// Threads and drafts of every side chat, kept while the app runs.
    public let sideChats = SideChats()

    @ObservationIgnored private var refreshing = false
    @ObservationIgnored private var lastTool: MarcelAction.ToolKind?
    @ObservationIgnored private var failedAt: TimeInterval?
    @ObservationIgnored private var poses = PoseGate()
    @ObservationIgnored private var poseTimer: Task<Void, Never>?
    /// Seconds on a steady clock; tests move it by hand.
    @ObservationIgnored var clock: () -> TimeInterval = { Date.timeIntervalSinceReferenceDate }
    /// Where the mood picks come from; tests seed it.
    @ObservationIgnored var random = SeededRandom(state: UInt64(Date().timeIntervalSince1970 * 1000))
    @ObservationIgnored private var held: (action: MarcelAction, loop: String?)?
    @ObservationIgnored private var flourish: (loop: String, until: TimeInterval)?
    @ObservationIgnored private var taskStates: [String: Components.Schemas.TaskState] = [:]
    @ObservationIgnored private var starts = 0
    /// Every side chat opened while the app runs, by the message it is on.
    @ObservationIgnored private var sideChatModels: [String: SideChatModel] = [:]
    @ObservationIgnored private var lastAsked: Set<String> = []
    @ObservationIgnored private var previewsAsked: Set<String> = []

    @ObservationIgnored let service: any ChatService
    /// Each agent's own avatar sheet (B-44): the header and speaker lines play it.
    @ObservationIgnored public let avatars: AvatarSheets
    @ObservationIgnored private let remember: (String) -> Void
    @ObservationIgnored private let rememberAgent: (String) -> Void

    /// - Parameters:
    ///   - conversationId: the main conversation remembered from last time, so the cache shows at once offline.
    ///   - remember: keeps the conversation id for next launch.
    ///   - avatarDirectory: where avatar sheets are kept between launches (`ChatModel.avatarDirectory`); nil keeps
    ///     them in memory only.
    ///   - agentId, rememberAgent: the agent remembered from last time, so an offline start plays its kept avatar.
    public init(service: any ChatService, conversationId: String? = nil,
                remember: @escaping (String) -> Void = { _ in }, avatarDirectory: URL? = nil, agentId: String? = nil,
                rememberAgent: @escaping (String) -> Void = { _ in }, buddy: CurrentBuddy = .shared) {
        self.buddy = buddy
        self.service = service
        self.conversationId = conversationId
        self.remember = remember
        self.agentId = agentId
        self.rememberAgent = rememberAgent
        let owner = WeakModel()
        avatars = AvatarSheets(directory: avatarDirectory) { id, etag in try await owner.model?.avatar(id, etag) }
        owner.model = self
    }

    /// Lets the avatar store ask the hub through this model's service without keeping the model alive.
    private final class WeakModel: @unchecked Sendable {
        weak var model: ChatModel?
    }

    private func avatar(_ agentId: String, _ etag: String?) async throws -> AvatarSheets.Download? {
        try await service.avatar(agentId: agentId, ifNoneMatch: etag)
    }

    /// Where the app keeps avatar sheets: `Library/Caches/avatars`.
    public static var avatarDirectory: URL { AvatarSheets.defaultDirectory() }

    /// Asks the hub which conversation is the main one. Offline, the remembered one stays.
    public func start() async {
        // Only the newest start writes: a slow first start must not undo a switch made meanwhile (a push on a cold
        // start, two quick picks).
        starts += 1
        let mine = starts
        do {
            let agent = try await service.mainAgent()
            guard mine == starts else { return }
            agentName = agent.name
            agentId = agent.id ?? agentId
            deviceId = agent.deviceId ?? deviceId
            if let id = agent.id { buddy.show(id) }
            bubbleColor = agent.look.map(LookColors.bubble)
            personality = agent.look?.personality ?? personality
            if let state = agent.state {
                agentState = state
                updateAction()
            }
            if let id = agent.id {
                rememberAgent(id)
                Task { await avatars.refresh(agentId: id) }
            }
            let id = agent.conversationId
            if id != conversationId {
                conversationId = id
                remember(id)
            }
            problem = nil
        } catch {
            guard mine == starts else { return }
            if conversationId == nil { problem = Self.say(error) }
        }
    }

    /// A frame from the live stream (the app's `liveEvents` cache calls this for every frame). `agent.state` moves
    /// the header's giraffe; after a re-sync the missed ones are gone, so the hub is asked again.
    public func receive(_ frame: EventFrame) {
        switch frame.event {
        case .deviceUpdated(let device, let shown):
            // Another phone switched this one, or the hub moved it off an archived buddy.
            guard device == deviceId, shown != agentId else { return }
            buddy.show(shown)
            Task { await reloadForBuddy(shown) }
        case .agentState(let id, let state):
            guard agentId == nil || agentId == id else { return }
            let before = agentState
            agentState = AvatarState(state)
            if case .working = agentState {} else { lastTool = nil }
            if case .working(let n) = agentState, n >= 3 {
                if case .working(let m) = before, m >= 3 {} else { play(.manyQuests) }
            }
            updateAction()
        case .taskUpdated(let task):
            taskChanged(task)
        case .approvalResolved(let approval):
            if approval.state == .denied { play(.denied) }
        case .taskEvent(let event):
            switch event._type {
            case .toolCall:
                lastTool = ((event.data.value["tool"] ?? nil) as? String).flatMap(MarcelAction.ToolKind.init(tool:))
                updateAction()
            case .error:
                failedAt = clock()
                updateAction()
            default:
                break
            }
        case .agentUpdated(let agent, let etag):
            // Your helper's new look (the look picker, S-19.4): your bubbles take its colour at once.
            if agent.id == agentId {
                bubbleColor = agent.drawnLook.map(LookColors.bubble)
                personality = agent.drawnLook?.personality ?? personality
            }
            // A new look: fetch that agent's sheet again (any helper's: speaker lines play theirs too).
            Task { await avatars.refresh(agentId: agent.id, etag: etag) }
        case .resyncRequired:
            Task { await start() }
        default:
            break
        }
    }

    // MARK: Buddies (B-49)

    /// The name of the buddy shown before, for a long-press on the avatar ("Back to Pip"); nil when there is none.
    public var previousBuddyName: String? {
        guard let previous = buddy.previousId, previous != agentId else { return nil }
        return buddies.first { $0.id == previous }?.name
    }

    /// Fills the buddy sheet.
    public func loadBuddies() async {
        // Offline the sheet keeps the buddies it last had; the chat has its own word for being offline.
        if let loaded = try? await service.buddies() { buddies = loaded }
    }

    /// Shows `id` on this phone: tells the hub (`PATCH /devices/{id}`), then reloads the chat for that buddy. Every
    /// other tab follows `CurrentBuddy`.
    public func switchTo(_ id: String) async {
        // A push tapped on a cold start can come before the chat has asked the hub who this phone is.
        if deviceId == nil { await start() }
        guard id != agentId else { return }
        guard let deviceId else {
            problem = "Can't switch while the hub is out of reach."
            return
        }
        do {
            try await service.show(agentId: id, deviceId: deviceId)
        } catch {
            problem = Self.say(error)
            return
        }
        buddy.show(id)
        await reloadForBuddy(id)
    }

    /// Straight back to the buddy shown before (a long-press on the avatar).
    public func switchBack() async {
        guard let previous = buddy.previousId else { return }
        await switchTo(previous)
    }

    /// Adds a buddy and shows it.
    public func addBuddy(named name: String) async {
        let name = name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !name.isEmpty else { return }
        do {
            let added = try await service.addBuddy(name: name)
            buddies.append(added)
            await switchTo(added.id)
        } catch {
            problem = Self.say(error)
        }
    }

    /// Forgets what belonged to the buddy shown before, then asks the hub for the new one. Its chat is cleared first,
    /// so if the hub cannot be asked the screen says so instead of showing the old buddy's chat under the new name.
    private func reloadForBuddy(_ id: String) async {
        agentId = id
        agentName = buddies.first { $0.id == id }?.name
        conversationId = nil
        bubbleColor = nil
        agentState = nil
        action = nil
        mood = nil
        held = nil
        flourish = nil
        poses = PoseGate()
        lastTool = nil
        failedAt = nil
        limit = Self.pageSize
        sideChat = nil
        focus = nil
        await start()
    }

    /// Works out what the header should show now and asks the gate for it; wakes again when a held pose or a
    /// problem's few seconds run out.
    func updateAction() {
        guard let agentState else { return }
        let now = clock()
        let problemLeft = failedAt.map { $0 + MarcelAction.errorFor - now }.flatMap { $0 > 0 ? $0 : nil }
        let wait = poses.want(MarcelAction(agent: agentState, tool: lastTool, failed: problemLeft != nil), at: now)
        action = poses.shown
        if let shown = action, held?.action != shown {
            // Picked once per pose; the header plays it only if the sheet it draws has that loop.
            held = (shown, MoodBook.holding(shown).map { pickLoop($0) })
        }
        if let current = flourish, current.until <= now { flourish = nil }
        mood = flourish?.loop ?? held?.loop
        poseTimer?.cancel()
        guard let next = [wait, problemLeft, flourish.map { $0.until - now }].compactMap({ $0 }).min() else { return }
        poseTimer = Task { [weak self] in
            try? await Task.sleep(for: .seconds(next))
            guard !Task.isCancelled else { return }
            self?.updateAction()
        }
    }

    /// The loop of a mood picked for `situation`.
    private func pickLoop(_ situation: MoodBook.Situation) -> String {
        MoodBook.loop(MoodBook.pick(situation, personality: personality, random: &random))
    }

    /// Plays one pass of a mood for `situation` over the current pose (the pose comes back after it).
    func play(_ situation: MoodBook.Situation) {
        let loop = pickLoop(situation)
        // A sheet without the loop (one made before moods) has nothing to play for it.
        guard let pass = avatars.sheet(for: agentId).loops[loop] else { return }
        let seconds = TimeInterval(pass.duration) / 1000
        flourish = (loop, clock() + max(seconds, PoseGate.hold))
        updateAction()
    }

    /// The app came to the front: a greeting, if the agent is free.
    public func greet() {
        guard action == nil || action == .idle else { return }
        play(.greeting)
    }

    /// A quest changed: its end shows on the avatar (relief after a long one, sadness when stopped, a problem when
    /// it failed). Only a change this model saw happen: the first update of a quest (a replay or catch-up after a
    /// launch, a quest that ended long ago) is just noted.
    private func taskChanged(_ task: HubTask) {
        let before = taskStates[task.id]
        taskStates[task.id] = task.state
        guard let before, before != task.state else { return }
        let long = task.updatedAt.timeIntervalSince(task.createdAt) >= MoodBook.long
        switch task.state {
        case .done where long: play(.longDone)
        case .stopped: play(long ? .longStopped : .stopped)
        case .failed:
            failedAt = clock()
            updateAction()
        default: break
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
        play(MoodBook.isThanks(text) ? .thanks : .listening)
        let clientId = UUID().uuidString.lowercased()
        local.pending.append(PendingMessage(clientId: clientId, text: text, failed: false, attachment: attachment,
                                            mentions: mentions))
        attachment = nil
        mentions = []
        mentionTrigger = nil
        mentionSuggestions = []
        await deliver(clientId)
    }

    // MARK: Mentions (B-42)

    /// The draft changed: if you are typing `@word` or `#word`, ask the hub what matches.
    public func draftChanged() async {
        let trigger = MentionTrigger.find(in: draft)
        mentionTrigger = trigger
        guard let trigger else {
            mentionSuggestions = []
            return
        }
        let found: [Mention]
        var unreachable = false
        do { found = try await service.searchMentions(query: trigger.query, kinds: trigger.kinds) } catch {
            found = []
            unreachable = true
        }
        // Only if you are still typing the same thing.
        guard mentionTrigger == trigger else { return }
        // A stray sign ("I paid #5 for it"): once a space follows and nothing matches, it is ordinary text.
        if found.isEmpty, !unreachable, trigger.query.contains(" ") {
            mentionTrigger = nil
            mentionSuggestions = []
            mentionsUnreachable = false
            return
        }
        mentionsUnreachable = unreachable
        mentionSuggestions = found.filter { picked in !mentions.contains(picked) }
    }

    /// The helpers whose speaker lines are on screen: fetch the sheets the phone does not have yet (B-44).
    public func loadAvatars(for agentIds: Set<String>) async {
        for id in agentIds.sorted() where avatars.sheets[id] == nil {
            await avatars.refresh(agentId: id)
        }
    }

    /// "re: your 08:12 message" was tapped: scroll to that message and outline it for a moment.
    public func jump(to messageId: String) {
        focus = messageId
        highlighted = messageId
        Task { [weak self] in
            try? await Task.sleep(for: Self.highlightFor)
            if self?.highlighted == messageId { self?.highlighted = nil }
        }
    }

    /// A suggestion was tapped: it becomes a chip on the next message and the `@word` leaves the text.
    public func pick(_ mention: Mention) {
        // Found again in the draft as it is now: it may have changed while the hub was asked.
        if mentionTrigger != nil, let trigger = MentionTrigger.find(in: draft) {
            draft.removeSubrange(trigger.range)
            while draft.last == " " { draft.removeLast() }
        }
        if !mentions.contains(mention), mentions.count < 10 { mentions.append(mention) }
        mentionTrigger = nil
        mentionSuggestions = []
        mentionsUnreachable = false
    }

    /// Takes a chip off the next message.
    public func unpick(_ mention: Mention) {
        mentions.removeAll { $0 == mention }
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
                                       attachment: message.attachment, mentions: message.mentions)
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
