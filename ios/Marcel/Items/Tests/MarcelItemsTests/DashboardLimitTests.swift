import Foundation
import SwiftUI
import XCTest

@testable import MarcelItems

/// Dashboards the hub let through but the phone cannot draw as they are: values Swift Charts cannot put on an axis,
/// and sizes that would freeze the screen. Each is cut short or left out, and the screen says so in words.
@MainActor
final class DashboardLimitTests: XCTestCase {
    private func chart(_ values: [Double?]) -> DashboardWidget.Chart {
        DashboardWidget.Chart(kind: .bar, title: "Odd", unit: nil, labels: values.indices.map { "p\($0)" },
                              series: [.init(name: "s", values: values)])
    }

    private func render<V: View>(_ view: V) -> CGImage? {
        let renderer = ImageRenderer(content: view.frame(width: 390).environment(\.colorScheme, .light))
        renderer.proposedSize = ProposedViewSize(width: 390, height: nil)
        return renderer.cgImage
    }

    // Regression: a chart with 1e308 and −1e308 stopped the app inside Swift Charts' axis.
    func testHugeInfiniteAndMissingNumbersAreLeftOutOfAChartAndItSaysSo() {
        let odd = chart([1e308, -1e308, .nan, .infinity, -.infinity, 2, nil])
        let drawn = DrawnChart(odd)
        XCTAssertEqual(drawn.points.map(\.value), [2])
        XCTAssertEqual(drawn.leftOut, 5)
        XCTAssertEqual(drawn.domain, 0...2)
        XCTAssertEqual(drawn.note, "5 values were left out: they are too large to draw.")
        XCTAssertNotNil(render(ChartView(chart: odd)), "the chart draws instead of stopping the app")
        XCTAssertNotNil(render(ChartView(chart: chart([1e308, -1e308]))))
        XCTAssertNotNil(render(ChartView(chart: chart([.nan, .infinity]))), "nothing left to draw still draws")
        XCTAssertTrue(ChartView(chart: odd).spoken.hasSuffix("p5 2. 5 values were left out: they are too large to draw."),
                      ChartView(chart: odd).spoken)
    }

    func testAnAxisAlwaysHasARange() {
        XCTAssertEqual(DrawnChart(chart([5, 5])).domain, 0...5)
        XCTAssertEqual(DrawnChart(chart([0, 0])).domain, 0...1)
        XCTAssertEqual(DrawnChart(chart([-3, 4])).domain, -3...4)
        XCTAssertEqual(DrawnChart(chart([30.1, 38.2])).domain, 0...40)
        XCTAssertEqual(DrawnChart(chart([-0.4, 1284])).domain, -0.4...2000)
    }

    func testABigTableDrawsItsFirstRowsQuicklyWithAndNMore() {
        let rows = (0..<3000).map { row in (0..<8).map { "r\(row)c\($0)" } }
        let started = Date()
        let table = TableView(title: "Big", columns: (0..<8).map { "C\($0)" }, rows: rows, spokenRows: rows)
        XCTAssertEqual(table.capped.shown.count, 200)
        XCTAssertEqual(table.capped.moreLine("row", "rows"), "…and 2,800 more rows.")
        XCTAssertNotNil(render(table))
        XCTAssertLessThan(Date().timeIntervalSince(started), 2, "a 3,000-row table froze the screen")
    }

    func testAChartWithThousandsOfPointsIsCut() {
        let big = DrawnChart(chart((0..<5000).map { Double($0) }))
        XCTAssertEqual(big.points.count, 500)
        XCTAssertEqual(big.note, "Only the first 500 points are drawn.")
    }

    func testThousandsOfWidgetsAreCutToAHundred() {
        let stats = (0..<5000).map { DashboardWidget.stat(.init(label: "S\($0)", value: "\($0)")) }
        let started = Date()
        XCTAssertNotNil(render(DashboardView(dashboard: Dashboard(title: "Many", subtitle: nil, widgets: stats),
                                             itemTitle: "Many")))
        XCTAssertLessThan(Date().timeIntervalSince(started), 2, "5,000 tiles froze the screen")
        XCTAssertEqual(Capped(stats, limit: DashboardLimits.widgets).moreLine("part", "parts"),
                       "…and 4,900 more parts.")
        XCTAssertNil(Capped([1, 2], limit: 5).moreLine("part", "parts"))
    }
}
