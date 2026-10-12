import Foundation
import MarcelKit
import XCTest

@testable import MarcelItems

/// What each kind of item becomes in the viewer, from the mock hub's and the contract's fixtures; Markdown blocks and
/// links; dashboards that do and do not match `contracts/dashboard.schema.json`.
final class ItemBodyTests: XCTestCase {
    private func body(_ json: [String: Any], _ data: Data?) -> ItemBody {
        ItemBody.make(Fx.artifact(json), contentType: nil, content: data.map(ContentState.loaded))
    }

    // MARK: Each kind

    func testAMarkdownDocumentIsDrawnNatively() {
        guard case .markdown(let parts) = body(Fx.mockArtifact(Fx.docId), Fx.mockHub("doc-v3.md")) else {
            return XCTFail("not Markdown")
        }
        XCTAssertEqual(parts.map(\.block), [
            .heading(level: 1, text: "Energy offers compared"),
            .paragraph("Version 3. The mock hub has no real content for this document."),
        ])
    }

    func testAnUntrustedHTMLPageHasItsScriptsOff() {
        XCTAssertEqual(body(Fx.html, Fx.htmlContent),
                       .html(String(decoding: Fx.htmlContent, as: UTF8.self), trusted: false))
        var trusted = Fx.html
        trusted["trusted"] = true
        guard case .html(_, let isTrusted) = body(trusted, Fx.htmlContent) else { return XCTFail("not HTML") }
        XCTAssertTrue(isTrusted, "only the hub's own HTML runs its scripts")
    }

    func testAFileGoesToQuickLookWithAFileNameItCanRead() {
        guard case .file(let file) = body(Fx.file, Fx.pdf) else { return XCTFail("not a file") }
        XCTAssertEqual(file.fileName, "Bills 2026.pdf")
        XCTAssertEqual(file.type, "PDF")
        XCTAssertEqual(file.data, Fx.pdf)
        XCTAssertEqual(FileItem.fileName(title: "../../etc/passwd", contentType: "text/plain"), "-..-etc-passwd.txt",
                       "a title never becomes a path")
        XCTAssertEqual(FileItem.fileName(title: "scan.PDF", contentType: "application/pdf"), "scan.PDF")
    }

    func testAPullRequestIsACardThatOpensOnlyOnGitHub() {
        guard case .pr(let card) = body(Fx.mockArtifact(Fx.prId), nil) else { return XCTFail("not a PR") }
        XCTAssertEqual(card.number, 14)
        XCTAssertEqual(card.state, .open)
        XCTAssertEqual(card.repository, "shbunder/marcel")
        XCTAssertEqual(card.checks, "Checks passing")
        XCTAssertEqual(card.review, "Review requested")
        XCTAssertEqual(card.github?.absoluteString, "https://github.com/shbunder/marcel/pull/14")
        XCTAssertEqual(card.spoken, "Pull request number 14. Fix flaky login test: wait on the session cookie. Open. "
                       + "In shbunder/marcel. Checks passing. Review requested")

        for url in ["http://github.com/a/b/pull/1", "https://github.com.evil.example/a/b/pull/1",
                    "https://gitlab.com/a/b/-/merge_requests/1", "javascript:alert(1)"] {
            var pr = Fx.mockArtifact(Fx.prId)
            pr["url"] = url
            guard case .pr(let card) = body(pr, nil) else { return XCTFail("not a PR") }
            XCTAssertNil(card.github, "\(url) is not offered as a button")
        }
    }

    /// S-12.11: `pr.note` (app API 0.5) says why the card may be stale; absent, null or empty says nothing.
    func testAPullRequestCardSaysWhyItMayBeOutOfDate() {
        XCTAssertEqual(Self.prWithNote("GitHub is having trouble.").note, "May be out of date: GitHub is having trouble.")
        XCTAssertTrue(Self.prWithNote("GitHub is having trouble.").spoken
            .hasSuffix("Review requested. May be out of date: GitHub is having trouble."))
        for nothing in [nil, NSNull(), ""] as [Any?] {
            XCTAssertNil(Self.prWithNote(nothing).note, String(describing: nothing))
        }
    }

