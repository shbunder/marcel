import Foundation

/// A Markdown document as blocks the viewer draws natively: headings, paragraphs, list items, quotes, code and
/// tables. The pixel fonts have no bold or italic, so emphasis is dropped and the words stay. Links stay as their
/// words in the text and are listed under their block as buttons — only `http` and `https` addresses with a host;
/// `javascript:`, `file:`, `tel:` and app links stay plain words.
public enum ItemMarkdown {
    public enum Block: Equatable, Sendable {
        case heading(level: Int, text: String)
        case paragraph(String)
        /// "• " or "3. " before the text; `depth` 0 is the outer list.
        case listItem(marker: String, text: String, depth: Int)
        case quote(String)
        case code(String)
        case table(header: [String], rows: [[String]])
        case rule
    }

    public struct Link: Equatable, Sendable {
        public var title: String
        public var url: URL
    }

    public struct Part: Equatable, Sendable {
        public var block: Block
        public var links: [Link]
    }

    public static func parse(_ markdown: String) -> [Part] {
        let options = AttributedString.MarkdownParsingOptions(
            allowsExtendedAttributes: false, interpretedSyntax: .full, failurePolicy: .returnPartiallyParsedIfPossible)
        guard let parsed = try? AttributedString(markdown: markdown, options: options) else {
            let text = markdown.trimmingCharacters(in: .whitespacesAndNewlines)
            return text.isEmpty ? [] : [Part(block: .paragraph(text), links: [])]
        }

        var parts: [Part] = []
        // The block being collected: its identity, what it is, its text and links.
        var current: (id: Int, kind: Kind, text: String, links: [Link])?
        // A table being collected: its identity, header and rows (cells keyed by row, then column).
        var table: (id: Int, header: [Int: String], rows: [Int: [Int: String]])?
        var lastLink: URL?

        func flushBlock() {
            guard let block = current else { return }
            current = nil
            let text = block.text.trimmingCharacters(in: .whitespacesAndNewlines)
            switch block.kind {
            case .rule: parts.append(Part(block: .rule, links: []))
            case .code: parts.append(Part(block: .code(block.text.trimmingCharacters(in: .newlines)), links: []))
            default:
                guard !text.isEmpty else { return }
                parts.append(Part(block: block.kind.block(text), links: tidy(block.links)))
            }
        }

        func flushTable() {
            guard let done = table else { return }
            table = nil
            let width = max(done.header.keys.max() ?? -1, done.rows.values.flatMap(\.keys).max() ?? -1) + 1
            let header = (0..<width).map { done.header[$0] ?? "" }
            let rows = done.rows.keys.sorted().map { row in (0..<width).map { done.rows[row]?[$0] ?? "" } }
            parts.append(Part(block: .table(header: header, rows: rows), links: []))
        }

        for run in parsed.runs {
            let piece = String(parsed[run.range].characters)
            let components = run.presentationIntent?.components ?? []
            let link = run.link.flatMap(webLink)

            // Tables: every cell is its own block; collect them by row and column.
            if let tableIntent = components.first(where: { if case .table = $0.kind { true } else { false } }) {
                flushBlock()
                if table?.id != tableIntent.identity {
                    flushTable()
                    table = (tableIntent.identity, [:], [:])
                }
                var column = 0
                var row: Int?
                for component in components {
                    switch component.kind {
                    case .tableCell(let index): column = index
                    case .tableRow(let index): row = index
                    case .tableHeaderRow: row = nil
                    default: break
                    }
                }
                if let row {
                    table?.rows[row, default: [:]][column, default: ""] += piece
                } else {
                    table?.header[column, default: ""] += piece
                }
                lastLink = nil
                continue
            }
            flushTable()

            let identity = components.first?.identity ?? -1
            if current?.id != identity {
                flushBlock()
                current = (identity, Kind(components), "", [])
            }
            current?.text += piece
            if let link {
                if link == lastLink, var links = current?.links, !links.isEmpty {
                    links[links.count - 1].title += piece  // one link split over runs (a bold word inside it)
                    current?.links = links
                } else {
                    current?.links.append(Link(title: piece, url: link))
                }
            }
            lastLink = link
        }
        flushBlock()
        flushTable()
        return parts
    }

    /// The link if it is an http(s) address with a host; nil for anything else.
    public static func webLink(_ url: URL) -> URL? {
        guard let scheme = url.scheme?.lowercased(), scheme == "http" || scheme == "https",
              let host = url.host(), !host.isEmpty else { return nil }
        return url
    }

    /// The document's words, for VoiceOver and for the first lines of a still.
    public static func plainText(_ parts: [Part]) -> String {
        parts.map { part in
            switch part.block {
            case .heading(_, let text), .paragraph(let text), .quote(let text), .code(let text): text
            case .listItem(let marker, let text, _): marker.hasPrefix("•") ? text : marker + text
            case .table(let header, let rows):
                ([header] + rows).map { $0.joined(separator: ", ") }.joined(separator: ". ")
            case .rule: ""
            }
        }
        .filter { !$0.isEmpty }
        .joined(separator: "\n")
    }

    private static func tidy(_ links: [Link]) -> [Link] {
        links.map { link in
            let title = link.title.trimmingCharacters(in: .whitespacesAndNewlines)
            return Link(title: title.isEmpty ? link.url.absoluteString : title, url: link.url)
        }
    }

    /// What a run's innermost block is.
    private enum Kind: Equatable {
        case heading(Int)
        case paragraph
        case listItem(String, Int)
        case quote
        case code
        case rule

        init(_ components: [PresentationIntent.IntentType]) {
            var kind = Kind.paragraph
            var ordinal: Int?
            var depth = -1
            var marker: String?
            var quoted = false
            for component in components {
                switch component.kind {
                case .header(let level): kind = .heading(level)
                case .codeBlock: kind = .code
                case .thematicBreak: kind = .rule
                case .blockQuote: quoted = true
                case .listItem(let number): if ordinal == nil { ordinal = number }
                case .orderedList:
                    depth += 1
                    if marker == nil { marker = ordinal.map { "\($0). " } ?? "" }
                case .unorderedList:
                    depth += 1
                    if marker == nil { marker = "• " }
                default: continue
                }
            }
            if case .paragraph = kind {
                if let marker { kind = .listItem(marker, max(0, depth)) } else if quoted { kind = .quote }
            }
            self = kind
        }

        func block(_ text: String) -> Block {
            switch self {
            case .heading(let level): .heading(level: level, text: text)
            case .paragraph: .paragraph(text)
            case .listItem(let marker, let depth): .listItem(marker: marker, text: text, depth: depth)
            case .quote: .quote(text)
            case .code: .code(text)
            case .rule: .rule
            }
        }
    }
}
