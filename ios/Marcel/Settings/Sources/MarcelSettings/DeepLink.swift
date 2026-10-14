import Foundation

/// A `marcel://` link, checked. Pushes carry one (`PushNotification.deep_link` in `contracts/app-api.yaml`), the ntfy
/// app opens one when a notification is tapped, and anything else on the phone can open one too, so every link is
/// treated as untrusted: only the `marcel` scheme, only the kinds below, one id of safe characters, nothing else.
public enum DeepLink: Equatable, Hashable, Sendable {
    /// `marcel://task/<task id>` — the quest view.
    case task(String)
    /// `marcel://message/<message id>` — that message in the chat (or its quest, for a quest's own thread).
    case message(String)
    /// `marcel://approval/<approval id>` — the quest that asks.
    case approval(String)
    /// `marcel://project/<slug>` — the project's room on the Map.
    case project(slug: String)
    /// `marcel://artifact/<artifact id>` — the item viewer.
    case artifact(String)
    /// `marcel://status` — the system status screen (a `broke` push about no one task).
    case status

    /// Why a link was not opened, in words for the person holding the phone.
    public enum Problem: Error, Equatable, Sendable, LocalizedError {
        /// Not `marcel://` at all.
        case notMarcel
        /// `marcel://pair?…`: a pairing code. Links never pair the phone: a web page could otherwise pair it with a
        /// stranger's hub. The code is scanned on the pairing screen.
        case pairing
        /// A kind this app does not know (a newer hub, or a typo).
        case unknownKind
        /// The right kind, but the id is missing, too long, or has characters an id never has.
        case damaged

        public var errorDescription: String? {
            switch self {
            case .notMarcel: "That isn't a Marcel link, so the app ignored it."
            case .pairing: "This phone is signed in already. To sign in again, use Sign out of this phone in Settings, then scan the new code."
            case .unknownKind: "That Marcel link points somewhere this app doesn't know. The app may need an update."
            case .damaged: "That Marcel link is damaged, so the app couldn't open it."
            }
        }
    }

    /// The longest link the app reads. Real ones are under 80 characters.
    static let maxLength = 300
    /// Ids the hub hands out (`tsk_…`, `msg_…`, `apr_…`, `art_…`): the contract's `[A-Za-z0-9_-]+`, capped.
    static let maxIdLength = 128

    /// Reads a link. Never throws for a good link and never crashes for a bad one.
    public static func parse(_ url: URL) -> Result<DeepLink, Problem> {
        parse(url.absoluteString)
    }

    public static func parse(_ text: String) -> Result<DeepLink, Problem> {
        guard text.count <= maxLength, let parts = URLComponents(string: text),
              parts.scheme?.lowercased() == "marcel" else { return .failure(.notMarcel) }
        let kind = (parts.percentEncodedHost ?? "").lowercased()
        if kind == "pair" { return .failure(.pairing) }
        guard ["task", "message", "approval", "project", "artifact", "status"].contains(kind) else {
            return .failure(.unknownKind)
        }
        // Nothing but `marcel://<kind>/<id>`: no user, port, query or fragment, no percent-escapes, one path part.
        guard parts.user == nil, parts.password == nil, parts.port == nil, parts.percentEncodedQuery == nil,
              parts.percentEncodedFragment == nil else { return .failure(.damaged) }
        let path = parts.percentEncodedPath
        if kind == "status" {
            return path.isEmpty || path == "/" ? .success(.status) : .failure(.damaged)
        }
        guard path.hasPrefix("/") else { return .failure(.damaged) }
        let id = String(path.dropFirst())
        switch kind {
        case "project":
            guard isSlug(id) else { return .failure(.damaged) }
            return .success(.project(slug: id))
        default:
            guard isId(id) else { return .failure(.damaged) }
            switch kind {
            case "task": return .success(.task(id))
            case "message": return .success(.message(id))
            case "approval": return .success(.approval(id))
            default: return .success(.artifact(id))
            }
        }
    }

    /// `[A-Za-z0-9_-]{1,128}`, ASCII only.
    public static func isId(_ text: String) -> Bool {
        !text.isEmpty && text.utf8.count <= maxIdLength && text.utf8.allSatisfy { byte in
            (byte >= 0x30 && byte <= 0x39) || (byte >= 0x41 && byte <= 0x5A) || (byte >= 0x61 && byte <= 0x7A)
                || byte == 0x5F || byte == 0x2D
        }
    }

    /// The contract's project slug: `^[a-z0-9][a-z0-9-]{0,39}$`.
    public static func isSlug(_ text: String) -> Bool {
        let bytes = Array(text.utf8)
        guard (1...40).contains(bytes.count) else { return false }
        func lowerOrDigit(_ byte: UInt8) -> Bool { (byte >= 0x30 && byte <= 0x39) || (byte >= 0x61 && byte <= 0x7A) }
        return lowerOrDigit(bytes[0]) && bytes.dropFirst().allSatisfy { lowerOrDigit($0) || $0 == 0x2D }
    }

    /// The link written back out, as the hub writes it.
    public var url: URL {
        let text = switch self {
        case .task(let id): "marcel://task/\(id)"
        case .message(let id): "marcel://message/\(id)"
        case .approval(let id): "marcel://approval/\(id)"
        case .project(let slug): "marcel://project/\(slug)"
        case .artifact(let id): "marcel://artifact/\(id)"
        case .status: "marcel://status"
        }
        return URL(string: text)!
    }
}
