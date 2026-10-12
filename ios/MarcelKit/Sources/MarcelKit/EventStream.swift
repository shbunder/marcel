import Foundation
import Observation

/// Receives what the event stream delivers. `MarcelCache` is the production sink.
@MainActor
public protocol EventSink: AnyObject {
    /// The last event id applied; the stream reconnects with `since` set to it.
    var lastEventId: Int? { get }
    /// Applies one numbered event and records its id as `lastEventId`.
    func apply(_ frame: EventFrame) throws
    /// The hub no longer holds what we missed: re-fetch over REST, then record `lastEventId`.
    func resync(upTo lastEventId: Int) async throws
}

/// Keeps one WebSocket to `GET /ws` open. On a drop it waits, reconnects with `since = lastEventId` and
/// lets the hub replay what was missed. Events it already applied are skipped, so a replay never duplicates.
@MainActor
@Observable
public final class EventStream {
    public enum Status: Equatable, Sendable {
        case idle
        case connecting
        case live
        case offline(String)
        case unpaired
    }

    public private(set) var status: Status = .idle
    /// A frame the app could not read, said plainly. The stream carries on past it.
    public private(set) var lastProblem: String?

    @ObservationIgnored private let connector: any EventSocketConnector
    @ObservationIgnored private let sink: any EventSink
    @ObservationIgnored private let retryDelay: @Sendable (Int) -> Duration
    @ObservationIgnored private let sleep: @MainActor (Duration) async throws -> Void
    @ObservationIgnored private let onUnpaired: @MainActor () -> Void
    @ObservationIgnored private var failures = 0

    public init(
        connector: any EventSocketConnector,
        sink: any EventSink,
        retryDelay: @escaping @Sendable (Int) -> Duration = EventStream.backoff,
        sleep: @escaping @MainActor (Duration) async throws -> Void = { try await Task.sleep(for: $0) },
        onUnpaired: @escaping @MainActor () -> Void = {}
    ) {
        self.connector = connector
        self.sink = sink
        self.retryDelay = retryDelay
        self.sleep = sleep
        self.onUnpaired = onUnpaired
    }

    /// 1, 2, 4, 8, 16, then 30 seconds between attempts.
    public static let backoff: @Sendable (Int) -> Duration = { failures in
        .seconds(min(30, 1 << min(max(failures - 1, 0), 5)))
    }

    /// Runs until the task is cancelled or the hub refuses the device token.
    public func run() async {
        while !Task.isCancelled {
            status = .connecting
            var socket: (any EventSocket)?
            do {
                let open = try await connector.connect(since: sink.lastEventId)
                socket = open
                try await pump(open)
                open.close()
                continue  // a resync asked for a fresh connection; reconnect at once
            } catch is EventStreamError {
                socket?.close()
                status = .unpaired
                onUnpaired()
                return
            } catch {
                socket?.close()
                if Task.isCancelled { break }
                failures += 1
                let wait = retryDelay(failures)
                status = .offline(
                    "Lost the connection to the hub. Trying again in \(wait.components.seconds) s.")
            }
            do {
                try await sleep(retryDelay(failures))
            } catch {
                break
            }
        }
        status = .idle
    }

    /// Reads frames until the socket drops (throws) or a resync needs a reconnect (returns).
    private func pump(_ socket: any EventSocket) async throws {
        while true {
            try Task.checkCancellation()
            let data = try await socket.receive()
            let frame: EventFrame
            do {
                frame = try EventFrame.decode(data)
            } catch {
                lastProblem = "Skipped an update the app could not read. If things look stale, pull to refresh."
                continue
            }
            switch frame.event {
            case .hello:
                status = .live
                failures = 0
            case .resyncRequired(let lastEventId):
                try await sink.resync(upTo: lastEventId)
                return
            default:
                if let last = sink.lastEventId, frame.id <= last { continue }
                try sink.apply(frame)
            }
        }
    }
}
