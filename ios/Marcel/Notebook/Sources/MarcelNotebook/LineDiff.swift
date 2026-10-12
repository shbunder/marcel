import Foundation

/// The hub's version of a note against yours, line by line, for "changed elsewhere". Lines the same in both are
/// folded to "3 lines the same" except one on each side of a change, so the change is easy to find on a phone.
public enum LineDiff {
    public enum Line: Equatable, Sendable {
        /// In both.
        case same(String)
        /// Only in the hub's version (someone else wrote it, or you deleted it).
        case theirs(String)
        /// Only in yours.
        case yours(String)
        /// Lines in both that are not shown.
        case folded(Int)

        /// What VoiceOver reads.
        public var spoken: String {
            switch self {
            case .same(let text): "Same in both: \(text.isEmpty ? "empty line" : text)"
            case .theirs(let text): "Only in the hub's version: \(text.isEmpty ? "empty line" : text)"
            case .yours(let text): "Only in yours: \(text.isEmpty ? "empty line" : text)"
            case .folded(let count): count == 1 ? "1 line the same" : "\(count) lines the same"
            }
        }
    }

    /// Every line of both, in order: for each change, the hub's lines first, then yours.
    public static func lines(theirs: String, yours: String) -> [Line] {
        let old = split(theirs), new = split(yours)
        let difference = new.difference(from: old)
        var removed = Set<Int>(), inserted = Set<Int>()
        for change in difference {
            switch change {
            case .remove(let offset, _, _): removed.insert(offset)
            case .insert(let offset, _, _): inserted.insert(offset)
            }
        }
        var lines: [Line] = []
        var i = 0, j = 0
        while i < old.count || j < new.count {
            if i < old.count, removed.contains(i) {
                lines.append(.theirs(old[i]))
                i += 1
            } else if j < new.count, inserted.contains(j) {
                lines.append(.yours(new[j]))
                j += 1
            } else if i < old.count, j < new.count {
                lines.append(.same(new[j]))
                i += 1
                j += 1
            } else {
                break  // cannot happen: a line left over on one side is always a removal or an insertion
            }
        }
        return lines
    }

    /// `lines` with long runs of unchanged lines folded, keeping `context` lines next to each change.
    public static func folded(theirs: String, yours: String, context: Int = 1) -> [Line] {
        let all = lines(theirs: theirs, yours: yours)
        let changed = all.indices.filter { if case .same = all[$0] { false } else { true } }
        guard !changed.isEmpty else { return [] }
        var keep = Set<Int>()
        for index in changed {
            for near in max(0, index - context)...min(all.count - 1, index + context) { keep.insert(near) }
        }
        var result: [Line] = []
        var skipped = 0
        for index in all.indices {
            if keep.contains(index) {
                if skipped > 0 { result.append(.folded(skipped)) }
                skipped = 0
                result.append(all[index])
            } else {
                skipped += 1
            }
        }
        if skipped > 0 { result.append(.folded(skipped)) }
        return result
    }

    private static func split(_ text: String) -> [String] {
        var lines = text.components(separatedBy: "\n")
        if lines.last == "" { lines.removeLast() }  // a final newline is not an extra empty line
        return lines
    }
}
