import AvatarKit
import HTTPTypes
import MarcelKit
import MarcelLook
import OpenAPIRuntime
import SwiftUI
import XCTest

@testable import MarcelChat

/// S-19.3 (B-44): the app plays each agent's own avatar. The UI test is one layer down, like S-13.3's: an
/// `agent.updated` frame goes through a real in-memory cache into `ChatModel.receive`, as `LiveFeed` wires it, and the
/// header and the thinking row are drawn before and after.
@MainActor
final class OwnAvatarTests: XCTestCase {
    /// The Pixel Zoo fox AvatarKit's tests use (`ios/AvatarKit/Tests/AvatarKitTests/Fixtures`).
    nonisolated static func fox(etag: String = "lk-fox-tangerine-cheerful") throws -> AvatarSheets.Download {
        let fixtures = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
            .deletingLastPathComponent().deletingLastPathComponent()
            .appending(path: "AvatarKit/Tests/AvatarKitTests/Fixtures")
        let name = "fox-tangerine-cheerful"
        return AvatarSheets.Download(json: try Data(contentsOf: fixtures.appending(path: "\(name).json")),
                                     png: try Data(contentsOf: fixtures.appending(path: "\(name).png")), etag: etag)
    }

    static func agentUpdated(_ id: String, name: String, animal: String, etag: String?, frame: Int,
                             color: String = "tangerine") -> EventFrame {
        var data: [String: Any] = [
            "agent": [
                "id": id, "name": name, "look": ["animal": animal, "color": color, "personality": "cheerful"],
                "animal": animal, "main_conversation_id": "cnv_\(id)", "state": ["kind": "idle"],
                "model_brain": "opus", "model_worker": "sonnet",
            ] as [String: Any],
        ]
        if let etag { data["avatar_etag"] = etag }
        return Fx.frame("agent.updated", data, id: frame)
    }

    /// The pixels of `view` as the snapshot tests draw it.
    private func pixels<V: View>(_ view: V, avatars: AvatarSheets?) throws -> Data {
        let image = try XCTUnwrap(Snapshot.render(view.environment(\.chatAvatars, avatars), variant: .light))
        return try XCTUnwrap(image.dataProvider?.data as Data?)
    }

    private func header(_ model: ChatModel, state: AvatarState = .thinking) -> ChatHeaderState {
        ChatHeaderState(avatar: state, name: model.agentName, agentId: model.agentId)
    }

    func testANewLookFromTheHubChangesTheHeaderAndTheThinkingRow() async throws {
        let hub = FakeService()
        let model = ChatModel(service: hub)
        await model.start()
        let cache = MarcelCache(container: try MarcelCache.makeContainer(inMemory: true))
        let token = cache.observeFrames { model.receive($0) }
        defer { cache.stopObservingFrames(token) }
        let headerView = AvatarHeader(state: header(model), collapsed: false)
        let thinking = ThinkingRow(state: .thinking, name: model.agentName ?? "Marcel", agentId: model.agentId)
        let giraffeHeader = try pixels(headerView, avatars: nil)
        let giraffeThinking = try pixels(thinking, avatars: nil)
        XCTAssertEqual(try pixels(headerView, avatars: model.avatars), giraffeHeader, "no sheet yet: the giraffe")

        hub.avatarDownload = try Self.fox()
        try cache.apply(Self.agentUpdated("agt_marcel", name: "Marcel", animal: "fox",
                                          etag: "lk-fox-tangerine-cheerful", frame: 1))
        for _ in 0..<100 where model.avatars.sheet(for: "agt_marcel").name != "fox" {
            try await Task.sleep(for: .milliseconds(20))
        }
        XCTAssertEqual(model.avatars.sheet(for: "agt_marcel").name, "fox", "agent.updated fetched the new sheet")
        XCTAssertNotEqual(try pixels(headerView, avatars: model.avatars), giraffeHeader, "the header plays the fox")
        XCTAssertNotEqual(try pixels(thinking, avatars: model.avatars), giraffeThinking, "so does the thinking row")

        // The same ETag again (a rename, say) fetches nothing.
        let asks = hub.avatarAsks.count
        try cache.apply(Self.agentUpdated("agt_marcel", name: "Marcel", animal: "fox",
                                          etag: "lk-fox-tangerine-cheerful", frame: 2))
        try await Task.sleep(for: .milliseconds(100))
        XCTAssertEqual(hub.avatarAsks.count, asks)
    }

    func testOfflineStartShowsTheBundledGiraffe() async throws {
        let hub = FakeService()
        hub.failing = true
        hub.avatarFails = true
        let model = ChatModel(service: hub, conversationId: Fx.conversation)
        await model.start()
        await model.avatars.refresh(agentId: "agt_marcel")
        XCTAssertTrue(model.avatars.sheet(for: "agt_marcel") === SpriteSheet.giraffe)
        XCTAssertTrue(model.avatars.sheet(for: model.agentId) === SpriteSheet.giraffe)
        let view = AvatarHeader(state: ChatHeaderState(avatar: .idle, agentId: "agt_marcel"), collapsed: false)
        XCTAssertEqual(try pixels(view, avatars: model.avatars), try pixels(view, avatars: nil))
    }

