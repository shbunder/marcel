import Foundation
import MarcelKit

/// A quest's events in `seq` order, merged from the history (`GET /tasks/{id}/events`) and the live stream
/// (`task.event`), which overlap and can arrive in any order. An event is kept once per `seq`, so a replay never
/// duplicates; `missingAfter` says where the events stop being contiguous, so a gap is fetched instead of skipped.
public struct QuestTranscript: Equatable, Sendable {
    public private(set) var events: [QuestEvent] = []
    private var seqs: Set<Int> = []
    /// Every event up to this `seq` is here (0 before the first one).
    public private(set) var contiguousThrough = 0

    public init() {}

    public var lastSeq: Int { events.last?.seq ?? 0 }

    /// The `after` to fetch from when events past a hole have arrived (a live event that skipped some); nil when
    /// there is no hole.
    public var missingAfter: Int? { lastSeq > contiguousThrough ? contiguousThrough : nil }

    /// The hub's history does not start at 1 (it kept only the newer events): count from its first one.
    public mutating func startsAt(_ firstSeq: Int) {
        guard events.isEmpty || firstSeq <= (events.first?.seq ?? firstSeq) else { return }
        contiguousThrough = max(contiguousThrough, firstSeq - 1)
        advance()
    }

    /// Adds events not seen yet; returns how many were new.
    @discardableResult
    public mutating func merge(_ incoming: [QuestEvent]) -> Int {
        var added = 0
        for event in incoming where seqs.insert(event.seq).inserted {
            added += 1
            if event.seq > lastSeq {
                events.append(event)
            } else {
                // Out of order (a page that fills a gap): insert in place.
                let index = events.partitioningIndex { $0.seq > event.seq }
                events.insert(event, at: index)
            }
        }
        if added > 0 { advance() }
        return added
    }

    public mutating func merge(_ incoming: [TaskEvent]) -> Int {
        merge(incoming.map(QuestEvent.init))
    }

    private mutating func advance() {
        while seqs.contains(contiguousThrough + 1) { contiguousThrough += 1 }
    }
}

extension Array {
    /// The first index whose element satisfies `belongsAfter`, for an array already partitioned by it.
    func partitioningIndex(where belongsAfter: (Element) -> Bool) -> Int {
        var low = 0, high = count
        while low < high {
            let mid = (low + high) / 2
            if belongsAfter(self[mid]) { high = mid } else { low = mid + 1 }
        }
        return low
    }
}
