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
            ChatHeaderState(tasks: [HubTask](), name: model.agentName, live: model.action)
        }
        XCTAssertEqual(header().avatar, .idle, "before the hub says anything")

        let steps: [(EventFrame, AvatarState, String)] = [
            (agentState("thinking", id: 1), .thinking, "Marcel, working on: thinking. Nothing running"),
            (agentState("working", tasks: 2, id: 2), .working(2), "Marcel, working on: 2 tasks. Nothing running"),
            (agentState("needs_you", id: 3), .needsYou, "Marcel, waiting for you. Nothing running"),
            (agentState("done", id: 4), .done, "Marcel, done. Nothing running"),
            (agentState("idle", id: 5), .idle, "Marcel, free. Nothing running"),
        ]
        // Each step is held its two seconds before the next (the clock moves by hand).
        var now: TimeInterval = 1000
        model.clock = { now }
        for (frame, avatar, spoken) in steps {
            now += PoseGate.hold
            try cache.apply(frame)
            XCTAssertEqual(header().avatar, avatar)
            XCTAssertEqual(AvatarHeader(state: header()).state.accessibilityText, spoken)
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
        let header = ChatHeaderState(tasks: [HubTask](), name: model.agentName, live: model.action)
        XCTAssertEqual(header.accessibilityText, "Gigi, working on: thinking. Nothing running", "from GET /me, with its name")
        XCTAssertEqual(AvatarState.working(1).accessibilityDescription(name: "Gigi"), "Gigi is working on 1 task")
    }

    func testOfflineTheHeaderFallsBackToTheCachedTasks() async throws {
        let hub = FakeService()
        hub.agentState = .thinking
        let model = ChatModel(service: hub)
        await model.start()
        let tasks: [HubTask] = [Fx.decode(Fx.task("tsk_1", title: "Energy", state: "needs_you"))]
        XCTAssertEqual(ChatHeaderState(tasks: tasks, live: model.action).avatar, .thinking, "the hub's word")
        let offline = ChatHeaderState(tasks: tasks, offline: "Offline · showing what's saved", live: model.action)
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

    func testTheHeaderShowsTheHelperBigAndCrisp() {
        // The owner's pick over wireframe Q1's 44 pt strip: 96 pt is an exact 9x of the 32-pixel sprite at 3x.
        XCTAssertEqual(AvatarHeader.box, 96)
        XCTAssertEqual(AvatarSize.zoom(box: AvatarHeader.box, frameWidth: 32, frameHeight: 32, displayScale: 3), 9)
    }

    func testTheBubbleSaysWhatMarcelIsDoingAndHidesWhenFree() {
        XCTAssertEqual(MarcelAction.thinking.caption, "Thinking…")
        XCTAssertEqual(MarcelAction.reading.caption, "Reading…")
        XCTAssertEqual(MarcelAction.working(2).caption, "On 2 quests…")
        XCTAssertEqual(MarcelAction.waiting.caption, "Waiting for you")
    }

    private func toolCall(_ tool: String, id: Int) -> EventFrame {
        Fx.frame("task.event", ["task_id": "tsk_1", "seq": id, "type": "tool_call", "at": "2026-10-14T18:00:00Z",
                                "data": ["tool_use_id": "toolu_\(id)", "tool": tool, "summary": "x"]], id: id)
    }

    private func taskError(id: Int) -> EventFrame {
        Fx.frame("task.event", ["task_id": "tsk_1", "seq": id, "type": "error", "at": "2026-10-14T18:00:00Z",
                                "data": ["message": "The NAS did not answer."]], id: id)
    }

    func testTheLastToolSaysWhatMarcelIsWorkingOn() async {
        let model = ChatModel(service: FakeService())
        var now: TimeInterval = 1000
        model.clock = { now }
        model.receive(agentState("working", tasks: 1, id: 1))
        XCTAssertEqual(model.action, .working(1))
        let steps: [(String, MarcelAction)] = [("Grep", .reading), ("Edit", .writing(1)), ("Bash", .working(1)),
                                               ("WebFetch", .reading), ("Write", .writing(1))]
        for (i, (tool, action)) in steps.enumerated() {
            now += PoseGate.hold
            model.receive(toolCall(tool, id: i + 2))
            XCTAssertEqual(model.action, action, tool)
        }
        XCTAssertEqual(ChatHeaderState(tasks: [HubTask](), live: model.action).avatar, .working(1), "writing: laptop")

        // Leaving work forgets the tool: the next working spell starts plain.
        now += PoseGate.hold
        model.receive(agentState("thinking", id: 20))
        now += PoseGate.hold
        model.receive(agentState("working", tasks: 1, id: 21))
        XCTAssertEqual(model.action, .working(1))
    }

    func testAPoseIsHeldTwoSecondsBeforeTheNextShows() async {
        let model = ChatModel(service: FakeService())
        var now: TimeInterval = 1000
        model.clock = { now }
        model.receive(agentState("thinking", id: 1))
        XCTAssertEqual(model.action, .thinking, "the first pose shows at once")
        now += 0.5
        model.receive(agentState("working", tasks: 1, id: 2))
        model.receive(toolCall("Read", id: 3))
        XCTAssertEqual(model.action, .thinking, "too soon: thinking stays")
        now += 1.0
        model.updateAction()
        XCTAssertEqual(model.action, .thinking)
        now += 0.5
        model.updateAction()
        XCTAssertEqual(model.action, .reading, "after two seconds, the newest wish shows")
    }

    func testTheGateWakesByItselfWhenTheHoldIsOver() async throws {
        let model = ChatModel(service: FakeService())
        model.receive(agentState("thinking", id: 1))
        model.receive(agentState("done", id: 2))
        XCTAssertEqual(model.action, .thinking)
        for _ in 0..<150 where model.action != .done { try await Task.sleep(for: .milliseconds(20)) }
        XCTAssertEqual(model.action, .done, "shown without another event")
    }

    func testAProblemShowsForFiveSecondsButWaitingForYouComesFirst() async {
        let model = ChatModel(service: FakeService())
        var now: TimeInterval = 1000
        model.clock = { now }
        model.receive(agentState("working", tasks: 1, id: 1))
        now += PoseGate.hold
        model.receive(taskError(id: 2))
        XCTAssertEqual(model.action, .error)
        XCTAssertEqual(ChatHeaderState(tasks: [HubTask](), live: model.action).accessibilityText,
                       "Marcel, ran into a problem. Nothing running")
        now += MarcelAction.errorFor
        model.updateAction()
        XCTAssertEqual(model.action, .working(1), "the problem has had its five seconds")

        now += PoseGate.hold
        model.receive(taskError(id: 3))
        now += PoseGate.hold
        model.receive(agentState("needs_you", id: 4))
        XCTAssertEqual(model.action, .waiting, "waiting for you outranks a problem")
    }

    func testVoiceOverSaysWhatMarcelIsWorkingOn() {
        XCTAssertEqual(MarcelAction.reading.spoken(name: "Marcel"), "Marcel, working on: reading and searching")
        XCTAssertEqual(MarcelAction.writing(2).spoken(name: "Marcel"), "Marcel, working on: writing")
        XCTAssertEqual(MarcelAction.working(1).spoken(name: "Gigi"), "Gigi, working on: 1 task")
        XCTAssertEqual(MarcelAction.waiting.spoken(name: "Marcel"), "Marcel, waiting for you")
        XCTAssertEqual(MarcelAction.idle.spoken(name: "Marcel"), "Marcel, free")
    }
}

