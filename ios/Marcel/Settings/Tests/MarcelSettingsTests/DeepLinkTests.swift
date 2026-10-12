import Foundation
import XCTest

@testable import MarcelSettings

/// Reading `marcel://` links: every kind the app routes, and everything else refused in plain words without a crash.
final class DeepLinkTests: XCTestCase {
    func testEveryKindReads() {
        let cases: [(String, DeepLink)] = [
            ("marcel://task/tsk_01J9ZQ6", .task("tsk_01J9ZQ6")),
            ("marcel://message/msg_01J9ZQZ8", .message("msg_01J9ZQZ8")),
            ("marcel://approval/apr_01J9ZR2", .approval("apr_01J9ZR2")),
            ("marcel://project/marcel-dev", .project(slug: "marcel-dev")),
            ("marcel://artifact/art_01JA0A2", .artifact("art_01JA0A2")),
            ("marcel://status", .status),
            ("marcel://status/", .status),
            ("MARCEL://TASK/tsk_1", .task("tsk_1")),
        ]
        for (text, link) in cases {
            XCTAssertEqual(DeepLink.parse(text), .success(link), text)
            XCTAssertEqual(DeepLink.parse(link.url), .success(link), "\(link) writes itself back out")
        }
    }

    func testTheContractsExampleAndPatternRead() throws {
        let contract = try String(contentsOf: URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
            .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
            .appendingPathComponent("contracts/app-api.yaml"), encoding: .utf8)
        // The push example's link.
        XCTAssertTrue(contract.contains("deep_link: marcel://approval/apr_01J9ZR2"))
        XCTAssertEqual(DeepLink.parse("marcel://approval/apr_01J9ZR2"), .success(.approval("apr_01J9ZR2")))
    }

    func testOtherSchemesAreNotMarcelLinks() {
        for text in ["https://marcel-bot.com/task/tsk_1", "http://task/tsk_1", "javascript:alert(1)", "marcelx://task/a",
                     "file:///etc/passwd", "", "task/tsk_1"] {
            XCTAssertEqual(DeepLink.parse(text), .failure(.notMarcel), text)
        }
    }

    func testPairingCodesNeverPairFromALink() {
        XCTAssertEqual(DeepLink.parse("marcel://pair?hub=https%3A%2F%2Fevil.example%2Fapi&code=K7Q2M9"),
                       .failure(.pairing))
    }

    func testUnknownKinds() {
        for text in ["marcel://settings", "marcel://tasks/tsk_1", "marcel://", "marcel:task/tsk_1", "marcel:///task/x"] {
            XCTAssertEqual(DeepLink.parse(text), .failure(.unknownKind), text)
        }
    }

    func testDamagedLinks() {
        let damaged = [
            "marcel://task", "marcel://task/", "marcel://task/a/b", "marcel://task/../etc",
            "marcel://task/tsk%2F1", "marcel://task/tsk 1", "marcel://task/tsk_1?x=1", "marcel://task/tsk_1#frag",
            "marcel://user@task/tsk_1", "marcel://task:8080/tsk_1", "marcel://task/" + String(repeating: "a", count: 129),
            "marcel://task/tsk_é", "marcel://project/Energy", "marcel://project/-energy", "marcel://project/energy_1",
            "marcel://project/" + String(repeating: "a", count: 41), "marcel://status/now", "marcel://status?x=1",
            "marcel://artifact/<script>",
        ]
        for text in damaged {
            let result = DeepLink.parse(text)
            XCTAssertTrue(result == .failure(.damaged) || result == .failure(.notMarcel), "\(text): \(result)")
        }
    }

    func testAVeryLongLinkIsRefusedBeforeItIsRead() {
        XCTAssertEqual(DeepLink.parse("marcel://task/" + String(repeating: "a", count: 10_000)), .failure(.notMarcel))
    }

    func testEveryProblemHasPlainWords() {
        for problem in [DeepLink.Problem.notMarcel, .pairing, .unknownKind, .damaged] {
            let words = problem.errorDescription ?? ""
            XCTAssertFalse(words.isEmpty)
            XCTAssertTrue(words.hasSuffix("."), words)
        }
    }
}
