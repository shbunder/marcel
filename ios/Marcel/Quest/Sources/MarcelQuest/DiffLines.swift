import Foundation

/// A diff event made ready to draw: its lines with their kind, capped so a huge Write does not flood the screen.
public struct DiffLines: Equatable, Sendable {
    public struct Line: Equatable, Sendable {
        public enum Kind: Equatable, Sendable { case added, removed, context, hunk }
        public var kind: Kind
        /// The line without its '+', '-' or ' ' (a hunk line is "@@ -4,2 +4,2 @@").
        public var text: String
    }

    public var lines: [Line]
    public var hiddenLines: Int
    public var added: Int
    public var removed: Int

    public static let maxLines = 60

    public init(_ hunks: [DiffHunk], maxLines: Int = Self.maxLines) {
        var lines: [Line] = []
        var hidden = 0, added = 0, removed = 0
        func keep(_ line: Line) {
            if lines.count < maxLines { lines.append(line) } else { hidden += 1 }
        }
        for hunk in hunks {
            let oldCount = hunk.lines.filter { !$0.hasPrefix("+") }.count
            let newCount = hunk.lines.filter { !$0.hasPrefix("-") }.count
            keep(Line(kind: .hunk, text: "@@ -\(hunk.oldStart),\(oldCount) +\(hunk.newStart),\(newCount) @@"))
            for raw in hunk.lines {
                let body = String(raw.dropFirst())
                let capped = body.count > CappedText.maxLineLength
                    ? String(body.prefix(CappedText.maxLineLength)) + "…" : body
                switch raw.first {
                case "+":
                    added += 1
                    keep(Line(kind: .added, text: capped))
                case "-":
                    removed += 1
                    keep(Line(kind: .removed, text: capped))
                default:
                    keep(Line(kind: .context, text: capped))
                }
            }
        }
        self.lines = lines
        hiddenLines = hidden
        self.added = added
        self.removed = removed
    }

    /// "+1 −1", for the folded row.
    public var counts: String { "+\(added) −\(removed)" }

    /// "1 line added, 1 line removed", for VoiceOver.
    public var spokenCounts: String {
        func words(_ n: Int, _ what: String) -> String { "\(n) line\(n == 1 ? "" : "s") \(what)" }
        return "\(words(added, "added")), \(words(removed, "removed"))"
    }

    public var moreLine: String? {
        hiddenLines > 0 ? "…and \(hiddenLines) more line\(hiddenLines == 1 ? "" : "s")" : nil
    }
}

/// A small, safe syntax colourer for diff lines: comments, strings, numbers and the common keywords of the language
/// the file's extension names. It only splits text into coloured runs; nothing in the text is ever interpreted.
public enum SyntaxColours {
    public enum Token: Equatable, Sendable { case plain, keyword, string, comment, number }

    public struct Language: Equatable, Sendable {
        var lineComment: [String]
        var keywords: Set<String>
    }

    public static func language(for filePath: String) -> Language? {
        let ext = (filePath as NSString).pathExtension.lowercased()
        switch ext {
        case "swift":
            return Language(lineComment: ["//"], keywords: [
                "func", "let", "var", "if", "else", "guard", "return", "struct", "class", "enum", "case", "switch",
                "import", "public", "private", "static", "self", "true", "false", "nil", "for", "in", "while", "try",
                "throw", "throws", "async", "await", "init", "extension", "protocol", "some", "any",
            ])
        case "py":
            return Language(lineComment: ["#"], keywords: [
                "def", "class", "return", "if", "elif", "else", "for", "while", "in", "import", "from", "as", "with",
                "try", "except", "finally", "raise", "None", "True", "False", "and", "or", "not", "is", "lambda",
                "async", "await", "pass", "yield",
            ])
        case "js", "ts", "tsx", "jsx", "mjs":
            return Language(lineComment: ["//"], keywords: [
                "function", "const", "let", "var", "if", "else", "return", "class", "import", "export", "from",
                "for", "while", "new", "this", "true", "false", "null", "undefined", "async", "await", "try",
                "catch", "throw", "interface", "type",
            ])
        case "go":
            return Language(lineComment: ["//"], keywords: [
                "func", "package", "import", "var", "const", "type", "struct", "interface", "if", "else", "for",
                "range", "return", "go", "defer", "nil", "true", "false", "map", "chan", "switch", "case",
            ])
        case "rs":
            return Language(lineComment: ["//"], keywords: [
                "fn", "let", "mut", "pub", "struct", "enum", "impl", "trait", "use", "mod", "if", "else", "match",
                "for", "in", "while", "loop", "return", "self", "Self", "true", "false", "None", "Some",
            ])
        case "sh", "bash", "zsh":
            return Language(lineComment: ["#"], keywords: [
                "if", "then", "else", "elif", "fi", "for", "do", "done", "while", "case", "esac", "function",
                "return", "export", "local",
            ])
        case "yaml", "yml", "toml":
            return Language(lineComment: ["#"], keywords: ["true", "false", "null"])
        case "json":
            return Language(lineComment: [], keywords: ["true", "false", "null"])
        default:
            return nil
        }
    }

    /// The line as runs of text, each with its token kind. Joined, the runs are exactly the line.
    public static func runs(_ line: String, language: Language?) -> [(String, Token)] {
        guard let language else { return [(line, .plain)] }
        var runs: [(String, Token)] = []
        var plain = ""
        func flush() {
            if !plain.isEmpty { runs.append((plain, .plain)) }
            plain = ""
        }
        let chars = Array(line)
        var i = 0
        while i < chars.count {
            let c = chars[i]
            // A comment runs to the end of the line.
            if language.lineComment.contains(where: { matches($0, chars, at: i) }) {
                flush()
                runs.append((String(chars[i...]), .comment))
                return runs
            }
            if c == "\"" || c == "'" || c == "`" {
                flush()
                var j = i + 1
                while j < chars.count, chars[j] != c {
                    j += chars[j] == "\\" ? 2 : 1
                }
                let end = min(j + 1, chars.count)
                runs.append((String(chars[i..<end]), .string))
                i = end
                continue
            }
            if c.isNumber, i == 0 || !(chars[i - 1].isLetter || chars[i - 1] == "_") {
                flush()
                var j = i
                while j < chars.count, chars[j].isNumber || chars[j] == "." || chars[j] == "_" { j += 1 }
                runs.append((String(chars[i..<j]), .number))
                i = j
                continue
            }
            if c.isLetter || c == "_" {
                var j = i
                while j < chars.count, chars[j].isLetter || chars[j].isNumber || chars[j] == "_" { j += 1 }
                let word = String(chars[i..<j])
                if language.keywords.contains(word) {
                    flush()
                    runs.append((word, .keyword))
                } else {
                    plain += word
                }
                i = j
                continue
            }
            plain.append(c)
            i += 1
        }
        flush()
        return runs
    }

    private static func matches(_ marker: String, _ chars: [Character], at index: Int) -> Bool {
        let marker = Array(marker)
        guard index + marker.count <= chars.count else { return false }
        return Array(chars[index..<index + marker.count]) == marker
    }
}