/// The header in light and dark in every pose, the helper at 96 pt with its speech bubble. Drawn at 3× (a real iPhone) so the snapshots
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
        ("reading", ChatHeaderState(action: .reading, running: 1)),
        ("error", ChatHeaderState(action: .error, running: 1)),
    ]

    func testEveryState() {
        for (name, header) in Self.states {
            Snapshot.assert(AvatarHeader(state: header), named: "Header-\(name)", variants: twoModes, padding: 0,
                            scale: 3)
        }
    }
}

/// Idle moods: the timing on a hand-moved clock and a seeded random source.
final class IdleMoodsTests: XCTestCase {
    private func moods(_ personality: LookPersonality? = nil, has: @escaping (String) -> Bool = { _ in true })
        -> IdleMoods {
        IdleMoods(personality: personality, has: has, duration: { _ in 2.24 })
    }

    func testAMoodPlaysAfterARandomGapThenIdleAgain() throws {
        var idle = moods()
        var random = SeededRandom(state: 7)
        let first = idle.update(now: 0, free: true, random: &random)
        XCTAssertNil(first.loop, "plain idle at first")
        let gap = try XCTUnwrap(first.wake)
        XCTAssertTrue(IdleMoods.gap.contains(gap), "the first mood waits 20-60 s, not \(gap)")

        let playing = idle.update(now: gap, free: true, random: &random)
        let loop = try XCTUnwrap(playing.loop)
        XCTAssertTrue(MoodBook.moods(.idle, personality: nil).map(MoodBook.loop).contains(loop), loop)
        XCTAssertEqual(try XCTUnwrap(playing.wake), gap + 2.24, accuracy: 0.001, "one pass of the loop")

        let after = idle.update(now: gap + 2.24, free: true, random: &random)
        XCTAssertNil(after.loop, "back to idle")
        XCTAssertTrue(IdleMoods.gap.contains(try XCTUnwrap(after.wake) - (gap + 2.24)), "then another 20-60 s gap")
    }