    static func prWithNote(_ note: Any?) -> PRCard {
        var json = Fx.mockArtifact(Fx.prId)
        var pr = json["pr"] as! [String: Any]
        pr["note"] = note
        json["pr"] = pr
        guard case .pr(let card) = ItemBody.make(Fx.artifact(json), contentType: nil, content: nil) else {
            fatalError("not a PR")
        }
        return card
    }

    func testLinksAndBranchesOpenOnlyWebAddresses() {
        guard case .link(let link) = body(Fx.link, nil), case .link(let branch) = body(Fx.branch, nil) else {
            return XCTFail("not links")
        }
        XCTAssertEqual(link.url?.absoluteString, "https://www.engie.be/prices")
        XCTAssertEqual(link.buttonTitle, "Open in Safari")
        XCTAssertTrue(branch.isBranch)
        XCTAssertEqual(branch.buttonTitle, "Open in GitHub")
        var bad = Fx.link
        bad["url"] = "file:///etc/passwd"
        guard case .link(let card) = body(bad, nil) else { return XCTFail("not a link") }
        XCTAssertNil(card.url)
    }

    func testBoardsFromTheMockHubAreDrawnFromTheirJSON() {
        for (name, sections) in [("board-energy.json", 4), ("board-dev.json", 4)] {
            guard case .dashboard(let board) = body(Fx.mockArtifact(Fx.boardId), Fx.mockHub(name)) else {
                return XCTFail("\(name) did not parse")
            }
            XCTAssertEqual(board.widgets.count, sections)
        }
        guard case .dashboard(let board) = body(Fx.mockArtifact(Fx.boardId), Fx.mockHub("board-energy.json")),
              case .section(let waiting) = board.widgets[0], case .section(let results) = board.widgets[2],
              case .list(let list) = results.widgets[0] else { return XCTFail("unexpected board") }
        XCTAssertEqual(waiting.title, "Waiting on you")
        XCTAssertEqual(waiting.emptyText, "Nothing waiting on you.")
        XCTAssertEqual(list.items.first?.text, "Energy offers compared · v3")
    }

    func testEveryDashboardExampleInTheContractIsDrawn() {
        let kinds = Fx.contractDashboards.flatMap { data -> [String] in
            guard case .success(let dashboard) = Dashboard.parse(data) else {
                XCTFail("a contract example did not parse: \(String(decoding: data, as: UTF8.self))")
                return []
            }
            func kinds(_ widgets: [DashboardWidget]) -> [String] {
                widgets.flatMap { widget -> [String] in
                    switch widget {
                    case .stat: ["stat"]
                    case .list: ["list"]
                    case .checklist: ["checklist"]
                    case .table: ["table"]
                    case .chart: ["chart"]
                    case .note: ["note"]
                    case .section(let section): ["section"] + kinds(section.widgets)
                    }
                }
            }
            return kinds(dashboard.widgets)
        }
        XCTAssertEqual(Set(kinds), ["stat", "list", "checklist", "table", "chart", "note", "section"],
                       "the examples cover every widget kind")
    }

    func testLoadingAndFailingSayWhatIsGoingOn() {
        let doc = Fx.artifact(Fx.mockArtifact(Fx.docId))
        XCTAssertEqual(ItemBody.make(doc, contentType: nil, content: nil), .loading)
        XCTAssertEqual(ItemBody.make(doc, contentType: nil, content: .loading), .loading)
        XCTAssertEqual(ItemBody.make(doc, contentType: nil, content: .failed("Couldn't load this item.")),
                       .failed("Couldn't load this item."))
    }

    // MARK: Dashboards that do not match the schema

    private func problem(_ json: String) -> String? {
        if case .failure(let problem) = Dashboard.parse(Data(json.utf8)) { return problem.message }
        return nil
    }

