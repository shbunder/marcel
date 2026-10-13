import Foundation
import SwiftUI
import XCTest

@testable import PixelKit

/// The shared Markdown parser (moved from MarcelItems' ItemBodyTests in S-18.7, unchanged), and its inline style.
final class MarkdownTests: XCTestCase {
    /// Wireframe 4B's document, written as a worker would write it (MarcelItems' `Fx.comparison`).
    static let comparison = Data("""
        # Switch to Supplier A

        Cheapest of 4 offers on our address. With 4 kWp solar the yearly bill drops to **€1,010**.

        | Offer | Per year |
        |---|---|
        | Current | €1,496 |
        | Supplier A | €1,284 |
        | A + solar | €1,010 |

        ## Open questions

        - Solar quote expected Friday
        - Heat pump: wait for the [2027 subsidy](https://www.vlaanderen.be/premies) news

        Sources: [the offers](javascript:alert(1)) are in the bills folder.
        """.utf8)

    private enum Fx {
        static let comparison = MarkdownTests.comparison
    }


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

    // MARK: Inline style (S-18.7)

    func testBoldItalicCodeAndWebLinksAreKeptAsSpans() {
        let parts = ItemMarkdown.parse("Pay **€1,010** a *year*, see `bills/` or [the site](https://example.com).")
        XCTAssertEqual(parts.map(\.block), [.paragraph("Pay €1,010 a year, see bills/ or the site.")])
        XCTAssertEqual(parts.first?.spans, [
            .init("Pay "), .init("€1,010", bold: true), .init(" a "), .init("year", italic: true), .init(", see "),
            .init("bills/", code: true), .init(" or "), .init("the site", link: URL(string: "https://example.com")),
            .init("."),
        ])
    }

    func testOnlyWebLinksBecomeLinkSpansAndHTMLStaysWords() {
        let parts = ItemMarkdown.parse("[steal](javascript:alert(1)) [file](file:///etc/passwd) <b>bold?</b>")
        XCTAssertEqual(parts.flatMap(\.spans).compactMap(\.link), [])
        XCTAssertTrue(ItemMarkdown.plainText(parts).contains("<b>bold?</b>"), ItemMarkdown.plainText(parts))
    }

    func testListItemsAndQuotesKeepTheirStyleAndHeadingsAndCodeDoNot() {
        let parts = ItemMarkdown.parse("""
            # A **bold** title

            - one **two**

            > a *quote*

            ```
            **not bold**
            ```
            """)
        XCTAssertEqual(parts[0].spans, [], "headings are drawn in the label font, without inline style")
        XCTAssertEqual(parts[1].spans, [.init("one "), .init("two", bold: true)])
        XCTAssertEqual(parts[2].spans, [.init("a "), .init("quote", italic: true)])
        XCTAssertEqual(parts[3].block, .code("**not bold**"))
        XCTAssertEqual(parts[3].spans, [])
    }

    func testSpansLoseTheWhitespaceTheBlockWasTrimmedOf() {
        let spans = ItemMarkdown.parse("**Hi** there   ").first?.spans
        XCTAssertEqual(spans, [.init("Hi", bold: true), .init(" there")])
    }

    // MARK: Drawing

    @MainActor
    func testDrawingOpensNothingAndAWideTableStillDraws() {
        var opened: [URL] = []
        let wide = "| " + (1...14).map { "Column \($0)" }.joined(separator: " | ") + " |\n|"
            + String(repeating: "---|", count: 14) + "\n| " + (1...14).map { "value \($0)" }.joined(separator: " | ") + " |"
        let view = MarkdownView(parts: ItemMarkdown.parse("**[site](https://example.com)** and *x*\n\n" + wide))
            .environment(\.openURL, OpenURLAction { url in
                opened.append(url)
                return .handled
            })
            .frame(width: 390)
        XCTAssertNotNil(ImageRenderer(content: view).uiImage)
        XCTAssertTrue(opened.isEmpty, "a link opens only when it is tapped")
    }

    @MainActor
    func testATableDrawsAtMostTheCapAndCountsTheRest() {
        let rows = (1...205).map { ["row \($0)", "\($0)"] }
        let table = MarkdownTable(header: ["Name", "Value"], rows: rows)
        XCTAssertEqual(table.shown.count, MarkdownLimits.tableRows)
        XCTAssertEqual(table.more, 5)
        XCTAssertEqual(table.spoken(["Current", ""]), "Name: Current, Value: empty")
    }
}