    func testTheSameSeedPicksTheSameMoods() {
        func run() -> [String?] {
            var idle = moods()
            var random = SeededRandom(state: 42)
            var now: TimeInterval = 0
            var seen: [String?] = []
            for _ in 0..<12 {
                let step = idle.update(now: now, free: true, random: &random)
                seen.append(step.loop)
                now = step.wake ?? now + 1
            }
            return seen
        }
        XCTAssertEqual(run(), run())
    }

    func testSleepyAfterAQuietSpellUntilSomethingHappens() {
        var idle = moods()
        var random = SeededRandom(state: 1)
        _ = idle.update(now: 0, free: true, random: &random)
        XCTAssertEqual(idle.update(now: 600, free: true, random: &random).loop, "mood_sleepy")
        XCTAssertNil(idle.update(now: 660, free: true, random: &random).wake, "it stays")
        XCTAssertNil(idle.update(now: 661, free: false, random: &random).loop, "busy: awake")
        XCTAssertNil(idle.update(now: 662, free: true, random: &random).loop, "free again: the quiet spell starts over")

        var cozy = moods(.cozy)
        _ = cozy.update(now: 0, free: true, random: &random)
        XCTAssertEqual(cozy.update(now: 300, free: true, random: &random).loop, "mood_sleepy", "a cozy one: sooner")
    }

    func testASheetWithoutMoodLoopsStaysIdle() throws {
        let fixtures = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
            .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
            .deletingLastPathComponent().appending(path: "AvatarKit/Tests/AvatarKitTests/Fixtures")
        let fox = try SpriteSheet(json: try Data(contentsOf: fixtures.appending(path: "fox-tangerine-cheerful.json")),
                                  png: try Data(contentsOf: fixtures.appending(path: "fox-tangerine-cheerful.png")))
        var idle = IdleMoods(sheet: fox, personality: .cheerful)
        var random = SeededRandom(state: 3)
        for now in stride(from: 0.0, through: 1200, by: 15) {
            XCTAssertNil(idle.update(now: now, free: true, random: &random).loop)
        }
    }

    func testTheGiraffeHasEveryMood() {
        for mood in ["calm", "sleepy", "dizzy", "crying", "kiss", "angry", "focused", "surprised"] {
            XCTAssertNotNil(SpriteSheet.giraffe.loops[MoodBook.loop(mood)], mood)
        }
    }
}

/// The owner's mood table: which moods each situation picks from, weighted by personality.
@MainActor
final class MoodBookTests: XCTestCase {
    func testEverySetUsesRealMoodsAndTogetherNearlyAllOfThem() {
        let situations: [MoodBook.Situation] = [.needsYou, .done, .problem, .longDone, .stopped, .longStopped,
                                                 .denied, .manyQuests, .listening, .thanks, .greeting, .idle]
        var used: Set<String> = ["thinking", "focused", "working", "sleepy", "neutral"]
        for situation in situations {
            for mood in MoodBook.moods(situation, personality: .grumpy) {
                XCTAssertNotNil(SpriteSheet.giraffe.loops[MoodBook.loop(mood)], "\(situation): \(mood)")
                used.insert(mood)
            }
        }
        XCTAssertEqual(used.count, 25, "every Pixel Zoo mood has a situation")
    }

    func testAProblemPicksFromTheErrorSetAndOnlyAGrumpyHelperGetsAngry() {
        XCTAssertEqual(MoodBook.moods(.problem, personality: .cheerful),
                       ["embarrassed", "grumpy", "confused", "sad", "worried"])
        XCTAssertTrue(MoodBook.moods(.problem, personality: .grumpy).contains("angry"))
        var random = SeededRandom(state: 5)
        let picks = Set((0..<200).map { _ in MoodBook.pick(.problem, personality: .shy, random: &random) })
        XCTAssertEqual(picks, ["embarrassed", "grumpy", "confused", "sad", "worried"], "not always worried")
    }

    func testPersonalityWeightsThePick() {
        var random = SeededRandom(state: 9)
        let grumpy = (0..<900).map { _ in MoodBook.pick(.problem, personality: .grumpy, random: &random) }
        let cheerful = (0..<900).map { _ in MoodBook.pick(.problem, personality: .cheerful, random: &random) }
        let grumpyShare = Double(grumpy.filter { $0 == "grumpy" }.count) / 900
        let cheerfulShare = Double(cheerful.filter { $0 == "grumpy" }.count) / 900
        // Grumpy: 3 of 3+1+1+1+1+3 (angry is a favourite too) = 0.3; cheerful: 1 of 5 = 0.2.
        XCTAssertGreaterThan(grumpyShare, cheerfulShare + 0.05)
    }

