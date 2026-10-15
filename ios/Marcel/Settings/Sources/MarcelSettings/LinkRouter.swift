import Foundation
import Observation

/// Where a link leads, once the app has looked it up. The app shows it: a tab, or a screen over the tabs.
public enum LinkDestination: Equatable, Hashable, Sendable {
    /// The quest view (`QuestView(taskId:)`).
    case quest(taskId: String)
    /// The Chat tab, scrolled to this message.
    case chat(messageId: String)
    /// The Map tab, inside this project's room.
    case room(projectId: String)
    /// The item viewer (`ArtifactViewer(artifactId:)`).
    case artifact(id: String)
    /// The system status screen; `alert` is the push that led here, when one did.
    case status(alert: PushAlert?)

    /// The tab this destination lives in, or nil when it opens over the tabs.
    public var tab: String? {
        switch self {
        case .chat: "chat"
        case .room: "map"
        case .quest, .artifact, .status: nil
        }
    }
}

/// What the router needs from the hub and the cache to turn a link into a place. The live one is
/// `LiveLinkLookup`; tests use a fake hub.
@MainActor
public protocol LinkLookup {
    /// The task an approval belongs to (`GET /approvals/{id}`, answered or not); nil when the hub has no such approval.
    func taskId(forApproval id: String) async throws -> String?
    /// The live project with this slug: the cache first, then `GET /projects`.
    func projectId(slug: String) async throws -> String?
    /// Where a message lives (`GET /messages/{id}`): its conversation and, for a quest's own thread, its task; nil
    /// when the hub has no such message.
    func message(id: String) async throws -> (conversationId: String, taskId: String?)?
    /// The main conversation, when known.
    var mainConversationId: String? { get }
}

/// Turns `marcel://` links and tapped pushes into a destination. Made once, before anything is on screen, so a link
/// that cold-launches the app (a tapped push, the ntfy app, a link in Safari) waits here until the tabs are up and
/// the router is `attach`ed to the paired hub. From then on a link is looked up and shown at once.
@MainActor
@Observable
public final class LinkRouter {
    /// Where to go now. The app shows it, then calls `consume()`.
    public private(set) var destination: LinkDestination?
    /// A tapped push about a buddy (`agent_id`, B-49): the app shows that buddy before opening the link. Cleared by
    /// `consumeBuddy()`.
    public private(set) var buddy: String?
    /// A link that could not be opened, in plain words. The app shows it until tapped away.
    public var notice: String?
    /// A link that arrived before the tabs were up.
    public private(set) var pending: Pending?

    public enum Pending: Equatable, Sendable {
        case link(DeepLink, alert: PushAlert?)
    }

    @ObservationIgnored private var lookup: (any LinkLookup)?
    /// True once the tabs attached the router, false once the pairing screen showed; nil right after launch.
    @ObservationIgnored public private(set) var paired: Bool?
    /// Bumps with every link, so a slow lookup never overrides a newer link.
    @ObservationIgnored private var generation = 0

    public init() {}

    public var isAttached: Bool { lookup != nil }

    // MARK: Links in

    /// `onOpenURL`: a link from the ntfy app, Safari, another app. Starts the lookup and returns.
    public func open(_ url: URL) {
        Task { await handle(url) }
    }

    /// Reads a link and goes there (or holds it until `attach`). Awaitable, for tests.
    public func handle(_ url: URL, alert: PushAlert? = nil) async {
        switch DeepLink.parse(url) {
        case .success(let link): await go(link, alert: alert)
        case .failure(.pairing):
            notice = paired == false ? Self.pairOnTheScreen : DeepLink.Problem.pairing.errorDescription
        case .failure(let problem): fail(problem.errorDescription)
        }
    }

    /// A pairing link while the phone is not paired: links never pair it, the pairing screen does.
    public static let pairOnTheScreen =
        "Links can't pair this phone. Scan the code with the camera on this screen, or paste the link below."

