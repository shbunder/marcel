import Foundation
import MarcelKit
import Observation

/// A message typed into the quest that the transcript does not show yet. It leaves when its `note` event arrives.
public struct PendingNote: Identifiable, Equatable, Sendable {
    public var id: String  // the client_id sent with it
    public var text: String
    public var sent: Bool
}

/// Everything the quest view shows besides the cache: the task, its transcript (history merged with the live
/// stream), what is known about its approvals, and what you are typing or sending. The rows and steps are rebuilt only
/// when the transcript, the task or an approval changes, never while you type.
@MainActor
@Observable
public final class QuestViewModel {
    public let taskId: String
    public private(set) var task: HubTask?
    public private(set) var transcript = QuestTranscript()
    public private(set) var rows: [DetailRow] = []
    public private(set) var steps: [QuestStep] = []
    public private(set) var approvals: [String: Approval] = [:]
    /// Approvals with an answer on its way: true to approve, false to deny.
    public private(set) var answering: [String: Bool] = [:]
    public private(set) var pending: [PendingNote] = []
    /// True until the history has been read once (or failed to).
    public private(set) var loading = true
    /// Stop, hand back or move on its way.
    public private(set) var busy = false
    /// The last thing that went wrong, in plain words; cleared by the next action.
    public var problem: String?
    /// The rows you opened (tools, diffs). The detail itself starts open; its tool rows start folded.
    public var expanded: Set<String> = []
    public var detailOpen = true
    public var draft = ""

    @ObservationIgnored public var service: (any QuestViewService)?
    @ObservationIgnored public var cache: MarcelCache?
    @ObservationIgnored private var filling = false
    /// The `seq` of every note of yours already matched against `pending` (or there before you sent anything).
    @ObservationIgnored private var matchedNotes: Set<Int> = []

    public init(taskId: String, task: HubTask? = nil, service: (any QuestViewService)? = nil,
                cache: MarcelCache? = nil) {
        self.taskId = taskId
        self.task = task
        self.service = service
        self.cache = cache
        rebuild()
    }

    // MARK: Loading and the live stream

    /// Reads the task and its whole history, page by page. Live events that arrive meanwhile are merged as they come;
    /// the `seq` keeps each one once.
    public func load() async {
        guard let service else {
            problem = QuestViewError.notPaired.errorDescription
            loading = false
            return
        }
        defer { loading = false }
        do {
            setTask(try await service.task(id: taskId))
            try await fetch(after: transcript.contiguousThrough == 0 ? nil : transcript.contiguousThrough,
                            fromStart: transcript.contiguousThrough == 0)
        } catch {
            problem = QuestViewError.words(for: error)
            return
        }
        // A live event that arrived while the pages were read may have skipped some; fetch them now.
        await fillGap()
    }

    /// The stream re-synced: frames sent during the outage are never replayed, so read the task (its state and open
    /// approvals) and every event after the last one this view has.
    public func reload() async {
        guard let service else { return }
        do {
            setTask(try await service.task(id: taskId))
            try await fetch(after: transcript.contiguousThrough == 0 ? nil : transcript.contiguousThrough,
                            fromStart: transcript.contiguousThrough == 0)
        } catch {
            problem = QuestViewError.words(for: error)
        }
        await fillGap()
    }

    /// One frame from the live stream. Only this quest's events, task, approvals and moves matter here.
    public func receive(_ frame: EventFrame) {
        switch frame.event {
        case .resyncRequired:
            Task { await reload() }
        case .taskEvent(let event) where event.taskId == taskId:
            if transcript.merge([event]) > 0 { transcriptChanged() }
            // A live event past a hole: fetch what was skipped instead of showing a gap.
            if transcript.missingAfter != nil, !loading { Task { await fillGap() } }
        case .taskUpdated(let task) where task.id == taskId, .taskCreated(let task) where task.id == taskId:
            setTask(task)
        case .approvalCreated(let approval) where approval.taskId == taskId,
             .approvalResolved(let approval) where approval.taskId == taskId:
            approvals[approval.id] = approval
            rebuild()
        case .taskMoved(let id, _, let to) where id == taskId:
            if var task {
                task.projectId = to
                setTask(task)
            }
        default:
            break
        }
    }

    /// Fetches the events after the last contiguous one, so a live event that skipped some never leaves a hole.
    public func fillGap() async {
        guard service != nil, !filling, let after = transcript.missingAfter else { return }
        filling = true
        defer { filling = false }
        do {
            try await fetch(after: after, fromStart: false)
        } catch {
            problem = QuestViewError.words(for: error)
        }
    }

    private func fetch(after: Int?, fromStart: Bool) async throws {
        guard let service else { return }
        var after = after
        var first = fromStart
        // 200 a page; 500 pages is far more than any quest, and keeps a hub that never says "no more" from looping.
        for _ in 0..<500 {
            let page = try await service.events(taskId: taskId, after: after)
            if first, let seq = page.events.first?.seq { transcript.startsAt(seq) }
            first = false
            if transcript.merge(page.events) > 0 { transcriptChanged() }
            guard page.hasMore, let last = page.events.last?.seq else { break }
            after = last
        }
    }

