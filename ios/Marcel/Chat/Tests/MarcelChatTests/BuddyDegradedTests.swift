import Foundation
import HTTPTypes
import MarcelKit
import OpenAPIRuntime
import XCTest

@testable import MarcelChat

/// S-22.12's degraded paths (story-verifier): which buddy the chat opens when the hub can't say, a buddy that is gone,
/// a switch the hub refuses, a switch whose chat can't be loaded, and a slow start that must not undo a switch.
@MainActor
final class BuddyDegradedTests: XCTestCase {
    /// A hub that answers by "METHOD /path", through the real generated client; anything else is the hub's 404.
    final class RoutedHub: ClientTransport, @unchecked Sendable {
        var routes: [String: (Int, String)]
        init(_ routes: [String: (Int, String)]) { self.routes = routes }

        func send(_ request: HTTPRequest, body: HTTPBody?, baseURL: URL, operationID: String) async throws -> (
            HTTPResponse, HTTPBody?
        ) {
            let path = (request.path ?? "").split(separator: "?").first.map(String.init) ?? ""
            let key = "\(request.method.rawValue) \(path.replacingOccurrences(of: "/api", with: ""))"
            let (status, text) = routes[key] ?? (404, #"{"code":"not_found","message":"Not found."}"#)
            var fields = HTTPFields()
            fields[.contentType] = "application/json"
            return (HTTPResponse(status: .init(code: status), headerFields: fields), HTTPBody(text))
        }
    }

    static func agent(_ id: String, _ name: String) -> String {
        #"{"id":"\#(id)","name":"\#(name)","animal":"giraffe","main_conversation_id":"cnv_\#(id)","state":{"kind":"idle"},"model_brain":"opus","model_worker":"sonnet"}"#
    }

    static func me(_ agents: [String]) -> String {
        #"{"user_id":"usr_1","name":"Shaun","device_id":"dev_me","agents":[\#(agents.joined(separator: ","))],"push":{"apns":false,"ntfy":false}}"#
    }

    static let devicesOnPip =
        #"[{"id":"dev_me","name":"Phone","created_at":"2026-10-15T09:00:00Z","agent_id":"agt_pip","push":"none","current":true}]"#

    private func buddy() -> CurrentBuddy {
        let name = "BuddyDegradedTests-\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: name)!
        addTeardownBlock { defaults.removePersistentDomain(forName: name) }
        return CurrentBuddy(defaults: defaults)
    }

    private func service(_ hub: RoutedHub, _ buddy: CurrentBuddy) -> LiveChatService {
        LiveChatService(client: HubClient.make(serverURL: URL(string: "http://hub.test/api")!, transport: hub), buddy: buddy)
    }

    func testTheHubSaysWhichBuddyThisPhoneShows() async throws {
        let hub = RoutedHub(["GET /me": (200, Self.me([Self.agent("agt_marcel", "Marcel"), Self.agent("agt_pip", "Pip")])),
                             "GET /devices": (200, Self.devicesOnPip)])
        let shown = buddy()
        let agent = try await service(hub, shown).mainAgent()
        XCTAssertEqual(agent.id, "agt_pip")
        XCTAssertEqual(agent.conversationId, "cnv_agt_pip")
        XCTAssertEqual(agent.deviceId, "dev_me")
        XCTAssertEqual(shown.agentId, "agt_pip")
    }

    func testWithoutDevicesTheRememberedBuddyStays() async throws {
        // A hub without `GET /devices` (or a hiccup): the buddy remembered on this phone.
        let hub = RoutedHub(["GET /me": (200, Self.me([Self.agent("agt_marcel", "Marcel"), Self.agent("agt_pip", "Pip")]))])
        let shown = buddy()
        shown.show("agt_pip")
        let agent = try await service(hub, shown).mainAgent()
        XCTAssertEqual(agent.id, "agt_pip")

        // Nothing remembered: the first live buddy.
        let fresh = buddy()
        let first = try await service(hub, fresh).mainAgent()
        XCTAssertEqual(first.id, "agt_marcel")
        XCTAssertEqual(fresh.agentId, "agt_marcel")
    }

    func testABuddyThatIsGoneGivesWayToTheFirstLiveOne() async throws {
        // Pip was archived on another phone: it left `/me`'s agents.
        let hub = RoutedHub(["GET /me": (200, Self.me([Self.agent("agt_marcel", "Marcel")]))])
        let shown = buddy()
        shown.show("agt_pip")
        let agent = try await service(hub, shown).mainAgent()
        XCTAssertEqual(agent.id, "agt_marcel")
        XCTAssertEqual(shown.agentId, "agt_marcel", "every tab follows")
    }

    func testASwitchToAnArchivedBuddyIsRefusedInPlainWords() async throws {
        let hub = RoutedHub(["PATCH /devices/dev_me": (404, #"{"code":"agent_not_found","message":"No."}"#)])
        do {
            try await service(hub, buddy()).show(agentId: "agt_pip", deviceId: "dev_me")
            XCTFail("the switch went through")
        } catch let error as ChatError {
            XCTAssertEqual(error.message, "That buddy is gone. Pick another one.")
        }

        // Through the model, as a push about an archived buddy would go: it stays put and says so.
        let fake = FakeService()
        fake.archived = ["agt_pip"]
        let model = ChatModel(service: fake, buddy: buddy())
        await model.start()
        await model.switchTo("agt_pip")
        XCTAssertEqual(model.agentId, "agt_marcel")
        XCTAssertEqual(model.problem, "That buddy is gone. Pick another one.")
    }

    func testOfflineFromTheStartTheSwitchSaysTheHubIsOutOfReach() async {
        let fake = FakeService(failing: true)
        let model = ChatModel(service: fake, buddy: buddy())
        await model.switchTo("agt_pip")
        XCTAssertEqual(model.problem, "Can't switch while the hub is out of reach.")
        XCTAssertNil(model.agentId)
    }

    func testASwitchWhoseChatCannotBeLoadedNeverShowsTheOldChat() async {
        let fake = FakeService()
        fake.buddiesOnHub = [BuddyChoice(id: "agt_pip", name: "Pip")]
        let shown = buddy()
        let model = ChatModel(service: fake, buddy: shown)
        await model.start()
        await model.loadBuddies()
        // The hub takes the switch, then can't be reached for Pip's chat.
        fake.failingAfterSwitch = true
        await model.switchTo("agt_pip")
        XCTAssertEqual(shown.agentId, "agt_pip")
        XCTAssertEqual(model.agentId, "agt_pip")
        XCTAssertEqual(model.agentName, "Pip")
        XCTAssertNil(model.conversationId, "not Marcel's chat under Pip's name")
        XCTAssertNotNil(model.problem)
    }

    func testASlowStartDoesNotUndoASwitch() async throws {
        let fake = FakeService()
        fake.buddiesOnHub = [BuddyChoice(id: "agt_pip", name: "Pip")]
        let shown = buddy()
        let model = ChatModel(service: fake, buddy: shown)
        await model.start()
        // A start whose answer (Marcel) is slow, then a switch to Pip that finishes first.
        fake.holdMe = true
        let slow = Task { await model.start() }
        try await Task.sleep(for: .milliseconds(50))
        fake.holdMe = false
        await model.switchTo("agt_pip")
        fake.releaseMe()
        await slow.value
        XCTAssertEqual(model.agentId, "agt_pip")
        XCTAssertEqual(shown.agentId, "agt_pip")
    }
}
