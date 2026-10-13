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
        try cache.apply(try EventFrame.decode(try Frames.contractExample("ArtifactCreated")))

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

    func testProjectsAreCachedAndUpdatedInPlace() throws {
        let cache = MarcelCache(container: try MarcelCache.makeContainer(inMemory: true))
        try cache.apply(try EventFrame.decode(try Frames.contractExample("ProjectCreated")))
        try cache.apply(try EventFrame.decode(try Frames.contractExample("ProjectUpdated")))

        let projects = try cache.projects()
        XCTAssertEqual(projects.map(\.slug), ["energy"])
        XCTAssertEqual(projects.first?.counts.needsYou, 1)
        XCTAssertEqual(cache.lastEventId, 2105)
    }

    func testAMovedTaskChangesProject() throws {
        let cache = MarcelCache(container: try MarcelCache.makeContainer(inMemory: true))
        try cache.apply(try EventFrame.decode(Data(Frames.task(id: 1, taskId: "tsk_01JA0A9", state: "working").utf8)))
        try cache.apply(try EventFrame.decode(try Frames.contractExample("TaskMoved")))

        XCTAssertEqual(try cache.tasks(inProject: "prj_01JA0A1").map(\.id), ["tsk_01JA0A9"])
        XCTAssertEqual(try cache.tasks(inProject: "prj_inbox"), [])
        XCTAssertEqual(try cache.tasks().first?.projectId, "prj_01JA0A1")
    }

    /// `artifact.moved` (app API 0.5.0): the cached item changes project; one the cache never saw is left alone.
    func testAMovedArtifactChangesProject() throws {
        let cache = MarcelCache(container: try MarcelCache.makeContainer(inMemory: true))
        var created = try XCTUnwrap(
            JSONSerialization.jsonObject(with: try Frames.contractExample("ArtifactCreated")) as? [String: Any])
        var artifact = try XCTUnwrap(created["data"] as? [String: Any])
        artifact["id"] = "art_01JA0B1"
        artifact["project_id"] = "prj_inbox"
        created["data"] = artifact
        try cache.apply(try EventFrame.decode(try JSONSerialization.data(withJSONObject: created)))
        XCTAssertEqual(try cache.artifacts(inProject: "prj_inbox").map(\.id), ["art_01JA0B1"])

        try cache.apply(try EventFrame.decode(try Frames.contractExample("ArtifactMoved")))

        XCTAssertEqual(try cache.artifacts(inProject: "prj_01JA0A1").map(\.id), ["art_01JA0B1"])
        XCTAssertEqual(try cache.artifacts(inProject: "prj_inbox"), [])
        XCTAssertEqual(try cache.artifacts().first?.projectId, "prj_01JA0A1", "the stored item says so too")
        XCTAssertEqual(cache.lastEventId, 2002)
    }

    func testANewArtifactVersionReplacesTheCachedArtifact() throws {
        let cache = MarcelCache(container: try MarcelCache.makeContainer(inMemory: true))
        try cache.apply(try EventFrame.decode(try Frames.contractExample("ArtifactUpdated")))

        let artifact = try XCTUnwrap(cache.artifacts(inProject: "prj_01JA0A1").first)
        XCTAssertEqual(artifact.currentVersion, 3)
        XCTAssertEqual(try cache.artifacts(inProject: "prj_other"), [])
    }

    func testObserversSeeEveryAppliedFrameIncludingOnesTheCacheDoesNotStore() throws {
        let cache = MarcelCache(container: try MarcelCache.makeContainer(inMemory: true))
        var seen: [Int] = []
        let token = cache.observeFrames { seen.append($0.id) }
        try cache.apply(try EventFrame.decode(try Frames.contractExample("TaskEventAppended")))
        try cache.apply(try EventFrame.decode(try Frames.contractExample("ApprovalCreated")))
        XCTAssertEqual(seen, [1294, 1296], "a quest view needs task.event and approval frames, which are not stored")

        cache.stopObservingFrames(token)
        try cache.apply(try EventFrame.decode(try Frames.contractExample("ApprovalResolved")))
        XCTAssertEqual(seen, [1294, 1296], "a stopped observer hears nothing more")
    }

    func testObserversHearAboutAResyncSoTheyCanReRead() async throws {
        let cache = MarcelCache(container: try MarcelCache.makeContainer(inMemory: true))
        var heard: [String] = []
        cache.observeFrames { frame in
            if case .resyncRequired(let last) = frame.event { heard.append("resync \(last)") }
        }
        try await cache.resync(upTo: 1288)
        XCTAssertEqual(heard, ["resync 1288"])
    }

    // MARK: app API 0.7.0

    private static func agentFrame(id: Int, name: String, etag: String?) -> Data {
        let tag = etag.map { #","avatar_etag":"\#($0)""# } ?? ""
        return Data(#"{"id":\#(id),"type":"agent.updated","at":"2026-10-13T17:25:00Z","data":{"agent":{"id":"agt_1","name":"\#(name)","look":{"animal":"fox","color":"sky","personality":"cozy"},"animal":"fox","main_conversation_id":"cnv_1","state":{"kind":"idle"},"model_brain":"opus","model_worker":"sonnet"}\#(tag)}}"#.utf8)
    }

    func testAgentUpdatedIsCachedWithItsAvatarEtag() throws {
        let cache = MarcelCache(container: try MarcelCache.makeContainer(inMemory: true))
        try cache.apply(try EventFrame.decode(Self.agentFrame(id: 1, name: "Pip", etag: "e1")))
        try cache.apply(try EventFrame.decode(Self.agentFrame(id: 2, name: "Pippa", etag: "e2")))

        XCTAssertEqual(try cache.agents().map(\.name), ["Pippa"])
        XCTAssertEqual(try cache.agents().first?.drawnLook?.color, .sky)
        XCTAssertEqual(try cache.avatarEtag(forAgent: "agt_1"), "e2")
        XCTAssertEqual(cache.lastEventId, 2)
    }

    func testAnUpdateWithoutAnEtagKeepsTheLastOne() throws {
        let cache = MarcelCache(container: try MarcelCache.makeContainer(inMemory: true))
        try cache.apply(try EventFrame.decode(Self.agentFrame(id: 1, name: "Pip", etag: "e1")))
        let agent = try XCTUnwrap(try cache.agents().first)
        try cache.upsert(agent)  // what a REST refresh does: /me carries no ETag

        XCTAssertEqual(try cache.avatarEtag(forAgent: "agt_1"), "e1")
        XCTAssertNil(try cache.avatarEtag(forAgent: "agt_unknown"))
        try cache.clear()
        XCTAssertEqual(try cache.agents(), [])
    }

    func testAQuotedReplyFindsItsQuestion() throws {
        let cache = MarcelCache(container: try MarcelCache.makeContainer(inMemory: true))
        let reply = try HubDates.decoder().decode(Message.self, from: Data(#"{"id":"msg_2","conversation_id":"cnv_main_marcel","role":"agent","kind":"text","text":"Yes.","reply_to_message_id":"msg_1","created_at":"2026-10-13T18:00:00Z"}"#.utf8))
        try cache.upsert(try EventFrameFixture.message(id: "msg_1", text: "Is it done?"))
        try cache.upsert(reply)

        XCTAssertEqual(try cache.replies(to: "msg_1").map(\.id), ["msg_2"])
        XCTAssertEqual(try cache.replies(to: "msg_2"), [])
        XCTAssertEqual(try cache.messages(in: "cnv_main_marcel").last?.replyToMessageId, "msg_1")
    }
}
