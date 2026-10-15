import AvatarKit
import Foundation
import MarcelItems
import MarcelKit
import OpenAPIRuntime

/// Who the chat is with: the agent's main conversation and its name (the header's title; Settings can rename it),
/// its id (which `agent.state` events name) and what its avatar shows now.
public struct MainAgent: Equatable, Sendable {
    public var conversationId: String
    public var name: String
    public var id: String?
    public var state: AvatarState?
    /// The agent's Pixel Zoo look (app API 0.7); nil before one is chosen or when this build does not know it.
    public var look: KnownLook?
    /// This phone (`Me.device_id`), whose buddy `PATCH /devices/{id}` switches (app API 0.8.0).
    public var deviceId: String?

    public init(conversationId: String, name: String, id: String? = nil, state: AvatarState? = nil,
                look: KnownLook? = nil, deviceId: String? = nil) {
        self.conversationId = conversationId
        self.name = name
        self.id = id
        self.state = state
        self.look = look
        self.deviceId = deviceId
    }
}

/// One row of the buddy sheet (B-49): a live buddy, and the last message of its main conversation.
public struct BuddyChoice: Equatable, Identifiable, Sendable {
    public var id: String
    public var name: String
    public var look: KnownLook?
    /// The newest message of its main chat, in a line; nil when it has none (or it could not be read).
    public var lastMessage: String?

    public init(id: String, name: String, look: KnownLook? = nil, lastMessage: String? = nil) {
        self.id = id
        self.name = name
        self.look = look
        self.lastMessage = lastMessage
    }
}

/// What the chat asks the hub for over REST. Everything else it shows comes from the cache, which the live event
/// stream keeps current. Tests use a fake; the app uses `LiveChatService`.
@MainActor
public protocol ChatService {
    /// The buddy this phone shows (`GET /me`, `GET /devices` for this phone's `agent_id`, `CurrentBuddy`): its main
    /// conversation and its name.
    func mainAgent() async throws -> MainAgent
    /// Every live buddy, with the last message of its main conversation (`GET /me`, then
    /// `GET /conversations/{id}/messages?limit=1` each).
    func buddies() async throws -> [BuddyChoice]
    /// Shows `agentId` on this phone (`PATCH /devices/{device_id}`).
    func show(agentId: String, deviceId: String) async throws
    /// Adds a buddy (`POST /agents`); the hub picks its look from the name.
    func addBuddy(name: String) async throws -> BuddyChoice
    /// Stores your message (`POST /conversations/{id}/messages`). It never waits for Marcel; the same
    /// `clientId` sent again returns the same message, so a retry never doubles it. An attached item goes as
    /// `artifact_id`, tagged with the item's project (`project_id`).
    func send(text: String, clientId: String, conversationId: String, attachment: ItemAttachment?,
              mentions: [Mention]) async throws -> Message
    /// Quests and items (`@`) or projects (`#`) whose title holds `query`, newest first (`GET /mentions`, B-42).
    func searchMentions(query: String, kinds: [MentionKind]) async throws -> [Mention]
    /// Every approval, open ones first (`GET /approvals`).
    func approvals() async throws -> [Approval]
    /// Approve or deny. An approval already answered elsewhere comes back as it stands.
    func answer(approvalId: String, approve: Bool) async throws -> Approval
    /// An artifact's current bytes, for its preview.
    func artifactContent(id: String) async throws -> Data
    /// An agent's avatar sheet, its JSON and its PNG (`GET /agents/{id}/avatar`, B-44). Nil when `ifNoneMatch` is
    /// still the current ETag (304).
    func avatar(agentId: String, ifNoneMatch: String?) async throws -> AvatarSheets.Download?
}

extension ChatService {
    /// A service with one buddy and nothing to switch (tests that do not switch).
    public func buddies() async throws -> [BuddyChoice] { [] }
    public func show(agentId: String, deviceId: String) async throws {}
    public func addBuddy(name: String) async throws -> BuddyChoice { throw ChatError("Can't add a buddy here.") }

    /// A service with no avatars to give: every agent plays the bundled giraffe.
    public func avatar(agentId: String, ifNoneMatch: String?) async throws -> AvatarSheets.Download? { nil }

    /// A message without mentions.
    public func send(text: String, clientId: String, conversationId: String, attachment: ItemAttachment?) async throws
        -> Message
    {
        try await send(text: text, clientId: clientId, conversationId: conversationId, attachment: attachment, mentions: [])
    }
}

/// A failure said for the person holding the phone.
public struct ChatError: Error, Equatable, LocalizedError {
    public var message: String
    /// The item attached to the message is gone from the hub (404 `artifact_not_found`): sending it again would fail
    /// the same way, so the message is offered without it.
    public var itemGone = false
    public init(_ message: String, itemGone: Bool = false) {
        self.message = message
        self.itemGone = itemGone
    }
    public var errorDescription: String? { message }

