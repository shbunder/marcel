import Foundation
import MarcelKit
import XCTest

@testable import MarcelNotebook

/// S-12.12: "What Marcel carries" reads the hub's list (`GET /agents/{id}/brain/rollovers`, app API 0.6) with this
/// phone's log as the fallback, and "Fresh start" asks for one (`POST /agents/{id}/brain/rollover`).
@MainActor
final class RolloverHistoryTests: XCTestCase {
    private func model(_ hub: FakeHub, log: RolloverLog? = nil, clock: Date = Fx.now) -> NotebookModel {
        NotebookModel(service: hub.service, drafts: DraftStore(defaults: .scratch("drafts-\(name)")),
                      rollovers: log ?? RolloverLog(defaults: .scratch("rollovers-\(name)")),
                      projectNames: { Fx.projectNames }, text: Fx.text, clock: { clock })
    }

    func testTheHubsListIsReadNewestTenAndMergedWithThePhonesLogWithoutDuplicates() async throws {
        let hub = FakeHub()
        hub[Fx.rolloversRoute] = (200, Fx.json([
            Fx.hubRollover("rol_2", at: Fx.at(-3600 * 19 - 40 * 60)),
            Fx.hubRollover("rol_1", at: Fx.at(-86_400 * 3), reason: "context_full", kept: "Your notes."),
        ]))
        let log = RolloverLog(defaults: .scratch())
        // The phone saw rol_2 as an event too (a few seconds apart), and one the hub's list doesn't have yet.
        log.receive(Fx.rollover(id: 12, at: Fx.at(-3600 * 19 - 40 * 60 + 3)))
        log.receive(Fx.rollover(id: 30, at: Fx.at(-60), reason: "manual", kept: "Your notes and today's plan."))
        let model = model(hub, log: log)
        await model.load()

        XCTAssertTrue(hub.requests.contains(Fx.rolloversRoute), "\(hub.requests)")
        XCTAssertEqual(model.hubRollovers?.map(\.id), ["rol_2", "rol_1"])
        XCTAssertEqual(model.carried.map(\.id), ["event-30", "rol_2", "rol_1"], "each rollover once, newest first")
        XCTAssertEqual(Fx.text.rollover(model.carried[2]),
                       "Context rolled over Thu 1 Oct, 18:40 (its working memory was full). Kept: Your notes. "
                       + "Your chat history is unchanged.")
        XCTAssertNil(model.rolloversNote)

        // A rollover that arrives on the stream while the screen is open shows at once.
        log.receive(Fx.rollover(id: 31, at: Fx.now, reason: "crashed"))
        XCTAssertEqual(model.carried.first?.id, "event-31")
    }

    func testAtMostTenAreShown() {
        let hub = (1...10).map { Rollover(id: "rol_\($0)", at: Fx.at(TimeInterval(-$0 * 3600)), reason: "daily", kept: "") }
        let phone = [Rollover(eventId: 1, at: Fx.now, reason: "manual", kept: "")]
        let merged = Rollover.merged(hub: hub, phone: phone)
        XCTAssertEqual(merged.count, 10)
        XCTAssertEqual(merged.first?.id, "event-1")
        XCTAssertEqual(merged.last?.id, "rol_9")
    }