    // MARK: Talking to the quest

    /// Sends the draft to this quest only (`POST /tasks/{id}/messages`). It shows as "Sending…" under the transcript
    /// until the hub records it as a note.
    public func send() async {
        let text = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }
        problem = nil
        guard let service else {
            problem = QuestViewError.notPaired.errorDescription
            return
        }
        let note = PendingNote(id: UUID().uuidString, text: text, sent: false)
        pending.append(note)
        draft = ""
        do {
            setTask(try await service.steer(taskId: taskId, text: text, clientId: note.id))
            if let index = pending.firstIndex(where: { $0.id == note.id }) { pending[index].sent = true }
        } catch {
            pending.removeAll { $0.id == note.id }
            if draft.isEmpty { draft = text }  // keep what you typed, to try again
            problem = QuestViewError.words(for: error)
        }
    }

    /// Approve or deny an inline prompt. If a terminal answered first, the prompt shows that answer and says so.
    public func answer(_ approvalId: String, approve: Bool) async {
        problem = nil
        guard let service else {
            problem = QuestViewError.notPaired.errorDescription
            return
        }
        guard answering[approvalId] == nil, statusOf(approvalId).isOpen else { return }
        answering[approvalId] = approve
        rebuild()
        do {
            approvals[approvalId] = try await service.answer(approvalId: approvalId, approve: approve)
        } catch QuestViewError.alreadyAnswered(let approval) {
            approvals[approvalId] = approval
            problem = QuestViewError.alreadyAnswered(approval).errorDescription
        } catch {
            problem = QuestViewError.words(for: error)
        }
        answering[approvalId] = nil
        rebuild()
    }

    public func statusOf(_ approvalId: String) -> ApprovalStatus {
        QuestDetail.status(of: approvalId, approvals: approvals, answering: answering, openIds: openIds,
                           finished: isFinished)
    }

    // MARK: Toolbar

    public var canStop: Bool { task.map { !Self.finishedStates.contains($0.state) } ?? false }

    public func stop() async {
        await act { try await $0.stop(taskId: self.taskId) }
    }

    public func handBack(note: String?) async {
        let note = note?.trimmingCharacters(in: .whitespacesAndNewlines)
        await act { try await $0.handBack(taskId: self.taskId, note: note?.isEmpty == false ? note : nil) }
    }

    public func move(to projectId: String) async {
        guard projectId != task?.projectId else { return }
        await act { try await $0.move(taskId: self.taskId, to: projectId) }
    }

    /// The quest's claude.ai/code page (app-api.yaml `Task`): `cloud_url` for a cloud quest, `remote_url` for a NUC
    /// session with Remote Control, never the other way round. Only `https://claude.ai/code…` is offered; anything
    /// else is not a button.
    public var claudeURL: URL? { task.flatMap(Self.claudeURL(for:)) }

    public static func claudeURL(for task: HubTask) -> URL? {
        guard let raw = task.location == .cloud ? task.cloudUrl : task.remoteUrl, let url = URL(string: raw),
              url.scheme?.lowercased() == "https", url.host()?.lowercased() == "claude.ai", url.port == nil,
              url.user == nil, url.password == nil, url.path == "/code" || url.path.hasPrefix("/code/")
        else { return nil }
        return url
    }

    private func act(_ action: @MainActor (any QuestViewService) async throws -> HubTask) async {
        problem = nil
        guard let service else {
            problem = QuestViewError.notPaired.errorDescription
            return
        }
        guard !busy else { return }
        busy = true
        defer { busy = false }
        do {
            setTask(try await action(service))
        } catch {
            problem = QuestViewError.words(for: error)
        }
    }

    // MARK: Building what the screen shows

    private static let finishedStates: Set<Components.Schemas.TaskState> = [.done, .failed, .stopped]

    private var isFinished: Bool { task.map { Self.finishedStates.contains($0.state) } ?? false }

    private var openIds: Set<String>? { task.map { Set($0.approvalIds ?? []) } }

    private func setTask(_ task: HubTask) {
        self.task = task
        try? cache?.upsert(task)
        try? cache?.save()
        rebuild()
    }

    private func transcriptChanged() {
        // A note you sent is in the transcript now: it no longer needs its own "Sending…" row. A recorded note has no
        // client_id (NoteEventData), so each new note of yours replaces one pending message with the same text, the
        // oldest: two messages with the same words stay two until both are recorded.
        for event in transcript.events where !matchedNotes.contains(event.seq) {
            guard case .note("user", let text, _, _) = event.entry else { continue }
            matchedNotes.insert(event.seq)
            if let index = pending.firstIndex(where: { $0.text == text }) { pending.remove(at: index) }
        }
        rebuild()
    }

    private func rebuild() {
        rows = QuestDetail.rows(transcript.events, approvals: approvals, answering: answering, openIds: openIds,
                                finished: isFinished)
        steps = QuestStep.steps(transcript.events, state: task?.state)
    }
}
