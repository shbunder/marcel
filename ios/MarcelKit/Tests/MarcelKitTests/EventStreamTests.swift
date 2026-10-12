import Foundation
import XCTest

@testable import MarcelKit

@MainActor
final class EventStreamTests: XCTestCase {
    private func makeCache() throws -> MarcelCache {
        MarcelCache(container: try MarcelCache.makeContainer(inMemory: true))
    }

    /// Runs the stream until the scripted connector has nothing left, with no real waiting.
    private func run(_ connector: FakeConnector, into sink: any EventSink, onUnpaired: @escaping @MainActor () -> Void = {})
        async -> EventStream
    {
        let stream = EventStream(
            connector: connector, sink: sink, retryDelay: { _ in .zero },
            sleep: { _ in if connector.exhausted { throw CancellationError() } },
            onUnpaired: onUnpaired)
        await stream.run()
        return stream
    }

    func testAfterADropTheStreamReconnectsFromTheLastEventAndReplaysWithoutDuplicates() async throws {
        let cache = try makeCache()
        let connector = FakeConnector([
            .success(FakeSocket([Frames.hello(last: 2), Frames.message(id: 1, text: "one"), Frames.message(id: 2, text: "two")])),
            // The hub replays 2 (already applied) and 3, which arrived while the phone was offline.
            .success(FakeSocket([Frames.hello(last: 3, replayed: 2), Frames.message(id: 2, text: "two"), Frames.message(id: 3, text: "three")])),
        ])

        _ = await run(connector, into: cache)

        XCTAssertEqual(connector.sinceRequested.prefix(2), [nil, 2], "the reconnect asks for what came after event 2")
        XCTAssertEqual(try cache.messages(in: "cnv_main_marcel").map(\.text), ["one", "two", "three"])
        XCTAssertEqual(cache.lastEventId, 3)
    }

    func testAFreshStartResumesFromTheCachedEventId() async throws {
        let container = try MarcelCache.makeContainer(inMemory: true)
        let first = MarcelCache(container: container)
        _ = await run(FakeConnector([.success(FakeSocket([Frames.hello(last: 7), Frames.message(id: 7, text: "seven")]))]), into: first)

        let connector = FakeConnector([.success(FakeSocket([Frames.hello(last: 7)]))])
        _ = await run(connector, into: MarcelCache(container: container))

        XCTAssertEqual(connector.sinceRequested.first, 7)
    }

    func testATaskCardUpdatesInPlace() async throws {
        let cache = try makeCache()
        let connector = FakeConnector([
            .success(
                FakeSocket([
                    Frames.hello(last: 0),
                    Frames.task(id: 1, taskId: "tsk_1", state: "starting", type: "task.created"),
                    Frames.task(id: 2, taskId: "tsk_1", state: "working"),
                ]))
        ])

        _ = await run(connector, into: cache)

        let tasks = try cache.tasks()
        XCTAssertEqual(tasks.count, 1)
        XCTAssertEqual(tasks.first?.state, .working)
    }

    func testAnEventAlreadyAppliedIsNotAppliedAgain() async throws {
        let cache = try makeCache()
        let connector = FakeConnector([
            .success(
                FakeSocket([
                    Frames.hello(last: 0),
                    Frames.task(id: 1, taskId: "tsk_1", state: "starting", type: "task.created"),
                    Frames.task(id: 2, taskId: "tsk_1", state: "working"),
                ])),
            // An older event delivered again must not roll the task back.
            .success(FakeSocket([Frames.hello(last: 2), Frames.task(id: 1, taskId: "tsk_1", state: "starting")])),
        ])

        _ = await run(connector, into: cache)

        XCTAssertEqual(try cache.tasks().first?.state, .working)
        XCTAssertEqual(cache.lastEventId, 2)
    }

