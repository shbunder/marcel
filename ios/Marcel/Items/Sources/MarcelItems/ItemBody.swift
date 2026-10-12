import Foundation
import MarcelKit

/// What the viewer draws for an item, by kind. Pure: worked out from the item and its bytes, so the tests check
/// exactly what each kind becomes.
public enum ItemBody: Equatable, Sendable {
    case loading
    case failed(String)
    /// A Markdown or plain-text document, drawn natively.
    case markdown([ItemMarkdown.Part])
    /// An HTML document in a web view. JavaScript runs only when the hub made the HTML itself (`trusted`).
    case html(String, trusted: Bool)
    /// Anything else with bytes: shown with QuickLook.
    case file(FileItem)
    case dashboard(Dashboard)
    /// A dashboard or board whose JSON does not fit the schema: a plain message, never a crash.
    case brokenDashboard(String)
    case pr(PRCard)
    case link(LinkCard)

    /// Whether the viewer must fetch the item's bytes to draw it.
    public static func needsContent(_ kind: Components.Schemas.ArtifactKind) -> Bool {
        switch kind {
        case .pr, .link, .branch: false
        case .doc, .file, .dashboard, .board: true
        }
    }

    /// - Parameters:
    ///   - contentType: the shown version's type (an older version may differ from the current one).
    ///   - content: the shown version's bytes, as far as they have loaded.
    public static func make(_ artifact: Artifact, contentType: String?, content: ContentState?) -> ItemBody {
        switch artifact.kind {
        case .pr: return .pr(PRCard(artifact))
        case .link, .branch: return .link(LinkCard(artifact))
        case .doc, .file, .dashboard, .board: break
        }
        let data: Data
        switch content {
        case .loaded(let bytes): data = bytes
        case .failed(let problem): return .failed(problem)
        case .loading?, nil: return .loading
        }
        let type = (contentType ?? artifact.contentType ?? "").lowercased()
        switch artifact.kind {
        case .dashboard, .board:
            switch Dashboard.parse(data) {
            case .success(let dashboard): return .dashboard(dashboard)
            case .failure(let problem): return .brokenDashboard(problem.message)
            }
        case .doc where ItemWords.isHTML(type):
            return .html(String(decoding: data, as: UTF8.self), trusted: artifact.trusted == true)
        case .doc where type.hasPrefix("text/") || type.isEmpty:
            return .markdown(ItemMarkdown.parse(String(decoding: data, as: UTF8.self)))
        default:
            return .file(FileItem(artifact, contentType: type, data: data))
        }
    }
}

/// An item's bytes as the viewer has them, per version.
public enum ContentState: Equatable, Sendable {
    case loading
    case loaded(Data)
    case failed(String)
}

/// A file for QuickLook: its bytes, and a name with the right extension so QuickLook knows what it is.
public struct FileItem: Equatable, Sendable {
    public var id: String
    public var title: String
    /// "Bills 2026.pdf"
    public var fileName: String
    /// "PDF", "Picture"
    public var type: String
    public var size: Int
    public var data: Data

    init(_ artifact: Artifact, contentType: String, data: Data) {
        id = artifact.id
        title = artifact.title
        type = ItemWords.fileType(contentType)
        size = data.count
        self.data = data
        fileName = Self.fileName(title: artifact.title, contentType: contentType)
    }

    /// A safe file name: no slashes or leading dots, and the content type's extension when the title has none.
    static func fileName(title: String, contentType: String) -> String {
        var name = title.components(separatedBy: CharacterSet(charactersIn: "/\\:\0")).joined(separator: "-")
            .trimmingCharacters(in: .whitespaces)
        while name.hasPrefix(".") { name.removeFirst() }
        if name.isEmpty { name = "item" }
        let ext = Self.fileExtension(contentType)
        if let ext, !name.lowercased().hasSuffix("." + ext) { name += "." + ext }
        return name
    }

    static func fileExtension(_ contentType: String) -> String? {
        let type = contentType.split(separator: ";").first.map { $0.trimmingCharacters(in: .whitespaces) } ?? ""
        let known: [String: String] = [
            "application/pdf": "pdf", "image/png": "png", "image/jpeg": "jpg", "image/gif": "gif",
            "image/heic": "heic", "image/webp": "webp", "text/csv": "csv", "text/plain": "txt",
            "application/zip": "zip", "audio/mpeg": "mp3", "video/mp4": "mp4", "application/json": "json",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
        ]
        return known[type]
    }

    public var spoken: String {
        "\(type == "PDF" ? "PDF file" : type), \(ItemWords.size(size, spoken: true))"
    }
}

/// A pull request's card: its state, checks and review, and a way to open it on GitHub.
public struct PRCard: Equatable, Sendable {
    public enum State: String, Sendable { case open, draft, merged, closed }

    public var title: String
    public var number: Int?
    /// "shbunder/marcel"
    public var repository: String?
    public var state: State?
    public var checks: String?
    public var review: String?
    public var selfChange: Bool
    /// Only an https address on github.com: anything else is not offered as a button.
    public var github: URL?

    init(_ artifact: Artifact) {
        title = artifact.title
        number = artifact.pr?.number
        state = artifact.pr.flatMap { State(rawValue: $0.state.rawValue) }
        checks = artifact.pr?.checks.flatMap(ItemWords.checks)
        review = switch artifact.pr?.review {
        case .requested?: "Review requested"
        case .approved?: "Approved"
        case .changesRequested?: "Changes requested"
        case .none?, nil: nil
        }
        selfChange = artifact.selfChange == true
        github = artifact.url.flatMap(URL.init(string:)).flatMap(Self.githubLink)
        repository = github.map { url in url.pathComponents.dropFirst().prefix(2).joined(separator: "/") }
            .flatMap { $0.isEmpty ? nil : $0 }
    }

    /// The link if it is `https://github.com/…`; nil for anything else.
    public static func githubLink(_ url: URL) -> URL? {
        guard url.scheme?.lowercased() == "https", url.host()?.lowercased() == "github.com" else { return nil }
        return url
    }

    public var stateText: String {
        switch state {
        case .open?: "Open"
        case .draft?: "Draft"
        case .merged?: "Merged"
        case .closed?: "Closed"
        case nil: "Unknown"
        }
    }

    public var spoken: String {
        var words = ["Pull request" + (number.map { " number \($0)" } ?? ""), title, stateText]
        if let repository { words.append("In \(repository)") }
        if let checks { words.append(checks) }
        if let review { words.append(review) }
        if selfChange { words.append("This changes Marcel itself") }
        return words.joined(separator: ". ")
    }
}

/// A link or a branch: where it goes, and a button only when it is a web address.
public struct LinkCard: Equatable, Sendable {
    public var title: String
    public var isBranch: Bool
    public var host: String?
    /// Only http(s): anything else is shown as words, not offered as a button.
    public var url: URL?

    init(_ artifact: Artifact) {
        title = artifact.title
        isBranch = artifact.kind == .branch
        let parsed = artifact.url.flatMap(URL.init(string:))
        url = parsed.flatMap(ItemMarkdown.webLink)
        host = url?.host()
    }

    /// "Open in GitHub" for a branch on github.com, "Open in Safari" otherwise.
    public var buttonTitle: String {
        url.flatMap(PRCard.githubLink) != nil ? "Open in GitHub" : "Open in Safari"
    }
}
