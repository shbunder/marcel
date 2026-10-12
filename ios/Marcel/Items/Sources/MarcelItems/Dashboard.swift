import Foundation

/// A dashboard or a project board (`contracts/dashboard.schema.json`), read from an item's JSON and drawn natively.
/// Workers write this JSON and the hub checks it, but the app never trusts it: anything that does not fit the schema
/// is a `DashboardProblem` in plain words, never a crash.
public struct Dashboard: Equatable, Sendable {
    public var title: String
    public var subtitle: String?
    public var widgets: [DashboardWidget]

    /// Reads a dashboard, or says in plain words why it cannot.
    public static func parse(_ data: Data) -> Result<Dashboard, DashboardProblem> {
        do {
            let raw = try JSONDecoder().decode(Raw.self, from: data)
            return .success(try raw.checked())
        } catch let problem as DashboardProblem {
            return .failure(problem)
        } catch let error as DecodingError {
            return .failure(DashboardProblem(error))
        } catch {
            return .failure(DashboardProblem(reason: "it could not be read"))
        }
    }

    private struct Raw: Decodable {
        var version: Int
        var title: String
        var subtitle: String?
        var widgets: [DashboardWidget]

        func checked() throws -> Dashboard {
            guard version == 1 else {
                throw DashboardProblem(reason: "it is in format version \(version), and the app reads version 1")
            }
            guard !widgets.isEmpty else { throw DashboardProblem(reason: "it has nothing in it") }
            for widget in widgets { try widget.check(topLevel: true) }
            return Dashboard(title: title, subtitle: subtitle, widgets: widgets)
        }
    }
}

/// Why a dashboard cannot be drawn.
public struct DashboardProblem: Error, Equatable, Sendable {
    public var reason: String

    public init(reason: String) {
        self.reason = reason
    }

    init(_ error: DecodingError) {
        func path(_ context: DecodingError.Context, _ key: CodingKey? = nil) -> String {
            let names = (context.codingPath + (key.map { [$0] } ?? [])).compactMap { key in
                key.intValue == nil ? key.stringValue : nil
            }
            return names.last.map { "“\($0)”" } ?? "a part"
        }
        switch error {
        case .keyNotFound(let key, let context): reason = "\(path(context, key)) is missing"
        case .typeMismatch(_, let context), .valueNotFound(_, let context): reason = "\(path(context)) has the wrong kind of value"
        case .dataCorrupted(let context):
            reason = context.codingPath.isEmpty ? "it isn't valid JSON" : "\(path(context)) has a value the app doesn’t know"
        @unknown default: reason = "it could not be read"
        }
    }

    /// What the viewer says.
    public var message: String {
        "This can't be drawn: \(reason). Ask Marcel to make it again."
    }
}

public enum DashboardWidget: Equatable, Sendable, Decodable {
    case stat(Stat)
    case list(ListWidget)
    case checklist(Checklist)
    case table(Table)
    case chart(Chart)
    case note(Note)
    case section(Section)

    public struct Stat: Equatable, Sendable, Decodable {
        public enum Tone: String, Sendable, Decodable { case neutral, good, warning, bad }
        public var label: String
        public var value: String
        public var delta: String?
        public var tone: Tone?
    }

    public struct ListWidget: Equatable, Sendable, Decodable {
        public struct Item: Equatable, Sendable, Decodable {
            public var text: String
            public var detail: String?
            public var url: String?
        }
        public var title: String?
        public var items: [Item]
    }

    public struct Checklist: Equatable, Sendable, Decodable {
        public struct Item: Equatable, Sendable, Decodable {
            public var text: String
            public var done: Bool
        }
        public var title: String?
        public var items: [Item]
    }

    public struct Table: Equatable, Sendable, Decodable {
        public var title: String?
        public var columns: [String]
        public var rows: [[Cell]]
    }

    /// A table cell: text, a number, or empty.
    public enum Cell: Equatable, Sendable, Decodable {
        case text(String)
        case number(Double)
        case empty

        public init(from decoder: any Decoder) throws {
            let container = try decoder.singleValueContainer()
            if container.decodeNil() {
                self = .empty
            } else if let number = try? container.decode(Double.self) {
                self = .number(number)
            } else {
                self = .text(try container.decode(String.self))
            }
        }

        public var text: String {
            switch self {
            case .text(let text): text
            case .number(let number): Chart.format(number)
            case .empty: "—"
            }
        }

        public var spoken: String {
            self == .empty ? "empty" : text
        }
    }

    public struct Chart: Equatable, Sendable, Decodable {
        public enum Kind: String, Sendable, Decodable { case bar, line }
        public struct Series: Equatable, Sendable, Decodable {
            public var name: String
            public var values: [Double?]
        }
        public var kind: Kind
        public var title: String?
        public var unit: String?
        public var labels: [String]
        public var series: [Series]

        /// "38.2", "412", "1,284" — numbers as the chart and its spoken summary show them.
        public static func format(_ number: Double) -> String {
            number.formatted(.number.precision(.fractionLength(0...2)).locale(Locale(identifier: "en_GB")))
        }
    }

    public struct Note: Equatable, Sendable, Decodable {
        public var text: String
        public var author: String?
        public var at: String?
    }

    public struct Section: Equatable, Sendable, Decodable {
        public var title: String
        public var emptyText: String?
        public var widgets: [DashboardWidget]

