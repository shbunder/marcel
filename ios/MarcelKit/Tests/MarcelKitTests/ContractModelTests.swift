import Foundation
import XCTest

@testable import MarcelKit

/// app API 0.7.0's new models decode from the contract's own examples (contracts/app-api.yaml, copied here because
/// the tests cannot read YAML) and survive the cache's encode → decode round trip.
final class ContractModelTests: XCTestCase {
    private func decode<T: Decodable>(_: T.Type, _ json: String) throws -> T {
        try HubDates.decoder().decode(T.self, from: Data(json.utf8))
    }

    private func roundTrip<T: Codable & Equatable>(_ value: T) throws {
        let again = try HubDates.decoder().decode(T.self, from: try HubDates.encoder().encode(value))
        XCTAssertEqual(again, value)
    }

    /// `Device` (app API 0.8.0): every phone shows one buddy.
    func testDeviceNamesItsBuddy() throws {
        let device = try decode(Device.self, #"{"id":"dev_01J9ZQ4K2B","name":"Shaun's iPhone","created_at":"2026-10-04T18:02:11Z","agent_id":"agt_01JA3E2","push":"relay","current":true}"#)
        XCTAssertEqual(device.agentId, "agt_01JA3E2")
        XCTAssertEqual(device.current, true)
        try roundTrip(device)
    }

    /// `Agent` with a look (0.7.0) and without (the app-api.yaml example: an agent from before 0.7.0).
    func testAgentLookDecodes() throws {
        let agent = try decode(Agent.self, #"{"id":"agt_marcel","name":"Marcel","look":{"animal":"giraffe","color":"rose","personality":"professional"},"animal":"giraffe","onboarded_at":"2026-10-13T17:20:00Z","main_conversation_id":"cnv_main_marcel","state":{"kind":"idle"},"model_brain":"opus","model_worker":"sonnet"}"#)
        XCTAssertEqual(agent.drawnLook, KnownLook(animal: .giraffe, color: .rose, personality: .professional))
        try roundTrip(agent)
        let old = try decode(Agent.self, #"{"id":"agt_marcel","name":"Marcel","animal":"giraffe","palette":"marcel","seed":"Marcel","main_conversation_id":"cnv_main_marcel","state":{"kind":"idle"},"model_brain":"opus","model_worker":"sonnet"}"#)
        XCTAssertNil(old.look)
        XCTAssertNil(old.onboardedAt)
    }

    /// app API 0.7.1: a look with a value this build does not know (a newer zoo) still decodes, with the rest of the
    /// answer, and is drawn as the default avatar.
    func testAnUnknownLookValueMeansTheDefaultAvatar() throws {
        let agent = try decode(Agent.self, #"{"id":"agt_1","name":"Pip","look":{"animal":"axolotl","color":"rose","personality":"cheerful"},"animal":"axolotl","main_conversation_id":"cnv_1","state":{"kind":"idle"},"model_brain":"opus","model_worker":"sonnet"}"#)
        XCTAssertEqual(agent.name, "Pip")
        XCTAssertEqual(agent.look?.animal, "axolotl")
        XCTAssertNil(agent.drawnLook, "unknown animal: the default avatar")
        for (animal, color, personality) in [("fox", "ultraviolet", "cozy"), ("fox", "sky", "mysterious")] {
            XCTAssertNil(Look(animal: animal, color: color, personality: personality).known)
        }
        // A /me answer with such an agent reads in full: nothing else is lost.
        let me = try decode(Me.self, #"{"user_id":"usr_1","name":"Emma","device_id":"dev_1","agents":[{"id":"agt_1","name":"Pip","look":{"animal":"axolotl","color":"rose","personality":"cheerful"},"animal":"axolotl","main_conversation_id":"cnv_1","state":{"kind":"idle"},"model_brain":"opus","model_worker":"sonnet"}],"push":{"apns":false,"ntfy":false}}"#)
        XCTAssertEqual(me.agents.map(\.name), ["Pip"])
        XCTAssertNil(me.agents.first?.drawnLook)
    }

    /// Every Pixel Zoo choice the contract lists is a case the app knows.
    func testLookEnumsCoverThePixelZoo() {
        XCTAssertEqual(LookAnimal.allCases.count, 11)
        XCTAssertEqual(LookColor.allCases.count, 17)  // "natural" + 16 colours
        XCTAssertEqual(LookPersonality.allCases.count, 9)
    }

    /// A brain reply that names the question it answers, with a mention.
    func testMessageReplyToAndMentionsDecode() throws {
        let message = try decode(Message.self, #"{"id":"msg_2","conversation_id":"cnv_main_marcel","role":"agent","kind":"text","text":"Done, see the quest.","reply_to_message_id":"msg_1","mentions":[{"kind":"task","id":"tsk_01J9ZQ5","title":"Fix flaky login test"}],"created_at":"2026-10-13T18:00:00Z"}"#)
        XCTAssertEqual(message.replyToMessageId, "msg_1")
        XCTAssertEqual(message.mentions, [Mention(kind: .task, id: "tsk_01J9ZQ5", title: "Fix flaky login test")])
        try roundTrip(message)
        // the app-api.yaml example: neither field
        let plain = try decode(Message.self, #"{"id":"msg_01J9ZQZ8","conversation_id":"cnv_main_marcel","role":"agent","kind":"milestone","milestone":{"task_id":"tsk_01J9ZQ5","task_title":"Fix the flaky login test","event":"started","location":"cloud"},"task_id":"tsk_01J9ZQ5","created_at":"2026-10-04T18:05:02Z"}"#)
        XCTAssertNil(plain.replyToMessageId)
        XCTAssertNil(plain.mentions)
    }

    /// `Mention` example from app-api.yaml, and each kind.
    func testMentionDecodes() throws {
        let mention = try decode(Mention.self, #"{"kind":"task","id":"tsk_01J9ZQ5","title":"Fix flaky login test"}"#)
        XCTAssertEqual(mention.kind, .task)
        XCTAssertEqual(mention.title, "Fix flaky login test")
        XCTAssertEqual(Set(MentionKind.allCases.map(\.rawValue)), ["task", "project", "artifact"])
    }
}