    /// A tapped push: its `deep_link`, and its words for the status screen.
    public func handle(push: PushPayload) async {
        if let agentId = push.agentId { buddy = agentId }
        guard let text = push.deepLink else {
            // A push with no link (or a bad one) still opened the app; say so instead of doing nothing.
            fail(DeepLink.Problem.damaged.errorDescription)
            return
        }
        switch DeepLink.parse(text) {
        case .success(let link): await go(link, alert: push.alert())
        case .failure(let problem): fail(problem.errorDescription)
        }
    }

    // MARK: The app's side

    /// The tabs are up and the phone is paired: look up whatever arrived before, and every link after.
    public func attach(_ lookup: any LinkLookup) async {
        self.lookup = lookup
        paired = true
        // Said on the pairing screen; stale once paired.
        if notice == Self.pairOnTheScreen { notice = nil }
        if case .link(let link, let alert) = pending {
            pending = nil
            await go(link, alert: alert)
        }
    }

    /// Unpaired: links wait again (they are looked up on the next hub's data once paired).
    public func detach() {
        lookup = nil
        paired = false
        destination = nil
        // A pairing link that arrived at launch, before the app knew it was not paired.
        if notice == DeepLink.Problem.pairing.errorDescription { notice = Self.pairOnTheScreen }
    }

    /// The app has shown `destination`.
    public func consume() {
        destination = nil
    }

    public func consumeBuddy() {
        buddy = nil
    }

    // MARK: Looking up

    private func go(_ link: DeepLink, alert: PushAlert?) async {
        generation += 1
        let mine = generation
        guard let lookup else {
            pending = .link(link, alert: alert)
            return
        }
        let result: Result<LinkDestination, LinkFailure>
        do {
            result = try await resolve(link, alert: alert, lookup: lookup)
        } catch {
            result = .failure(LinkFailure(error))
        }
        guard mine == generation else { return }
        switch result {
        case .success(let place):
            notice = nil
            destination = place
        case .failure(let failure):
            fail(failure.words)
        }
    }

    private func resolve(_ link: DeepLink, alert: PushAlert?, lookup: any LinkLookup) async throws
        -> Result<LinkDestination, LinkFailure>
    {
        switch link {
        case .task(let id):
            return .success(.quest(taskId: id))
        case .artifact(let id):
            return .success(.artifact(id: id))
        case .status:
            return .success(.status(alert: alert))
        case .approval(let id):
            guard let taskId = try await lookup.taskId(forApproval: id) else { return .failure(.noApproval) }
            return .success(.quest(taskId: taskId))
        case .project(let slug):
            guard let projectId = try await lookup.projectId(slug: slug) else { return .failure(.noProject) }
            return .success(.room(projectId: projectId))
        case .message(let id):
            guard let found = try await lookup.message(id: id) else { return .failure(.noMessage) }
            // A message in a quest's own thread lives with that quest; the app has no other thread view.
            if let taskId = found.taskId, let main = lookup.mainConversationId, found.conversationId != main {
                return .success(.quest(taskId: taskId))
            }
            return .success(.chat(messageId: id))
        }
    }

    private func fail(_ words: String?) {
        notice = words ?? DeepLink.Problem.damaged.errorDescription
    }
}

/// Why a well-formed link still led nowhere.
enum LinkFailure: Error, Equatable {
    case noApproval
    case noMessage
    case noProject
    case offline
    case refused
    case hubProblem
    case other

    /// A lookup that threw: what kind of failure it was.
    init(_ error: any Error) {
        self = switch error as? SettingsError {
        case .offline: .offline
        case .unauthorized, .notPaired: .refused
        case .hubProblem: .hubProblem
        default: .other
        }
    }

    var words: String {
        switch self {
        case .noApproval: "That question has gone: it was answered or it expired."
        case .noMessage: "That message isn't on the hub any more, so there's nothing to open."
        case .noProject: "Marcel has no project at that link any more. It may have been archived."
        case .offline: "Couldn't reach the hub to open that link. Check the connection and try again."
        case .refused: "The hub no longer accepts this phone, so that link couldn't be opened. Pair it again."
        case .hubProblem: "The hub had a problem opening that link. Try again in a minute."
        case .other: "Something went wrong opening that link. Try again in a moment."
        }
    }
}
