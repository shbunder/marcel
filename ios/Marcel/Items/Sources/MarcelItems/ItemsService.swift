import Foundation
import MarcelKit
import OpenAPIRuntime

/// What the Items tab and the viewer ask of the hub. The live one talks to `contracts/app-api.yaml`; the tests use a
/// fake. Everything else on screen comes from the cache, which the live event stream keeps current.
@MainActor
public protocol ItemsService {
    /// `GET /artifacts`: every item, newest change first (B-21).
    func artifacts() async throws -> [Artifact]
    /// `GET /artifacts/{id}`: one item, when the cache does not have it yet.
    func artifact(id: String) async throws -> Artifact
    /// `GET /artifacts/{id}/content?version=…`: an item's bytes; nil `version` is the current one.
    func content(id: String, version: Int?) async throws -> Data
    /// `GET /artifacts/{id}/versions`: every version, newest first (B-34).
    func versions(id: String) async throws -> [ArtifactVersion]
    /// The agent's main conversation (`GET /me`): "Ask about this" goes there.
    func mainConversationId() async throws -> String
    /// `POST /conversations/{id}/messages`, tagged with the item's project.
    func send(text: String, clientId: String, conversationId: String, projectId: String) async throws -> Message
}

/// Why something in the Items tab did not work, in words a family member reads on a phone.
public enum ItemsError: Error, Equatable, LocalizedError {
    /// 404: the hub no longer has the item (or that version of it).
    case gone
    /// 400 on a send: the hub does not know the item's project (any more).
    case unknownProject
    case unauthorized
    case notPaired
    case offline
    /// The content is bigger than the app will hold in memory.
    case tooBig
    case unexpected(String)

    public var errorDescription: String? {
        switch self {
        case .gone: "The hub no longer has this item."
        case .unknownProject: "The hub doesn't know this item's project any more."
        case .unauthorized: "The hub no longer accepts this phone. Pair it again."
        case .notPaired: "This phone isn't paired with a hub yet."
        case .offline: "Couldn't reach the hub. Check the connection and try again."
        case .tooBig: "This item is too big to open on the phone."
        case .unexpected(let what): "The hub sent something the app doesn't understand (\(what))."
        }
    }

    /// Any error, as the screen says it.
    static func words(for error: any Error) -> String {
        (error as? ItemsError)?.errorDescription ?? "Something went wrong talking to the hub. Try again in a moment."
    }
}

/// The hub's REST API, signed with this phone's device token.
public struct LiveItemsService: ItemsService {
    let client: Client

    /// The most the viewer reads into memory (a PDF of bills, a long document).
    static let maxContent = 25 * 1024 * 1024
    /// How many pages of items the tab reads at most (200 each).
    static let maxPages = 5

    public init(client: Client) {
        self.client = client
    }

    /// The paired hub as pairing left it (address in UserDefaults, token in the Keychain); nil when not paired.
    public static func paired(defaults: UserDefaults = .standard) -> LiveItemsService? {
        guard let hub = UserDefaultsPairedHubStore(defaults: defaults).load() else { return nil }
        return LiveItemsService(client: HubClient.make(serverURL: hub.serverURL, tokenStore: KeychainTokenStore()))
    }

    public func artifacts() async throws -> [Artifact] {
        var items: [Artifact] = []
        var cursor: String?
        for _ in 0..<Self.maxPages {
            let page: Components.Schemas.ArtifactPage
            switch try await call({ try await client.listArtifacts(query: .init(cursor: cursor, limit: 200)) }) {
            case .ok(let ok): page = try ok.body.json
            case .unauthorized: throw ItemsError.unauthorized
            case .badRequest: throw ItemsError.unexpected("items, HTTP 400")
            case .undocumented(let status, _): throw ItemsError.unexpected("items, HTTP \(status)")
            }
            items += page.items
            guard let next = page.nextCursor else { break }
            cursor = next
        }
        return items
    }

    public func artifact(id: String) async throws -> Artifact {
        switch try await call({ try await client.getArtifact(path: .init(artifactId: id)) }) {
        case .ok(let ok): return try ok.body.json
        case .notFound: throw ItemsError.gone
        case .unauthorized: throw ItemsError.unauthorized
        case .undocumented(let status, _): throw ItemsError.unexpected("item, HTTP \(status)")
        }
    }

    public func content(id: String, version: Int?) async throws -> Data {
        let answer = try await call {
            try await client.getArtifactContent(path: .init(artifactId: id), query: .init(version: version))
        }
        switch answer {
        case .ok(let ok):
            switch ok.body {
            case .any(let body):
                if case .known(let length) = body.length, length > Self.maxContent { throw ItemsError.tooBig }
                do {
                    return try await Data(collecting: body, upTo: Self.maxContent)
                } catch is URLError {
                    throw ItemsError.offline
                } catch {
                    // The runtime's "too many bytes" error is not public; a body of unknown length ran over.
                    throw ItemsError.tooBig
                }
            }
        case .notFound: throw ItemsError.gone
        case .unauthorized: throw ItemsError.unauthorized
        case .badRequest: throw ItemsError.unexpected("content, HTTP 400")
        case .undocumented(let status, _): throw ItemsError.unexpected("content, HTTP \(status)")
        }
    }

    public func versions(id: String) async throws -> [ArtifactVersion] {
        switch try await call({ try await client.listArtifactVersions(path: .init(artifactId: id)) }) {
        case .ok(let ok): return try ok.body.json
        case .notFound: throw ItemsError.gone
        case .unauthorized: throw ItemsError.unauthorized
        case .undocumented(let status, _): throw ItemsError.unexpected("versions, HTTP \(status)")
        }
    }

    public func mainConversationId() async throws -> String {
        let me: Components.Schemas.Me
        switch try await call({ try await client.getMe() }) {
        case .ok(let ok): me = try ok.body.json
        case .unauthorized: throw ItemsError.unauthorized
        case .undocumented(let status, _): throw ItemsError.unexpected("me, HTTP \(status)")
        }
        guard let agent = me.agents.first else { throw ItemsError.unexpected("no agent") }
        return agent.mainConversationId
    }

    public func send(text: String, clientId: String, conversationId: String, projectId: String) async throws
        -> Message
    {
        let answer = try await call {
            try await client.sendMessage(
                path: .init(conversationId: conversationId),
                body: .json(.init(text: text, clientId: clientId, projectId: projectId)))
        }
        switch answer {
        case .created(let created): return try created.body.json
        case .badRequest: throw ItemsError.unknownProject
        case .unauthorized: throw ItemsError.unauthorized
        case .notFound: throw ItemsError.unexpected("send, HTTP 404")
        case .conflict: throw ItemsError.unexpected("send, HTTP 409")
        case .undocumented(let status, _): throw ItemsError.unexpected("send, HTTP \(status)")
        }
    }

    /// Runs a request; a transport failure (no network, hub down) becomes `.offline`.
    private func call<T>(_ request: () async throws -> T) async throws -> T {
        do {
            return try await request()
        } catch let error as ClientError where error.underlyingError is URLError {
            throw ItemsError.offline
        } catch is URLError {
            throw ItemsError.offline
        }
    }
}