        enum CodingKeys: String, CodingKey {
            case title, widgets
            case emptyText = "empty_text"
        }
    }

    private enum Key: String, CodingKey { case type }

    public init(from decoder: any Decoder) throws {
        let type = try decoder.container(keyedBy: Key.self).decode(String.self, forKey: .type)
        switch type {
        case "stat": self = .stat(try Stat(from: decoder))
        case "list": self = .list(try ListWidget(from: decoder))
        case "checklist": self = .checklist(try Checklist(from: decoder))
        case "table": self = .table(try Table(from: decoder))
        case "chart": self = .chart(try Chart(from: decoder))
        case "note": self = .note(try Note(from: decoder))
        case "section": self = .section(try Section(from: decoder))
        default: throw DashboardProblem(reason: "it has a part of an unknown kind (“\(type)”)")
        }
    }

    /// The rules JSON decoding alone does not check.
    func check(topLevel: Bool) throws {
        switch self {
        case .section(let section):
            guard topLevel else { throw DashboardProblem(reason: "a section sits inside another section") }
            for widget in section.widgets { try widget.check(topLevel: false) }
        case .chart(let chart):
            guard !chart.labels.isEmpty, !chart.series.isEmpty else {
                throw DashboardProblem(reason: "a chart has no data")
            }
            for series in chart.series where series.values.count != chart.labels.count {
                throw DashboardProblem(reason: "the chart line “\(series.name)” has \(series.values.count) "
                    + "values for \(chart.labels.count) labels")
            }
        case .table(let table):
            guard !table.columns.isEmpty else { throw DashboardProblem(reason: "a table has no columns") }
            if table.rows.contains(where: { $0.count > table.columns.count }) {
                throw DashboardProblem(reason: "a table row has more cells than the table has columns")
            }
        case .stat, .list, .checklist, .note: break
        }
    }
}

/// How much of a dashboard the phone draws. The hub checks dashboards against the schema (at most 30 widgets), but
/// the app never trusts that: a huge table or chart would freeze the screen, so each is cut short with an
/// "…and N more" line.
public enum DashboardLimits {
    public static let widgets = 100
    public static let rows = 200
    public static let chartLabels = 500
    /// Values beyond this cannot be drawn on a chart's axis (Swift Charts turns them into whole numbers and stops the
    /// app on 1e308); they are left out and the chart says so.
    public static let largestValue = 1e15
}

/// The first `limit` things, and how many more there were.
public struct Capped<Element> {
    public var shown: [Element]
    public var more: Int

    public init(_ all: [Element], limit: Int) {
        shown = Array(all.prefix(limit))
        more = max(0, all.count - limit)
    }

    /// "…and 2,800 more rows."; nil when nothing was cut.
    public func moreLine(_ singular: String, _ plural: String) -> String? {
        guard more > 0 else { return nil }
        let count = more.formatted(.number.locale(Locale(identifier: "en_GB")))
        return "…and \(count) more \(more == 1 ? singular : plural)."
    }
}

/// A chart as it is drawn: at most `DashboardLimits.chartLabels` labels, only values that can be drawn, and an axis
/// range worked out here rather than by Swift Charts.
public struct DrawnChart: Equatable, Sendable {
    public struct Point: Identifiable, Equatable, Sendable {
        public var id: String { "\(series)|\(index)" }
        public var series: String
        public var index: Int
        public var label: String
        public var value: Double
    }

    public var points: [Point]
    /// Values left out: not a number, infinite, or too large to draw.
    public var leftOut: Int
    /// Labels past the limit.
    public var moreLabels: Int
    public var domain: ClosedRange<Double>

    public init(_ chart: DashboardWidget.Chart) {
        let labels = Capped(chart.labels, limit: DashboardLimits.chartLabels)
        var points: [Point] = []
        var leftOut = 0
        for series in chart.series {
            for (index, label) in labels.shown.enumerated() where index < series.values.count {
                guard let value = series.values[index] else { continue }
                guard value.isFinite, abs(value) <= DashboardLimits.largestValue else {
                    leftOut += 1
                    continue
                }
                points.append(Point(series: series.name, index: index, label: label, value: value))
            }
        }
        self.points = points
        self.leftOut = leftOut
        moreLabels = labels.more
        let low = min(0, points.map(\.value).min() ?? 0)
        let high = max(0, points.map(\.value).max() ?? 0)
        domain = low == high ? low...(low + 1) : Self.round(low, down: true)...Self.round(high, down: false)
    }

    /// Rounds an axis end outward to a whole step of its own size (38.2 → 40, −3 → −3, 1,284 → 2,000), as an axis
    /// would.
    static func round(_ value: Double, down: Bool) -> Double {
        guard value != 0 else { return 0 }
        let step = pow(10, floor(log10(abs(value))))
        return (down ? floor(value / step) : ceil(value / step)) * step
    }

    /// The plain-words line under the chart, when something was left out.
    public var note: String? {
        var parts: [String] = []
        if leftOut > 0 {
            parts.append(leftOut == 1 ? "1 value was left out: it is too large to draw."
                         : "\(leftOut) values were left out: they are too large to draw.")
        }
        if moreLabels > 0 {
            parts.append("Only the first \(DashboardLimits.chartLabels) points are drawn.")
        }
        return parts.isEmpty ? nil : parts.joined(separator: " ")
    }
}