    func testResyncRequiredRefetchesThenReconnectsFromTheHubsLastEvent() async throws {
        final class Refresher: CacheRefresher {
            var calls = 0
            func refresh(into cache: MarcelCache) async throws {
                calls += 1
                try cache.upsert(try EventFrameFixture.message(id: "msg_refetched", text: "from REST"))
            }
        }
        let refresher = Refresher()
        let cache = try makeCache()
        cache.refresher = refresher
        let connector = FakeConnector([
            .success(FakeSocket([Frames.resync(last: 1288)])),
            .success(FakeSocket([Frames.hello(last: 1288)])),
        ])

        _ = await run(connector, into: cache)

        XCTAssertEqual(refresher.calls, 1)
        XCTAssertEqual(connector.sinceRequested.prefix(2), [nil, 1288])
        XCTAssertEqual(cache.lastEventId, 1288)
        XCTAssertEqual(try cache.messages(in: "cnv_main_marcel").map(\.text), ["from REST"])
    }

    func testARefusedTokenUnpairsAndStopsRetrying() async throws {
        let cache = try makeCache()
        var unpaired = 0
        let connector = FakeConnector([
            .success(FakeSocket([], then: EventStreamError.unauthorized)),
            .success(FakeSocket([Frames.hello(last: 0)])),
        ])

        let stream = await run(connector, into: cache, onUnpaired: { unpaired += 1 })

        XCTAssertEqual(unpaired, 1)
        XCTAssertEqual(stream.status, .unpaired)
        XCTAssertEqual(connector.sinceRequested.count, 1, "no reconnect with a refused token")
    }

    func testAnUnreachableHubIsRetriedWithBackoffAndSaysSo() async throws {
        final class Box { var stream: EventStream?; var waits: [Duration] = []; var statuses: [EventStream.Status] = [] }
        let box = Box()
        let cache = try makeCache()
        let connector = FakeConnector([.failure(Offline()), .failure(Offline()), .success(FakeSocket([Frames.hello(last: 0)]))])
        let stream = EventStream(
            connector: connector, sink: cache,
            sleep: { wait in
                box.waits.append(wait)
                if let status = box.stream?.status { box.statuses.append(status) }
                if connector.exhausted { throw CancellationError() }
            })
        box.stream = stream

        await stream.run()

        XCTAssertEqual(connector.sinceRequested.count, 3)
        XCTAssertEqual(box.waits, [.seconds(1), .seconds(2), .seconds(1)], "backs off, and starts over once live")
        XCTAssertEqual(box.statuses.first, .offline("Lost the connection to the hub. Trying again in 1 s."))
        XCTAssertEqual(stream.status, .idle)
        XCTAssertEqual([1, 2, 3, 6, 9].map(EventStream.backoff), [.seconds(1), .seconds(2), .seconds(4), .seconds(30), .seconds(30)])
    }

    func testAnUnreadableFrameIsSkippedAndSaidPlainly() async throws {
        let cache = try makeCache()
        let connector = FakeConnector([
            .success(FakeSocket([Frames.hello(last: 0), #"{"id":1,"type":"message.created","at":"x","data":{}}"#, Frames.message(id: 2, text: "fine")]))
        ])

        let stream = await run(connector, into: cache)

        XCTAssertEqual(try cache.messages(in: "cnv_main_marcel").map(\.text), ["fine"])
        XCTAssertNotNil(stream.lastProblem)
    }

    func testTheStreamURLUsesWebSocketSchemesAndSince() {
        let https = URL(string: "https://marcel-bot.com/api")!
        let http = URL(string: "http://127.0.0.1:7499/api")!
        XCTAssertEqual(URLSessionEventSocketConnector.streamURL(serverURL: https, since: nil).absoluteString, "wss://marcel-bot.com/api/ws")
        XCTAssertEqual(URLSessionEventSocketConnector.streamURL(serverURL: http, since: 42).absoluteString, "ws://127.0.0.1:7499/api/ws?since=42")
    }
}
