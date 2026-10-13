import Foundation
import MarcelKit
import SwiftUI
import UIKit
import XCTest

@testable import MarcelQuests

/// The quest log as wireframe 2C draws it, plus its other states, compared pixel for pixel with
/// `__Snapshots__/*.png` (light, dark and the largest text size). `ImageRenderer` draws nothing inside a `List`, so
/// these render `QuestLogStill`: the same header, group titles and cards, in a plain stack.
@MainActor
final class QuestSnapshotTests: XCTestCase {
    /// Wireframe 2C: one quest waiting on you, two working, one PR ready for review, five done this week (folded).
    static var wireframe: QuestBoard {
        let tasks = [
            Fx.task("t_dentist", "Dentist booking", state: "needs_you", project: "prj_family", created: 3 * Fx.hour,
                    active: 8 * Fx.minute, extra: ["waiting_for": "Pick Tue 9:10 or Thu 8:30"]),
            Fx.task("t_energy", "Energy contracts", state: "working", project: "prj_energy", created: 5 * Fx.hour,
                    active: Fx.minute),
            Fx.task("t_lunch", "Sunday lunch", state: "working", project: "prj_family", location: "cloud",
                    created: 40 * Fx.minute, active: 6 * Fx.minute),
            Fx.task("t_avatar", "Avatar sprite in app", state: "done", project: "prj_dev", location: "cloud",
                    created: 26 * Fx.hour, active: 2 * Fx.hour),
        ] + (1...5).map { Fx.task("t_done\($0)", "Done \($0)", state: "done", active: Double($0) * Fx.day) }
        return QuestBoard(tasks: tasks, artifacts: [Fx.pr("art_pr", task: "t_avatar")], projects: Fx.projects,
                          now: Fx.now)
    }

    /// Everything else a log can show: a problem, the queue, a quiet quest and the done group opened.
    static var busy: QuestBoard {
        let tasks = [
            Fx.task("t_backup", "Backup check", state: "silent", project: "prj_inbox", created: 9 * Fx.hour,
                    active: 3 * Fx.hour),
            Fx.task("t_disk", "Disk usage", state: "queued", project: "prj_dev", created: 2 * Fx.minute,
                    active: 2 * Fx.minute, extra: ["queued_reason": "cap"]),
            Fx.task("t_bins", "Paper bins reminder", state: "done", project: "prj_inbox", active: 20 * Fx.hour),
            Fx.task("t_pass", "Renew passport", state: "failed", project: "prj_family", active: 2 * Fx.day),
            Fx.task("t_login", "Fix the flaky login test", state: "done", project: "prj_dev", active: 3 * Fx.day),
        ]
        return QuestBoard(tasks: tasks, artifacts: [Fx.pr("art_pr", task: "t_login", number: 14, state: "merged")],
                          projects: Fx.projects, now: Fx.now)
    }

    func testTheQuestLogLooksLikeWireframe2C() {
        Snapshot.assert(QuestLogStill(board: Self.wireframe, showsFinished: false), named: "QuestLog", padding: 0)
    }

    func testTheDoneGroupOpenWithAProblemAndTheQueue() {
        Snapshot.assert(
            QuestLogStill(board: Self.busy, showsFinished: true,
                          problem: "The NUC didn't answer, so the quest is still running. Try again in a minute."),
            named: "QuestLog-open", variants: [.light, .dark], padding: 0)
    }

    func testAnEmptyArchiveFilteredByProject() {
        let board = QuestBoard(tasks: [], projects: Fx.projects,
                               filter: QuestFilter(projectId: "prj_energy", archived: true), now: Fx.now)
        Snapshot.assert(QuestLogStill(board: board, showsFinished: false), named: "QuestLog-archived-empty",
                        variants: [.light], padding: 0)
    }
}

/// What VoiceOver reads on the quest log, from the accessibility tree SwiftUI builds (as PixelKit's audit does, since
/// XCUITest's audit cannot run on this Mac).
@MainActor
final class QuestAccessibilityTests: XCTestCase {
    override class func setUp() {
        super.setUp()
        let handle = dlopen("/usr/lib/libAccessibility.dylib", RTLD_NOW)
        typealias Enable = @convention(c) (Bool) -> Void
        if let symbol = dlsym(handle, "_AXSApplicationAccessibilitySetEnabled") {
            unsafeBitCast(symbol, to: Enable.self)(true)
        }
    }

    private var windows: [UIWindow] = []

    override func tearDown() {
        windows.forEach { $0.isHidden = true }
        windows = []
        super.tearDown()
    }

    private func elements<V: View>(of view: V, dynamicType: DynamicTypeSize = .large)
        -> [(label: String, traits: UIAccessibilityTraits, frame: CGRect)]
    {
        let host = UIHostingController(rootView: view.environment(\.dynamicTypeSize, dynamicType))
        let window = UIWindow(frame: CGRect(x: 0, y: 0, width: 390, height: 4000))
        window.rootViewController = host
        window.makeKeyAndVisible()
        windows.append(window)
        host.view.layoutIfNeeded()
        RunLoop.main.run(until: Date().addingTimeInterval(0.3))
        var found: [(String, UIAccessibilityTraits, CGRect)] = []
        func walk(_ object: NSObject, depth: Int) {
            guard depth < 20 else { return }
            if object.isAccessibilityElement {
                found.append((object.accessibilityLabel ?? "", object.accessibilityTraits, object.accessibilityFrame))
            }
            if let children = object.accessibilityElements as? [NSObject], !children.isEmpty {
                children.forEach { walk($0, depth: depth + 1) }
            } else if object.accessibilityElementCount() != NSNotFound, object.accessibilityElementCount() > 0 {
                for index in 0..<object.accessibilityElementCount() {
                    if let child = object.accessibilityElement(at: index) as? NSObject { walk(child, depth: depth + 1) }
                }
            } else if let view = object as? UIView {
                view.subviews.forEach { walk($0, depth: depth + 1) }
            }
        }
        walk(host.view, depth: 0)
        return found
    }

    func testEveryCardReadsAsOneSentenceInPlainWords() {
        let board = QuestSnapshotTests.wireframe
        for size in [DynamicTypeSize.large, .accessibility5] {
            let found = elements(of: QuestLogStill(board: board, showsFinished: true), dynamicType: size)
            let labels = found.map(\.label)
            for row in board.sections.flatMap(\.rows) {
                XCTAssertTrue(labels.contains(row.spoken), "\(size): no element reads \"\(row.spoken)\"")
            }
            XCTAssertTrue(labels.contains("Quests"))
            XCTAssertTrue(labels.contains("Waiting on you"))
            XCTAssertTrue(labels.contains("Done, this week, 5 quests"))
            for label in labels {
                XCTAssertFalse(label.isEmpty, "\(size): an element has no label")
                XCTAssertNil(label.first { "▶▼♥◀▸▾·".contains($0) }, "\(size): \"\(label)\" has a symbol")
                // "NUC" is the home server's name, said as one word; everything else is in sentence case.
                let shouted = label.split { !$0.isLetter }
                    .first { $0.count >= 3 && $0 == $0.uppercased() && $0 != $0.lowercased() && $0 != "NUC" }
                XCTAssertNil(shouted, "\(size): \"\(label)\" is in capitals")
            }
            let headers = found.filter { $0.traits.contains(.header) }.map(\.label)
            XCTAssertTrue(headers.contains("Waiting on you"), "\(size): group titles are headers")
            let card = found.first { $0.label == board.sections[0].rows[0].spoken }
            XCTAssertGreaterThanOrEqual(card?.frame.height ?? 0, 44, "\(size): a card is a comfortable target")
        }
    }
}
