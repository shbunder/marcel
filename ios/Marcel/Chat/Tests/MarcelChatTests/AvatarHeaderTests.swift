import AvatarKit
import MarcelKit
import SwiftUI
import XCTest

@testable import MarcelChat

/// S-13.3's "UI test: state changes from the mock hub show up in the header", one layer down: XCUITest cannot run on
/// this Mac's Xcode (see ios/README.md), so the header's model is fed `agent.state` frames through a real in-memory
/// cache, wired the way `LiveFeed` wires it (`liveEvents.observeFrames` → `ChatModel.receive`), and the header
/// state the screen draws is read back. The same sequence against the real mock hub is
/// `ChatMockHubTests.testAgentStateFromTheMockHubShowsInTheHeader` (not part of the gate).
@MainActor
final class AvatarHeaderStateTests: XCTestCase {
    private func agentState(_ kind: String, tasks: Int? = nil, agent: String = "agt_marcel", id: Int) -> EventFrame {
        var state: [String: Any] = ["kind": kind]
        if let tasks { state["active_tasks"] = tasks }
        return Fx.frame("agent.state", ["agent_id": agent, "state": state], id: id)
    }

    func testStateChangesFromTheHubShowInTheHeader() async throws {
        let hub = FakeService()
        let model = ChatModel(service: hub)
        await model.start()
        let cache = MarcelCache(container: try MarcelCache.makeContainer(inMemory: true))
        let token = cache.observeFrames { model.receive($0) }
        defer { cache.stopObservingFrames(token) }
        func header() -> ChatHeaderState {
            ChatHeaderState(tasks: [HubTask](), name: model.agentName, live: model.agentState)
        }
        XCTAssertEqual(header().avatar, .idle, "before the hub says anything")

        let steps: [(EventFrame, AvatarState, String)] = [
            (agentState("thinking", id: 1), .thinking, "Marcel is thinking. Nothing running"),
            (agentState("working", tasks: 2, id: 2), .working(2), "Marcel is working on 2 tasks. Nothing running"),
            (agentState("needs_you", id: 3), .needsYou, "Marcel needs you. Nothing running"),
            (agentState("done", id: 4), .done, "Marcel is done. Nothing running"),
            (agentState("idle", id: 5), .idle, "Marcel is free. Nothing running"),
        ]
        for (frame, avatar, spoken) in steps {
            try cache.apply(frame)
            XCTAssertEqual(header().avatar, avatar)
            XCTAssertEqual(AvatarHeader(state: header(), collapsed: false).state.accessibilityText, spoken)
        }

        // Another agent's state is not this header's.
        try cache.apply(agentState("thinking", agent: "agt_other", id: 6))
        XCTAssertEqual(header().avatar, .idle)
    }

    func testVoiceOverUsesTheAgentsName() async {
        let hub = FakeService()
        hub.agentName = "Gigi"
        hub.agentState = .thinking
        let model = ChatModel(service: hub)
        await model.start()
        let header = ChatHeaderState(tasks: [HubTask](), name: model.agentName, live: model.agentState)
        XCTAssertEqual(header.accessibilityText, "Gigi is thinking. Nothing running", "from GET /me, with its name")
        XCTAssertEqual(AvatarState.working(1).accessibilityDescription(name: "Gigi"), "Gigi is working on 1 task")
    }

    func testOfflineTheHeaderFallsBackToTheCachedTasks() async throws {
        let hub = FakeService()
        hub.agentState = .thinking
        let model = ChatModel(service: hub)
        await model.start()
        let tasks: [HubTask] = [Fx.decode(Fx.task("tsk_1", title: "Energy", state: "needs_you"))]
        XCTAssertEqual(ChatHeaderState(tasks: tasks, live: model.agentState).avatar, .thinking, "the hub's word")
        let offline = ChatHeaderState(tasks: tasks, offline: "Offline · showing what's saved", live: model.agentState)
        XCTAssertEqual(offline.avatar, .needsYou, "a stale 'thinking' is not shown while the stream is down")
    }

