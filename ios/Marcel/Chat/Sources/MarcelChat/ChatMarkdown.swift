import Foundation

/// Marcel writes Markdown. The dialogue box draws text in the pixel body font, which has no bold or italic, so
/// the Markdown is turned into clean text: no `**`, backticks or link brackets; paragraphs, headings and list
/// items keep their own lines; bullets become "•" and numbered items keep their number. Links stay as their words
/// in the text and are listed separately, so the box can offer each one as a button.
public enum ChatMarkdown {
    public struct Link: Equatable, Sendable {
        public var title: String
        public var url: URL
    }

    public struct Rendered: Equatable, Sendable {
        public var text: String
        public var links: [Link]
    }

    public static func render(_ markdown: String) -> Rendered {
        let options = AttributedString.MarkdownParsingOptions(
            allowsExtendedAttributes: false, interpretedSyntax: .full, failurePolicy: .returnPartiallyParsedIfPossible)
        guard let parsed = try? AttributedString(markdown: markdown, options: options) else {
            return Rendered(text: markdown.trimmingCharacters(in: .whitespacesAndNewlines), links: [])
        }

        var text = ""
        var links: [Link] = []
        var lastBlock: Int?
        var lastList: Int?
        var lastLink: URL?
        for run in parsed.runs {
            let piece = String(parsed[run.range].characters)
            let intent = run.presentationIntent
            let block = intent?.components.first?.identity
            if block != lastBlock {
                let list = intent?.components.first { component in
                    switch component.kind {
                    case .orderedList, .unorderedList: true
                    default: false
                    }
                }
                if !text.isEmpty {
                    // Items of one list sit on consecutive lines; anything else gets a blank line between.
                    text += (list != nil && list?.identity == lastList) ? "\n" : "\n\n"
                }
                text += prefix(for: intent)
                lastBlock = block
                lastList = list?.identity
            }
            text += piece
            // Only web links become buttons. `javascript:`, `tel:`, app links and the like stay plain words.
            if let url = run.link.flatMap(Self.webLink) {
                if url == lastLink, !links.isEmpty {
                    links[links.count - 1].title += piece  // one link split over runs (a bold word inside it)
                } else {
                    links.append(Link(title: piece, url: url))
                }
            }
            lastLink = run.link.flatMap(Self.webLink)
        }
        links = links.map { link in
            let title = link.title.trimmingCharacters(in: .whitespacesAndNewlines)
            return Link(title: title.isEmpty ? link.url.absoluteString : title, url: link.url)
        }
        return Rendered(text: text.trimmingCharacters(in: .whitespacesAndNewlines), links: links)
    }

    /// The link if it is an http(s) address with a host; nil for anything else.
    public static func webLink(_ url: URL) -> URL? {
        guard let scheme = url.scheme?.lowercased(), scheme == "http" || scheme == "https",
              let host = url.host(), !host.isEmpty else { return nil }
        return url
    }

    /// "• " for a bullet, "3. " for the third numbered item, nothing for other blocks.
    private static func prefix(for intent: PresentationIntent?) -> String {
        guard let components = intent?.components else { return "" }
        var ordinal: Int?
        for component in components {
            switch component.kind {
            case .listItem(let number): ordinal = number
            case .orderedList: return ordinal.map { "\($0). " } ?? ""
            case .unorderedList: return ordinal == nil ? "" : "• "
            default: continue
            }
        }
        return ""
    }
}