    func testAThankYouIsHeard() {
        XCTAssertTrue(MoodBook.isThanks("Thanks Marcel!"))
        XCTAssertTrue(MoodBook.isThanks("merci ❤️"))
        XCTAssertFalse(MoodBook.isThanks("What's on Thursday?"))
    }

    private func model() async -> (ChatModel, () -> TimeInterval, (TimeInterval) -> Void) {
        let model = ChatModel(service: FakeService())
        var now: TimeInterval = 1000
        model.clock = { now }
        model.random = SeededRandom(state: 11)
        return (model, { now }, { now += $0 })
    }

    func testHeldStatesPlayAPickedMoodAndProblemsUseTheErrorSet() async {
        let (model, _, advance) = await model()
        model.receive(Fx.frame("agent.state", ["agent_id": "agt_marcel", "state": ["kind": "needs_you"]], id: 1))
        XCTAssertTrue(["mood_surprised", "mood_shocked", "mood_idea"].contains(model.mood ?? ""), "\(model.mood ?? "-")")
        advance(PoseGate.hold)
        model.receive(Fx.frame("agent.state", ["agent_id": "agt_marcel", "state": ["kind": "done"]], id: 2))
        XCTAssertTrue(["mood_happy", "mood_laughing", "mood_proud", "mood_excited"].contains(model.mood ?? ""))
        advance(PoseGate.hold)
        model.receive(Fx.frame("agent.state", ["agent_id": "agt_marcel", "state": ["kind": "thinking"]], id: 3))
        XCTAssertNil(model.mood, "thinking plays its own pose")
    }

    func testThreeQuestsAtOnceMakeMarcelDizzyForOnePass() async {
        let (model, _, advance) = await model()
        model.receive(Fx.frame("agent.state", ["agent_id": "agt_marcel",
                                               "state": ["kind": "working", "active_tasks": 3]], id: 1))
        XCTAssertEqual(model.mood, "mood_dizzy")
        XCTAssertEqual(model.action, .working(3), "the bubble still says what it does")
        advance(3)
        model.updateAction()
        XCTAssertNil(model.mood, "then the laptop again")
    }

    func testAStoppedQuestIsSadAndAFailedOneShowsAProblem() async {
        let (model, _, _) = await model()
        model.receive(Fx.frame("agent.state", ["agent_id": "agt_marcel", "state": ["kind": "idle"]], id: 1))
        var task = Fx.task("tsk_9", title: "Energy", state: "working")
        model.receive(Fx.frame("task.updated", task, id: 2))
        task["state"] = "stopped"
        model.receive(Fx.frame("task.updated", task, id: 3))
        XCTAssertTrue(["mood_sad", "mood_embarrassed"].contains(model.mood ?? ""), "\(model.mood ?? "-")")
    }
}

/// The moods' controls and the events that start them, through `ChatModel.receive` on a hand-moved clock.
@MainActor
final class MoodEventTests: XCTestCase {
    private static let fixtures = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
        .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
        .deletingLastPathComponent().appending(path: "AvatarKit/Tests/AvatarKitTests/Fixtures")

    private func fox() throws -> SpriteSheet {
        try SpriteSheet(json: try Data(contentsOf: Self.fixtures.appending(path: "fox-tangerine-cheerful.json")),
                        png: try Data(contentsOf: Self.fixtures.appending(path: "fox-tangerine-cheerful.png")))
    }

    private func model() -> (ChatModel, (TimeInterval) -> Void) {
        let model = ChatModel(service: FakeService())
        var now: TimeInterval = 1000
        model.clock = { now }
        model.random = SeededRandom(state: 21)
        model.receive(Fx.frame("agent.state", ["agent_id": "agt_marcel", "state": ["kind": "idle"]], id: 1))
        return (model, { now += $0 })
    }

    func testReduceMotionShowsOnlyTheStatePoses() {
        let waiting = ChatHeaderState(action: .waiting, mood: "mood_shocked")
        XCTAssertEqual(AvatarHeader.pose(state: waiting, idleMood: nil, reduceMotion: false, sheet: .giraffe),
                       .mood("mood_shocked"))
        XCTAssertEqual(AvatarHeader.pose(state: waiting, idleMood: nil, reduceMotion: true, sheet: .giraffe),
                       .needsYou, "Reduce Motion: no mood over the pose")
        let free = ChatHeaderState(action: .idle)
        XCTAssertEqual(AvatarHeader.pose(state: free, idleMood: "mood_wink", reduceMotion: true, sheet: .giraffe),
                       .idle, "Reduce Motion: no idle moods")
    }

