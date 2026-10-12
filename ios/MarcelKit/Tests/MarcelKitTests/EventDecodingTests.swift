import Foundation
import XCTest

@testable import MarcelKit

enum EventFrameFixture {
    static func message(id: String, text: String) throws -> Message {
        let json = #"{"id":"\#(id)","conversation_id":"cnv_main_marcel","role":"agent","kind":"text","text":"\#(text)","created_at":"2026-10-04T18:05:00Z"}"#
        return try HubDates.decoder().decode(Message.self, from: Data(json.utf8))
    }
}

/// The event types are written by hand, so every example in contracts/events.schema.json must decode.
final class EventDecodingTests: XCTestCase {
    /// The repo's contracts folder. Tests run on this Mac (the simulator shares its disk), so it is readable.
    static let contracts = URL(fileURLWithPath: #filePath)
        .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
        .deletingLastPathComponent().deletingLastPathComponent().appending(path: "contracts")

    func testEveryContractExampleDecodes() throws {
        let data = try Data(contentsOf: Self.contracts.appending(path: "events.schema.json"))
        let schema = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
        let defs = try XCTUnwrap(schema["$defs"] as? [String: Any])
        var decoded: [String] = []
        for (name, def) in defs.sorted(by: { $0.key < $1.key }) {
            guard let examples = (def as? [String: Any])?["examples"] as? [Any] else { continue }
            for example in examples {
                let bytes = try JSONSerialization.data(withJSONObject: example)
                let frame = try EventFrame.decode(bytes)
                if case .unknown(let type) = frame.event { XCTFail("\(name): \(type) decoded as unknown") }
                decoded.append(name)
            }
        }
        XCTAssertEqual(
            Set(decoded),
            [
                "Hello", "ResyncRequired", "MessageCreated", "ConversationCreated", "TaskCreated", "TaskUpdated",
                "TaskEventAppended", "ApprovalCreated", "ApprovalResolved", "ArtifactCreated", "AgentStateChanged",
                "UsageUpdated", "ArtifactUpdated", "ProjectCreated", "ProjectUpdated", "TaskMoved", "BrainRollover",
            ])
    }

    func testHelloAndResyncAreControlFrames() throws {
        let hello = try EventFrame.decode(Data(Frames.hello(last: 5, replayed: 2).utf8))
        guard case .hello(5, 2) = hello.event else { return XCTFail("\(hello.event)") }
        XCTAssertTrue(hello.event.isControl)
        let resync = try EventFrame.decode(Data(Frames.resync(last: 9).utf8))
        guard case .resyncRequired(9) = resync.event else { return XCTFail("\(resync.event)") }
    }

    func testAnUnknownTypeStillCarriesItsId() throws {
        let frame = try EventFrame.decode(Data(#"{"id":12,"type":"something.new","at":"2026-10-04T18:05:00Z","data":{}}"#.utf8))
        XCTAssertEqual(frame.id, 12)
        guard case .unknown("something.new") = frame.event else { return XCTFail("\(frame.event)") }
        XCTAssertFalse(frame.event.isControl)
    }

    func testDatesWithAndWithoutFractionsDecode() {
        XCTAssertNotNil(HubDates.parse("2026-10-04T18:05:00Z"))
        XCTAssertNotNil(HubDates.parse("2026-10-04T18:05:00.123456Z"))
        XCTAssertNotNil(HubDates.parse("2026-10-04T20:05:00+02:00"))
        XCTAssertNil(HubDates.parse("yesterday"))
    }
}