    static let unreachable = ChatError("Can't reach the hub right now.")
    static let itemGone = ChatError(
        "Not sent: the item you attached isn't on the hub any more. Tap the message to send it without the item.",
        itemGone: true)
    static let noConversation = ChatError("Not sent: the hub does not know this conversation.")
    static let unpaired = ChatError("The hub no longer accepts this phone. Pair it again.")
    static func unexpected(_ what: String) -> ChatError {
        ChatError("The hub answered something the app does not understand (\(what)).")
    }
}

/// The real hub, through the generated client.
public struct LiveChatService: ChatService {
    let client: Client
    /// The buddy this phone shows (the app's one `CurrentBuddy`; tests pass their own).
    let buddy: CurrentBuddy

    public init(client: Client, buddy: CurrentBuddy = .shared) {
        self.client = client
        self.buddy = buddy
    }

    public func avatar(agentId: String, ifNoneMatch: String?) async throws -> AvatarSheets.Download? {
        // One URL, two bodies by `Accept`: the JSON first, then the PNG. Both carry the same ETag.
        func get(_ type: Operations.GetAgentAvatar.AcceptableContentType) async throws
            -> Operations.GetAgentAvatar.Output.Ok?
        {
            let output: Operations.GetAgentAvatar.Output
            do {
                output = try await client.getAgentAvatar(.init(
                    path: .init(agentId: agentId),
                    headers: .init(ifNoneMatch: ifNoneMatch, accept: [.init(contentType: type)])))
            } catch { throw ChatError.unreachable }
            switch output {
            case .ok(let ok): return ok
            case .notModified: return nil
            case .unauthorized: throw ChatError.unpaired
            case .notFound: throw ChatError.unexpected("avatar, HTTP 404")
            case .undocumented(let status, _): throw ChatError.unexpected("avatar, HTTP \(status)")
            }
        }
        // The look may change between the two: then the timing and the image would not match. Ask again once.
        for _ in 0..<2 {
            guard let sheet = try await get(.json) else { return nil }
            let json = try JSONEncoder().encode(try sheet.body.json)
            guard let image = try await get(.png) else { return nil }
            guard image.headers.eTag == sheet.headers.eTag else { continue }
            let png = try await Data(collecting: try image.body.png, upTo: 2 * 1024 * 1024)
            return AvatarSheets.Download(json: json, png: png, etag: sheet.headers.eTag)
        }
        throw ChatError.unexpected("avatar changed while it was fetched")
    }

    private func me() async throws -> Me {
        let output: Operations.GetMe.Output
        do { output = try await client.getMe() } catch { throw ChatError.unreachable }
        switch output {
        case .ok(let ok): return try ok.body.json
        case .unauthorized: throw ChatError.unpaired
        case .undocumented(let status, _): throw ChatError.unexpected("me, HTTP \(status)")
        }
    }

    public func mainAgent() async throws -> MainAgent {
        let me = try await me()
        // The hub's word on which buddy this phone shows; offline from here on, `CurrentBuddy` remembers it.
        // If the hub can't say (no `/devices` yet, a hiccup), the buddy remembered stays; one no longer live (archived)
        // gives way to the first live buddy.
        if case .ok(let ok) = try? await client.listDevices(), let devices = try? ok.body.json,
           let mine = devices.first(where: { $0.id == me.deviceId }) {
            buddy.apply(deviceId: mine.id, agentId: mine.agentId, thisDevice: me.deviceId)
        }
        guard let agent = buddy.pick(from: me.agents) else { throw ChatError("This hub has no buddy yet.") }
        buddy.show(agent.id)
        return MainAgent(conversationId: agent.mainConversationId, name: agent.name, id: agent.id,
                         state: AvatarState(agent.state), look: agent.drawnLook, deviceId: me.deviceId)
    }

    public func buddies() async throws -> [BuddyChoice] {
        let me = try await me()
        var choices: [BuddyChoice] = []
        for agent in me.agents where agent.archivedAt == nil {
            var last: String?
            if case .ok(let ok) = try? await client.listMessages(
                path: .init(conversationId: agent.mainConversationId), query: .init(limit: 1)),
               let page = try? ok.body.json, let message = page.items.first {
                last = message.text.map { $0.split(whereSeparator: \.isNewline).joined(separator: " ") }
            }
            choices.append(BuddyChoice(id: agent.id, name: agent.name, look: agent.drawnLook, lastMessage: last))
        }
        return choices
    }

    public func show(agentId: String, deviceId: String) async throws {
        let output: Operations.UpdateDevice.Output
        do {
            output = try await client.updateDevice(path: .init(deviceId: deviceId), body: .json(.init(agentId: agentId)))
        } catch { throw ChatError("Couldn't switch: can't reach the hub.") }
        switch output {
        case .ok: return
        case .notFound: throw ChatError("That buddy is gone. Pick another one.")
        case .unauthorized: throw ChatError.unpaired
        case .badRequest: throw ChatError.unexpected("switch, HTTP 400")
        case .undocumented(let status, _): throw ChatError.unexpected("switch, HTTP \(status)")
        }
    }