    func testAnOlderSheetPlaysTheStateLoopsNotIdle() throws {
        let fox = try fox()
        let waiting = ChatHeaderState(action: .waiting, mood: "mood_shocked")
        XCTAssertEqual(AvatarHeader.pose(state: waiting, idleMood: nil, reduceMotion: false, sheet: fox), .needsYou,
                       "no mood loops: the needs-you loop, not idle")
        let reading = ChatHeaderState(action: .reading)
        XCTAssertEqual(AvatarHeader.pose(state: reading, idleMood: nil, reduceMotion: false, sheet: fox), .working(1))
        let problem = ChatHeaderState(action: .error)
        XCTAssertEqual(AvatarHeader.pose(state: problem, idleMood: nil, reduceMotion: false, sheet: fox), .needsYou)
    }

    func testAQuestsFirstUpdateIsOnlyNoted() {
        let (model, _) = model()
        for (i, state) in ["done", "failed", "stopped"].enumerated() {
            let task = Fx.task("tsk_old\(i)", title: "Old", state: state, at: 3600)
            model.receive(Fx.frame("task.updated", task, id: 10 + i))
        }
        XCTAssertNil(model.mood, "quests that ended before the app saw them start no mood")
        XCTAssertEqual(model.action, .idle, "and no problem")
    }

    func testALongQuestEndingAndAFailedOneAfterTheModelSawThem() {
        let (model, advance) = model()
        model.receive(Fx.frame("task.updated", Fx.task("tsk_1", title: "Energy", state: "working"), id: 2))
        model.receive(Fx.frame("task.updated", Fx.task("tsk_1", title: "Energy", state: "done", at: 900), id: 3))
        XCTAssertTrue(["mood_calm", "mood_proud", "mood_love"].contains(model.mood ?? ""), "\(model.mood ?? "-")")
        advance(5)
        model.receive(Fx.frame("task.updated", Fx.task("tsk_2", title: "NAS", state: "working"), id: 4))
        model.receive(Fx.frame("task.updated", Fx.task("tsk_2", title: "NAS", state: "failed"), id: 5))
        XCTAssertEqual(model.action, .error, "a failed quest shows a problem")
    }

    func testYourMessageAThankYouADenialAndTheHello() async {
        let (model, advance) = model()
        model.greet()
        XCTAssertTrue(["mood_happy", "mood_excited", "mood_love"].contains(model.mood ?? ""), "hello")
        advance(5)
        model.updateAction()
        XCTAssertNil(model.mood, "one pass, then idle")

        await model.start()
        model.draft = "What's on Thursday?"
        await model.send()
        XCTAssertEqual(model.mood, "mood_focused", "listening")
        advance(5)
        model.draft = "Thanks Marcel ❤️"
        await model.send()
        XCTAssertTrue(["mood_love", "mood_kiss", "mood_happy"].contains(model.mood ?? ""), "a thank-you")
        advance(5)
        let denied = Fx.approval("apr_1", task: "tsk_1", summary: "Send the email", state: "denied")
        model.receive(Fx.frame("approval.resolved", denied, id: 9))
        XCTAssertTrue(["mood_sad", "mood_embarrassed", "mood_grumpy"].contains(model.mood ?? ""), "you said no")
    }

    func testTheHelloWaitsWhileMarcelIsBusy() {
        let (model, advance) = model()
        advance(PoseGate.hold)
        model.receive(Fx.frame("agent.state", ["agent_id": "agt_marcel", "state": ["kind": "thinking"]], id: 2))
        model.greet()
        XCTAssertNil(model.mood)
    }
}

extension IdleMoodsTests {
    func testASheetWithSomeMoodsKeepsTrying() {
        // Only `mood_wink` exists: picks of other moods wait another gap instead of stopping the idle moods.
        var idle = IdleMoods(personality: nil, has: { $0 == "mood_wink" }, duration: { _ in 2.24 })
        var random = SeededRandom(state: 4)
        var now: TimeInterval = 0
        var played = 0
        for _ in 0..<200 {
            let step = idle.update(now: now, free: true, random: &random)
            if step.loop == "mood_wink" { played += 1 }
            guard let wake = step.wake else { return XCTFail("the idle moods stopped") }
            now = wake
        }
        XCTAssertGreaterThan(played, 0)
    }
}