    func testADashboardThatDoesNotMatchTheSchemaIsAPlainMessageNotACrash() {
        let cases: [(String, String)] = [
            ("not json", "it isn't valid JSON"),
            (#"{"version": 2, "title": "x", "widgets": [{"type": "note", "text": "a"}]}"#,
             "it is in format version 2, and the app reads version 1"),
            (#"{"version": 1, "title": "x", "widgets": []}"#, "it has nothing in it"),
            (#"{"version": 1, "widgets": [{"type": "note", "text": "a"}]}"#, "“title” is missing"),
            (#"{"version": 1, "title": "x", "widgets": [{"type": "map"}]}"#,
             "it has a part of an unknown kind (“map”)"),
            (#"{"version": 1, "title": "x", "widgets": [{"type": "stat", "label": "Disk"}]}"#, "“value” is missing"),
            (#"{"version": 1, "title": "x", "widgets": [{"type": "stat", "label": "Disk", "value": 412}]}"#,
             "“value” has the wrong kind of value"),
            (#"{"version": 1, "title": "x", "widgets": [{"type": "chart", "kind": "bar", "labels": ["a", "b"], "#
                + #""series": [{"name": "s", "values": [1]}]}]}"#,
             "the chart line “s” has 1 values for 2 labels"),
            (#"{"version": 1, "title": "x", "widgets": [{"type": "chart", "kind": "pie", "labels": ["a"], "#
                + #""series": [{"name": "s", "values": [1]}]}]}"#, "“kind” has a value the app doesn’t know"),
            (#"{"version": 1, "title": "x", "widgets": [{"type": "section", "title": "a", "widgets": ["#
                + #"{"type": "section", "title": "b", "widgets": []}]}]}"#, "a section sits inside another section"),
            (#"{"version": 1, "title": "x", "widgets": [{"type": "table", "columns": ["a"], "rows": [["1", "2"]]}]}"#,
             "a table row has more cells than the table has columns"),
        ]
        for (json, reason) in cases {
            XCTAssertEqual(problem(json), "This can't be drawn: \(reason). Ask Marcel to make it again.", json)
        }
        let board = Fx.artifact(Fx.mockArtifact(Fx.boardId))
        XCTAssertEqual(ItemBody.make(board, contentType: nil, content: .loaded(Data("[1, 2]".utf8))),
                       .brokenDashboard("This can't be drawn: a part has the wrong kind of value. Ask Marcel to make it again."))
    }

    func testTableCellsAndChartNumbersInWords() {
        XCTAssertEqual(DashboardWidget.Chart.format(38.2), "38.2")
        XCTAssertEqual(DashboardWidget.Chart.format(1284), "1,284")
        XCTAssertEqual(DashboardWidget.Chart.format(0.126), "0.13")
        XCTAssertEqual(DashboardWidget.Cell.empty.text, "—")
        XCTAssertEqual(DashboardWidget.Cell.empty.spoken, "empty")
    }

    // MARK: Markdown

    func testMarkdownBlocksTablesAndOnlyWebLinks() {
        let parts = ItemMarkdown.parse(String(decoding: Fx.comparison, as: UTF8.self))
        XCTAssertEqual(parts.map(\.block), [
            .heading(level: 1, text: "Switch to Supplier A"),
            .paragraph("Cheapest of 4 offers on our address. With 4 kWp solar the yearly bill drops to €1,010."),
            .table(header: ["Offer", "Per year"], rows: [["Current", "€1,496"], ["Supplier A", "€1,284"],
                                                         ["A + solar", "€1,010"]]),
            .heading(level: 2, text: "Open questions"),
            .listItem(marker: "• ", text: "Solar quote expected Friday", depth: 0),
            .listItem(marker: "• ", text: "Heat pump: wait for the 2027 subsidy news", depth: 0),
            .paragraph("Sources: the offers are in the bills folder."),
        ])
        XCTAssertEqual(parts[5].links.map(\.title), ["2027 subsidy"])
        XCTAssertEqual(parts[5].links.first?.url.absoluteString, "https://www.vlaanderen.be/premies")
        XCTAssertEqual(parts[6].links, [], "a javascript: link stays plain words")
    }

    func testMarkdownListsQuotesCodeAndRules() {
        let parts = ItemMarkdown.parse("""
            1. First
            2. Second
               - inner

            > A quote

            ```
            let x = 1
            ```

            ---
            """)
        XCTAssertEqual(parts.map(\.block), [
            .listItem(marker: "1. ", text: "First", depth: 0),
            .listItem(marker: "2. ", text: "Second", depth: 0),
            .listItem(marker: "• ", text: "inner", depth: 1),
            .quote("A quote"),
            .code("let x = 1"),
            .rule,
        ])
        XCTAssertEqual(ItemMarkdown.parse("   "), [])
    }
}