    func testTheHeaderAndThinkingRowWithTheirOwnAvatar() async throws {
        let sheets = AvatarSheets(directory: nil) { _, _ in try Self.fox() }
        await sheets.refresh(agentId: "agt_pip")
        Snapshot.assert(
            VStack(spacing: 0) {
                AvatarHeader(state: ChatHeaderState(avatar: .working(1), running: 1, name: "Pip", agentId: "agt_pip"),
                             collapsed: false)
                ThinkingRow(state: .working(1), name: "Pip", agentId: "agt_pip")
                    .padding(16)
            }
            .environment(\.chatAvatars, sheets),
            named: "Own-avatar", variants: [.light, .dark], padding: 0)
    }

    func testAnOfflineStartPlaysTheRememberedAgentsKeptAvatar() async throws {
        let folder = FileManager.default.temporaryDirectory.appending(path: "avatars-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: folder) }
        let online = FakeService()
        online.avatarDownload = try Self.fox()
        var remembered: String?
        let first = ChatModel(service: online, avatarDirectory: folder, rememberAgent: { remembered = $0 })
        await first.start()
        for _ in 0..<100 where first.avatars.sheets["agt_marcel"] == nil { try await Task.sleep(for: .milliseconds(20)) }
        XCTAssertEqual(remembered, "agt_marcel", "the agent is remembered for next launch")

        let offline = FakeService()
        offline.failing = true
        offline.avatarFails = true
        let next = ChatModel(service: offline, conversationId: Fx.conversation, avatarDirectory: folder,
                             agentId: remembered)
        await next.start()
        XCTAssertEqual(next.avatars.sheet(for: next.agentId).name, "fox", "offline, the kept sheet plays at once")
    }

    func testEveryHelperWithASpeakerLineGetsItsOwnAvatar() async throws {
        let hub = FakeService()
        hub.avatarDownload = try Self.fox()
        let model = ChatModel(service: hub, conversationId: Fx.conversation)
        await model.loadAvatars(for: ["agt_emma"])
        XCTAssertEqual(model.avatars.sheet(for: "agt_emma").name, "fox", "fetched without a look change")
        let asks = hub.avatarAsks.count
        await model.loadAvatars(for: ["agt_emma"])
        XCTAssertEqual(hub.avatarAsks.count, asks, "once the phone has it, scrolling asks again for nothing")
    }

    // MARK: The hub's answer (GET /agents/{id}/avatar)

    /// A hub that answers the avatar by `Accept`, with an ETag per body, and remembers what it was sent.
    final class AvatarHub: ClientTransport, @unchecked Sendable {
        var status = 200
        var etags: [(json: String, png: String)] = [("lk-1", "lk-1")]
        var sent: [(accept: String, ifNoneMatch: String?)] = []
        let fox: AvatarSheets.Download

        init(fox: AvatarSheets.Download) { self.fox = fox }

        func send(_ request: HTTPRequest, body: HTTPBody?, baseURL: URL, operationID: String) async throws -> (
            HTTPResponse, HTTPBody?
        ) {
            let accept = request.headerFields[.accept] ?? ""
            let ifNoneMatch = request.headerFields[.ifNoneMatch]
            sent.append((accept, ifNoneMatch))
            let pair = etags[min((sent.count - 1) / 2, etags.count - 1)]
            let png = accept.contains("image/png")
            var fields = HTTPFields()
            fields[.eTag] = png ? pair.png : pair.json
            if status == 200, ifNoneMatch == fields[.eTag] {
                return (HTTPResponse(status: .notModified, headerFields: fields), nil)
            }
            fields[.contentType] = status == 200 && png ? "image/png" : "application/json"
            let answer = status != 200 ? Data(#"{"code":"x","message":"No."}"#.utf8) : png ? fox.png : fox.json
            return (HTTPResponse(status: .init(code: status), headerFields: fields), HTTPBody(answer))
        }
    }

    private func live(_ hub: AvatarHub) -> LiveChatService {
        LiveChatService(client: HubClient.make(serverURL: URL(string: "http://hub.test/api")!, transport: hub))
    }

    func testTheLiveServiceFetchesTheJSONAndThePNGOfOneVersion() async throws {
        let hub = AvatarHub(fox: try Self.fox())
        let answer = try await live(hub).avatar(agentId: "agt_pip", ifNoneMatch: nil)
        let download = try XCTUnwrap(answer)
        XCTAssertEqual(try SpriteSheet(json: download.json, png: download.png).name, "fox")
        XCTAssertEqual(download.etag, "lk-1")
        XCTAssertEqual(hub.sent.map(\.accept).map { $0.contains("image/png") }, [false, true], "JSON, then the PNG")

        let unchanged = try await live(hub).avatar(agentId: "agt_pip", ifNoneMatch: "lk-1")
        XCTAssertNil(unchanged, "304: nothing changed")
        XCTAssertEqual(hub.sent.last?.ifNoneMatch, "lk-1")
    }

    func testALookThatChangesMidFetchIsAskedAgain() async throws {
        let hub = AvatarHub(fox: try Self.fox())
        hub.etags = [("lk-1", "lk-2"), ("lk-2", "lk-2")]
        let answer = try await live(hub).avatar(agentId: "agt_pip", ifNoneMatch: nil)
        let download = try XCTUnwrap(answer)
        XCTAssertEqual(download.etag, "lk-2", "the timing and the image come from the same look")
        XCTAssertEqual(hub.sent.count, 4)

        let flapping = AvatarHub(fox: try Self.fox())
        flapping.etags = [("lk-1", "lk-2"), ("lk-2", "lk-3")]
        do {
            _ = try await live(flapping).avatar(agentId: "agt_pip", ifNoneMatch: nil)
            XCTFail("a sheet of two looks was kept")
        } catch {}
    }

    func testAnUnpairedPhoneIsToldSo() async throws {
        let hub = AvatarHub(fox: try Self.fox())
        hub.status = 401
        do {
            _ = try await live(hub).avatar(agentId: "agt_pip", ifNoneMatch: nil)
            XCTFail("401 answered a sheet")
        } catch let error as ChatError {
            XCTAssertEqual(error.message, ChatError.unpaired.message)
        }
    }

    func testYourBubblesTakeANewLooksColourAtOnce() async throws {
        let model = ChatModel(service: FakeService())
        await model.start()
        model.receive(Self.agentUpdated("agt_marcel", name: "Marcel", animal: "fox", etag: nil, frame: 1, color: "teal"))
        XCTAssertEqual(model.bubbleColor,
                       LookColors.bubble(KnownLook(animal: .fox, color: .teal, personality: .cheerful)))
        model.receive(Self.agentUpdated("agt_emma", name: "Emma", animal: "frog", etag: nil, frame: 2, color: "lime"))
        XCTAssertEqual(model.bubbleColor,
                       LookColors.bubble(KnownLook(animal: .fox, color: .teal, personality: .cheerful)),
                       "another helper's look leaves your bubbles alone")
    }

    // MARK: Several helpers in one chat

    private func feed(emmaSpeaks: Bool) throws -> [FeedItem] {
        let fed = try FedCache()
        try fed.cache.apply(Self.agentUpdated("agt_marcel", name: "Marcel", animal: "giraffe", etag: nil, frame: 900))
        try fed.cache.apply(Self.agentUpdated("agt_emma", name: "Emma", animal: "fox", etag: nil, frame: 901))
        try fed.send("task.updated", Fx.task("t1", title: "Heat pumps", state: "working"))
        try fed.send("task.updated", Fx.task("t2", title: "Swim lessons", state: "working",
                                             extra: emmaSpeaks ? ["agent_id": "agt_emma"] : [:]))
        try fed.send("message.created", Fx.message("u1", at: 1, role: "user", text: "Compare the heat-pump offers"))
        try fed.send("message.created", Fx.milestone("m1", at: 2, task: "t1", title: "Heat pumps", event: "started"))
        try fed.send("message.created", Fx.message("r1", at: 3, text: "Reading three offers."))
        try fed.send("message.created", Fx.milestone("m2", at: 4, task: "t2", title: "Swim lessons", event: "started"))
        try fed.send("message.created", Fx.milestone("r2", at: 5, task: "t2", title: "Swim lessons", event: "done",
                                                     summary: "Tuesday 17:00 has room."))
        try fed.send("message.created", Fx.message("r3", at: 6, text: "Daikin is cheapest."))
        let input = try FeedInput.read(from: fed.cache.container.mainContext, conversationId: Fx.conversation,
                                       limit: 50, agentId: "agt_marcel")
        return ChatFeed.items(input)
    }

    func testASpeakerLineOnlyWhereTheSpeakerChanges() throws {
        let speakers = Dictionary(uniqueKeysWithValues: try feed(emmaSpeaks: true).map { ($0.id, $0.speaker?.name) })
        XCTAssertEqual(speakers["m1"] ?? nil, "Marcel", "the first helper to speak")
        XCTAssertEqual(speakers["r1"] ?? nil, nil, "the same helper again: no line")
        XCTAssertEqual(speakers["m2"] ?? nil, "Emma", "Emma's quest")
        XCTAssertEqual(speakers["r2"] ?? nil, nil, "Emma's quest says more: no line")
        XCTAssertEqual(speakers["r3"] ?? nil, "Marcel", "a reply without a quest is the chat's own helper")
        XCTAssertEqual(speakers["u1"] ?? nil, nil, "your messages never get one")
    }

    func testWithOneHelperNoSpeakerLineEverShows() throws {
        XCTAssertTrue(try feed(emmaSpeaks: false).allSatisfy { $0.speaker == nil })
    }

    func testTwoHelpersInOneFeedLook() async throws {
        let sheets = AvatarSheets(directory: nil) { id, _ in id == "agt_emma" ? try Self.fox() : nil }
        await sheets.refresh(agentId: "agt_emma")
        Snapshot.assert(
            FeedStack(items: try feed(emmaSpeaks: true).filter { if case .time = $0 { false } else { true } },
                      actions: ChatActions())
                .environment(\.chatAvatars, sheets),
            named: "Speakers", variants: [.light, .dark])
    }
}
