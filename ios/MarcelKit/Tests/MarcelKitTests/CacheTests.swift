import Foundation
import XCTest

@testable import MarcelKit

@MainActor
final class CacheTests: XCTestCase {
    func testMessagesAreKeptOncePerIdInTimeOrder() throws {
        let cache = MarcelCache(container: try MarcelCache.makeContainer(inMemory: true))
        try cache.apply(try EventFrame.decode(Data(Frames.message(id: 2, text: "second").utf8)))
        try cache.apply(try EventFrame.decode(Data(Frames.message(id: 1, text: "first").utf8)))
        try cache.upsert(try EventFrameFixture.message(id: "msg_1", text: "first, edited"))

        XCTAssertEqual(try cache.messages(in: "cnv_main_marcel").map(\.text), ["first, edited", "second"])
        XCTAssertEqual(try cache.messages(in: "cnv_other"), [])
    }

    func testArtifactsAreCachedNewestFirst() throws {
        let cache = MarcelCache(container: try MarcelCache.makeContainer(inMemory: true))
        let data = try Data(contentsOf: EventDecodingTests.contracts.appending(path: "events.schema.json"))
        let schema = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
        let example = try XCTUnwrap(((schema["$defs"] as? [String: Any])?["ArtifactCreated"] as? [String: Any])?["examples"] as? [Any])
        try cache.apply(try EventFrame.decode(try JSONSerialization.data(withJSONObject: example[0])))

        XCTAssertEqual(try cache.artifacts().map(\.id), ["art_01J9ZT1"])
        XCTAssertEqual(cache.lastEventId, 1310)
    }

    func testClearEmptiesEverything() throws {
        let cache = MarcelCache(container: try MarcelCache.makeContainer(inMemory: true))
        try cache.apply(try EventFrame.decode(Data(Frames.message(id: 1, text: "x").utf8)))
        try cache.apply(try EventFrame.decode(Data(Frames.task(id: 2, taskId: "tsk_1", state: "working").utf8)))

        try cache.clear()

        XCTAssertEqual(try cache.messages(in: "cnv_main_marcel"), [])
        XCTAssertEqual(try cache.tasks().count, 0)
        XCTAssertNil(cache.lastEventId)
    }
}