    public func addBuddy(name: String) async throws -> BuddyChoice {
        let output: Operations.CreateAgent.Output
        do { output = try await client.createAgent(body: .json(.init(name: name))) } catch {
            throw ChatError("Couldn't add the buddy: can't reach the hub.")
        }
        switch output {
        case .created(let created):
            let agent = try created.body.json
            return BuddyChoice(id: agent.id, name: agent.name, look: agent.drawnLook)
        case .badRequest(let bad): throw ChatError((try? bad.body.json.message) ?? "The hub refused that name.")
        case .unauthorized: throw ChatError.unpaired
        case .undocumented(let status, _): throw ChatError.unexpected("new buddy, HTTP \(status)")
        }
    }

    public func searchMentions(query: String, kinds: [MentionKind]) async throws -> [Mention] {
        // One kind is asked for by name; several in one call, so the hub's newest-first order holds across them.
        let output: Operations.SearchMentions.Output
        let kind = kinds.count == 1 ? kinds[0] : nil
        do { output = try await client.searchMentions(query: .init(q: query, kind: kind)) } catch {
            throw ChatError.unreachable
        }
        switch output {
        case .ok(let ok): return try ok.body.json.filter { kinds.contains($0.kind) }
        case .unauthorized: throw ChatError.unpaired
        case .badRequest: throw ChatError.unexpected("mentions, HTTP 400")
        case .undocumented(let status, _): throw ChatError.unexpected("mentions, HTTP \(status)")
        }
    }

    public func send(text: String, clientId: String, conversationId: String, attachment: ItemAttachment?,
                     mentions: [Mention]) async throws -> Message
    {
        let output: Operations.SendMessage.Output
        do {
            output = try await client.sendMessage(
                path: .init(conversationId: conversationId),
                body: .json(.init(text: text, clientId: clientId, projectId: attachment?.projectId,
                                  artifactId: attachment?.artifactId, mentions: mentions.isEmpty ? nil : mentions)))
        } catch {
            throw ChatError("Not sent: can't reach the hub. Tap the message to try again.")
        }
        switch output {
        case .created(let created): return try created.body.json
        case .unauthorized: throw ChatError.unpaired
        case .notFound(let missing):
            // Which thing is gone, by its code: the attached item, or the conversation itself.
            throw (try? missing.body.json.code) == "artifact_not_found" ? ChatError.itemGone : ChatError.noConversation
        case .badRequest: throw ChatError("Not sent: the hub refused the message.")
        case .conflict: throw ChatError("Not sent: that thread has finished.")
        case .undocumented(let status, _): throw ChatError.unexpected("send, HTTP \(status)")
        }
    }

    public func approvals() async throws -> [Approval] {
        let output: Operations.ListApprovals.Output
        do { output = try await client.listApprovals() } catch { throw ChatError.unreachable }
        switch output {
        case .ok(let ok): return try ok.body.json
        case .unauthorized: throw ChatError.unpaired
        case .undocumented(let status, _): throw ChatError.unexpected("approvals, HTTP \(status)")
        }
    }

    public func answer(approvalId: String, approve: Bool) async throws -> Approval {
        let output: Operations.AnswerApproval.Output
        do {
            output = try await client.answerApproval(
                path: .init(approvalId: approvalId), body: .json(.init(decision: approve ? .approve : .deny)))
        } catch {
            throw ChatError("Couldn't send your answer. Check the connection and try again.")
        }
        switch output {
        case .ok(let ok): return try ok.body.json
        case .conflict(let conflict): return try conflict.body.json  // answered elsewhere, or expired
        case .notFound: throw ChatError("That question is gone from the hub.")
        case .unauthorized: throw ChatError.unpaired
        case .badRequest: throw ChatError.unexpected("answer, HTTP 400")
        case .undocumented(let status, _): throw ChatError.unexpected("answer, HTTP \(status)")
        }
    }

    public func artifactContent(id: String) async throws -> Data {
        let output: Operations.GetArtifactContent.Output
        do { output = try await client.getArtifactContent(path: .init(artifactId: id)) } catch {
            throw ChatError.unreachable
        }
        switch output {
        case .ok(let ok):
            switch ok.body {
            case .any(let body): return try await Data(collecting: body, upTo: 256 * 1024)
            }
        case .unauthorized: throw ChatError.unpaired
        case .notFound: throw ChatError("That item is gone from the hub.")
        case .badRequest: throw ChatError.unexpected("item content, HTTP 400")
        case .undocumented(let status, _): throw ChatError.unexpected("item content, HTTP \(status)")
        }
    }
}

extension AvatarState {
    /// The avatar for the contract's `AgentState` (`GET /me`, and the `agent.state` event).
    public init(_ state: AgentState) {
        self.init(kind: state.kind.rawValue, activeTasks: state.activeTasks)
    }
}