    func testAResyncAsksTheHubAgain() async throws {
        let hub = FakeService()
        let model = ChatModel(service: hub)
        await model.start()
        XCTAssertEqual(hub.meFetches, 1)
        hub.agentState = .working(3)
        model.receive(Fx.frame("resync.required", ["last_event_id": 9], id: 0))
        for _ in 0..<50 where model.agentState != .working(3) { try await Task.sleep(for: .milliseconds(20)) }
        XCTAssertEqual(hub.meFetches, 2)
        XCTAssertEqual(model.agentState, .working(3), "missed agent.state events are read again from GET /me")
    }

    func testTheHeaderShrinksSmoothlyAndTheSpriteInWholeZooms() {
        XCTAssertEqual(AvatarHeader.box(collapse: 0), 120)
        XCTAssertEqual(AvatarHeader.box(collapse: 1), 44)
        XCTAssertEqual(AvatarHeader.box(collapse: 0.5), 82)
        XCTAssertEqual(AvatarHeader.box(collapse: 2), 44, "clamped")
        // On a 3× phone the 48 × 54 giraffe steps 6×, 5×, 4×, 3×, 2× as the box shrinks: never a fraction.
        let zooms = stride(from: 0.0, through: 1.0, by: 0.01).map {
            AvatarSize.zoom(box: AvatarHeader.box(collapse: $0), frameWidth: 48, frameHeight: 54, displayScale: 3)
        }
        XCTAssertEqual(Array(NSOrderedSet(array: zooms)) as? [Int], [6, 5, 4, 3, 2])
        XCTAssertEqual(zooms, zooms.sorted(by: >), "it only ever gets smaller while collapsing")
        // The header animates `collapse` (Animatable), so SwiftUI hands it every in-between value.
        var header = AvatarHeader(state: ChatHeaderState(), collapsed: false)
        header.animatableData = 0.25
        XCTAssertEqual(header.collapse, 0.25)
    }
}

/// The header in light and dark: expanded (120 pt) and collapsed (44 pt) in every state, one moment half-way through
/// the collapse, and the 32-pt giraffe beside the agent's dialogue box. Drawn at 3× (a real iPhone) so the snapshots
/// show the pixels the phone shows. Re-record with `make -C ios chat-record` after a deliberate change.
@MainActor
final class AvatarHeaderSnapshotTests: XCTestCase {
    private let twoModes: [Snapshot.Variant] = [.light, .dark]

    static let states: [(name: String, header: ChatHeaderState)] = [
        ("idle", ChatHeaderState(avatar: .idle)),
        ("thinking", ChatHeaderState(avatar: .thinking)),
        ("working1", ChatHeaderState(avatar: .working(1), running: 1)),
        ("working2", ChatHeaderState(avatar: .working(2), running: 2)),
        ("working3", ChatHeaderState(avatar: .working(3), running: 3)),
        ("needs_you", ChatHeaderState(avatar: .needsYou, running: 1, needsYou: 1)),
        ("done", ChatHeaderState(avatar: .done)),
    ]

    func testExpandedInEveryState() {
        for (name, header) in Self.states {
            Snapshot.assert(AvatarHeader(state: header, collapsed: false), named: "Header-expanded-\(name)",
                            variants: twoModes, padding: 0, scale: 3)
        }
    }

    func testCollapsedInEveryState() {
        for (name, header) in Self.states {
            Snapshot.assert(AvatarHeader(state: header, collapsed: true), named: "Header-collapsed-\(name)",
                            variants: twoModes, padding: 0, scale: 3)
        }
    }

    func testHalfWayThroughTheCollapse() {
        Snapshot.assert(AvatarHeader(state: ChatHeaderState(avatar: .thinking), collapse: 0.5),
                        named: "Header-halfway-thinking", variants: twoModes, padding: 0, scale: 3)
    }

    func testDialogueBoxAvatar() {
        Snapshot.assert(
            MarcelSays(text: "Good morning. Two things wait for you.") { EmptyView() },
            named: "Dialogue-avatar", variants: twoModes, scale: 3)
    }
}