    /// Before S-06.7 the hub answers its generic 404 `not_found` (or 405): the phone's own log shows, silently.
    func testAHubWithoutTheListFallsBackToThePhonesLog() async {
        for answer in [Fx.notBuilt, (405, Fx.error("method_not_allowed", "That address does not take this kind of request.")),
                       (404, #"{"detail":"Not Found"}"#)] {
            let hub = FakeHub()
            hub[Fx.rolloversRoute] = answer
            let log = RolloverLog(defaults: .scratch("fallback-\(answer.0)-\(answer.1.count)"))
            log.receive(Fx.rollover(id: 12, at: Fx.at(-600)))
            let model = model(hub, log: log)
            await model.load()
            XCTAssertNil(model.hubRollovers, answer.1)
            XCTAssertEqual(model.carried.map(\.id), ["event-12"], answer.1)
            XCTAssertNil(model.rolloversNote, "a hub that has not built it yet is not a problem: \(answer.1)")
            XCTAssertEqual(model.state, .loaded, "the notes still load")
        }
    }

    func testAnotherFailureSaysSoAndStillShowsThePhonesLog() async {
        let hub = FakeHub()
        hub[Fx.rolloversRoute] = (500, Fx.error("internal", "Boom."))
        let log = RolloverLog(defaults: .scratch())
        log.receive(Fx.rollover(id: 12, at: Fx.at(-600)))
        let model = model(hub, log: log)
        await model.load()
        XCTAssertEqual(model.carried.map(\.id), ["event-12"])
        XCTAssertEqual(model.rolloversNote, "Couldn't read the past fresh starts from the hub, so this shows the ones "
                       + "this phone saw. The hub had a problem. Try again in a minute.")
    }

    /// The log an older build kept (ids were the event's number) still reads.
    func testAnOlderLogStillReads() throws {
        let defaults = UserDefaults.scratch()
        defaults.set(Data(#"[{"id": 12, "at": 0, "reason": "daily", "kept": "x"}]"#.utf8), forKey: RolloverLog.key)
        XCTAssertEqual(RolloverLog(defaults: defaults).rollovers.map(\.id), ["event-12"])
    }

    // MARK: Fresh start

    func testFreshStartRunsUntilTheManualRolloverShows() async {
        let hub = FakeHub()
        hub[Fx.rollOverRoute] = (202, "")
        let log = RolloverLog(defaults: .scratch())
        let model = model(hub, log: log)
        await model.load()
        XCTAssertTrue(model.canStartFresh)
        XCTAssertNil(model.freshStartLine)

        await model.startFresh()
        XCTAssertTrue(hub.requests.contains(Fx.rollOverRoute), "\(hub.requests)")
        XCTAssertEqual(model.freshStart, .running(since: Fx.now))
        XCTAssertFalse(model.canStartFresh, "off while one is on its way")
        XCTAssertEqual(model.freshStartLine, "Fresh start on its way. Marcel says hello in the chat when it's ready, "
                       + "and it shows here then.")
        await model.startFresh()
        XCTAssertEqual(hub.requests.filter { $0 == Fx.rollOverRoute }.count, 1, "a second tap sends nothing")

        // An older daily rollover arriving changes nothing; the manual one finishes it.
        log.receive(Fx.rollover(id: 40, at: Fx.at(-3600)))
        XCTAssertFalse(model.canStartFresh)
        log.receive(Fx.rollover(id: 41, at: Fx.at(45), reason: "manual"))
        XCTAssertTrue(model.canStartFresh)
        XCTAssertEqual(model.freshStartLine, "Done: Marcel started fresh today 18:40. It shows above.")
    }

    func testOneAlreadyOnItsWay() async {
        let hub = FakeHub()
        hub[Fx.rollOverRoute] = (409, Fx.error("rollover_running", "A rollover is already waiting."))
        let model = model(hub)
        await model.startFresh()
        XCTAssertEqual(model.freshStart, .running(since: Fx.now), "409 rollover_running: wait for that one")
        XCTAssertFalse(model.canStartFresh)
    }

    func testFailuresInPlainWords() async {
        let cases: [((Int, String), String)] = [
            (Fx.notBuilt, "Not available yet: this hub can't give Marcel a fresh start yet."),
            ((405, #"{"detail":"Method Not Allowed"}"#), "Not available yet: this hub can't give Marcel a fresh start yet."),
            ((503, Fx.error("runner_unreachable", "The runner is down.")),
             "The hub can't start a new session for Marcel right now. Nothing changed. Try again in a few minutes."),
            ((404, Fx.error("agent_not_found", "No such agent.")),
             "This buddy is gone or archived, so its notebook can't change. Pull down to refresh."),
            ((401, Fx.error("unauthorized", "No.")), "The hub no longer accepts this phone. Pair it again."),
        ]
        for (answer, words) in cases {
            let hub = FakeHub()
            hub[Fx.rollOverRoute] = answer
            let model = model(hub)
            await model.startFresh()
            XCTAssertEqual(model.freshStart, .failed(words), answer.1)
            XCTAssertEqual(model.freshStartLine, words)
            XCTAssertTrue(model.canStartFresh, "it can be tried again")
        }
        let offline = FakeHub()
        offline.offline = true
        let model = model(offline)
        await model.startFresh()
        XCTAssertEqual(model.freshStart, .failed("Couldn't reach the hub. Check the connection and try again."))
    }
}
