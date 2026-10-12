import Charts
import PixelKit
import SwiftUI

/// A dashboard or a project board, drawn natively from its JSON: every widget kind in
/// `contracts/dashboard.schema.json`, top to bottom. Charts use Swift Charts.
struct DashboardView: View {
    var dashboard: Dashboard
    /// The item's own title; the dashboard's is shown only when it says something else.
    var itemTitle: String

    @Environment(\.colorScheme) private var scheme

    var body: some View {
        let theme = PixelTheme(scheme)
        VStack(alignment: .leading, spacing: ItemsMetrics.gap) {
            if dashboard.title != itemTitle || dashboard.subtitle != nil {
                VStack(alignment: .leading, spacing: PixelMetrics.steps(2)) {
                    if dashboard.title != itemTitle {
                        PixelText(dashboard.title, role: .label)
                            .foregroundStyle(theme.text.color)
                            .fixedSize(horizontal: false, vertical: true)
                            .accessibilityAddTraits(.isHeader)
                    }
                    if let subtitle = dashboard.subtitle {
                        PixelText(subtitle, role: .detail)
                            .foregroundStyle(theme.muted.color)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
            let widgets = Capped(dashboard.widgets, limit: DashboardLimits.widgets)
            ForEach(Array(widgets.shown.enumerated()), id: \.offset) { _, widget in
                WidgetView(widget: widget)
            }
            MoreLine(text: widgets.moreLine("part", "parts"))
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// "…and 2,800 more rows.", under anything the phone cut short.
struct MoreLine: View {
    var text: String?
    @Environment(\.colorScheme) private var scheme

    var body: some View {
        if let text {
            PixelText(text, role: .detail)
                .foregroundStyle(PixelTheme(scheme).muted.color)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}

struct WidgetView: View {
    var widget: DashboardWidget

    var body: some View {
        switch widget {
        case .stat(let stat): StatView(stat: stat)
        case .list(let list): ListWidgetView(list: list)
        case .checklist(let checklist): ChecklistView(checklist: checklist)
        case .table(let table): TableView(title: table.title, columns: table.columns,
                                          rows: table.rows.map { $0.map(\.text) },
                                          spokenRows: table.rows.map { $0.map(\.spoken) })
        case .chart(let chart): ChartView(chart: chart)
        case .note(let note): NoteView(note: note)
        case .section(let section): SectionView(section: section)
        }
    }
}

/// A widget's small title ("Largest volumes"), a header for VoiceOver.
struct WidgetTitle: View {
    var text: String
    @Environment(\.colorScheme) private var scheme

    var body: some View {
        PixelText(text, role: .smallLabel)
            .foregroundStyle(PixelTheme(scheme).muted.color)
            .fixedSize(horizontal: false, vertical: true)
            .accessibilityAddTraits(.isHeader)
    }
}

/// A titled group ("Waiting on you"), with its own empty line when nothing is in it.
struct SectionView: View {
    var section: DashboardWidget.Section
    @Environment(\.colorScheme) private var scheme

    var body: some View {
        let theme = PixelTheme(scheme)
        VStack(alignment: .leading, spacing: PixelMetrics.steps(4)) {
            PixelText(section.title, role: .label)
                .foregroundStyle(theme.text.color)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.bottom, PixelMetrics.steps(2))
                .overlay(alignment: .bottom) {
                    Rectangle().fill(theme.line.color).frame(height: PixelMetrics.thinBorder)
                }
                .accessibilityAddTraits(.isHeader)
            if section.widgets.isEmpty {
                PixelText(section.emptyText ?? "Nothing here yet.", role: .detail)
                    .foregroundStyle(theme.muted.color)
                    .fixedSize(horizontal: false, vertical: true)
            }
            let widgets = Capped(section.widgets, limit: DashboardLimits.widgets)
            ForEach(Array(widgets.shown.enumerated()), id: \.offset) { _, widget in
                WidgetView(widget: widget)
            }
            MoreLine(text: widgets.moreLine("part", "parts"))
        }
        .padding(.top, PixelMetrics.steps(2))
    }
}

/// One headline number: "Disk free 412 GB, −12 GB this week".
struct StatView: View {
    var stat: DashboardWidget.Stat
    @Environment(\.colorScheme) private var scheme

    var toneWords: String? {
        switch stat.tone {
        case .good?: "good"
        case .warning?: "a warning"
        case .bad?: "bad"
        case .neutral?, nil: nil
        }
    }

    var body: some View {
        let theme = PixelTheme(scheme)
        let ink: PixelColor = switch stat.tone {
        case .good?: ProjectChip.Tone.teal.color(in: theme)
        case .warning?: ProjectChip.Tone.gold.color(in: theme)
        case .bad?: ProjectChip.Tone.rose.color(in: theme)
        case .neutral?, nil: theme.text
        }
        VStack(alignment: .leading, spacing: PixelMetrics.steps(2)) {
            PixelText(stat.label, role: .smallLabel)
                .foregroundStyle(theme.muted.color)
                .fixedSize(horizontal: false, vertical: true)
            PixelText(stat.value, role: .title)
                .foregroundStyle(ink.color)
                .fixedSize(horizontal: false, vertical: true)
            if let delta = stat.delta {
                PixelText(delta, role: .detail)
                    .foregroundStyle(theme.muted.color)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(PixelMetrics.padding)
        .pixelBox(fill: theme.card, line: theme.line, shadow: nil, border: PixelMetrics.thinBorder)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel([stat.label, stat.value, stat.delta, toneWords.map { "This is \($0)" }]
            .compactMap { $0 }.joined(separator: ", "))
    }
}

struct ListWidgetView: View {
    var list: DashboardWidget.ListWidget
    @Environment(\.colorScheme) private var scheme
    @Environment(\.openURL) private var openURL

    var body: some View {
        let theme = PixelTheme(scheme)
        VStack(alignment: .leading, spacing: PixelMetrics.steps(3)) {
            if let title = list.title { WidgetTitle(text: title) }
            let items = Capped(list.items, limit: DashboardLimits.rows)
            ForEach(Array(items.shown.enumerated()), id: \.offset) { _, item in
                let link = item.url.flatMap(URL.init(string:)).flatMap(ItemMarkdown.webLink)
                HStack(alignment: .firstTextBaseline, spacing: PixelMetrics.steps(3)) {
                    PixelText("•", role: .body).foregroundStyle(theme.muted.color)
                        .accessibilityHidden(true)
                    VStack(alignment: .leading, spacing: PixelMetrics.steps(1)) {
                        PixelText(item.text, role: .body)
                            .foregroundStyle(theme.text.color)
                            .fixedSize(horizontal: false, vertical: true)
                        if let detail = item.detail {
                            PixelText(detail, role: .detail)
                                .foregroundStyle(theme.muted.color)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                    Spacer(minLength: 0)
                    if let link {
                        Button("Open") { openURL(link) }
                            .pixelText(.smallLabel)
                            .frame(minWidth: PixelMetrics.minTapTarget, minHeight: PixelMetrics.minTapTarget)
                            .accessibilityLabel("Open \(item.text) in Safari")
                    }
                }
                .accessibilityElement(children: link == nil ? .ignore : .contain)
                .accessibilityLabel([item.text, item.detail].compactMap { $0 }.joined(separator: ", "))
            }
            MoreLine(text: items.moreLine("line", "lines"))
        }
    }
}

struct ChecklistView: View {
    var checklist: DashboardWidget.Checklist
    @Environment(\.colorScheme) private var scheme

    var body: some View {
        let theme = PixelTheme(scheme)
        VStack(alignment: .leading, spacing: PixelMetrics.steps(3)) {
            if let title = checklist.title { WidgetTitle(text: title) }
            let items = Capped(checklist.items, limit: DashboardLimits.rows)
            ForEach(Array(items.shown.enumerated()), id: \.offset) { _, item in
                HStack(alignment: .firstTextBaseline, spacing: PixelMetrics.steps(4)) {
                    ZStack {
                        Rectangle().strokeBorder(theme.line.color, lineWidth: PixelMetrics.thinBorder)
                            .frame(width: 18, height: 18)
                        if item.done {
                            PixelText("✓", role: .detail).foregroundStyle(theme.text.color)
                        }
                    }
                    .alignmentGuide(.firstTextBaseline) { $0[.bottom] - 3 }
                    PixelText(item.text, role: .body)
                        .foregroundStyle(item.done ? theme.muted.color : theme.text.color)
                        .fixedSize(horizontal: false, vertical: true)
                    Spacer(minLength: 0)
                }
                .accessibilityElement(children: .ignore)
                .accessibilityLabel("\(item.done ? "Done" : "To do"): \(item.text)")
            }
            MoreLine(text: items.moreLine("line", "lines"))
        }
    }
}

/// A table: a header row and the rows under it. At accessibility text sizes each row becomes a small card of
/// "column: value" lines, so nothing is squeezed. Markdown tables use it too.
struct TableView: View {
    var title: String?
    var columns: [String]
    var rows: [[String]]
    var spokenRows: [[String]]

    @Environment(\.colorScheme) private var scheme
    @Environment(\.dynamicTypeSize) private var dynamicType

    func cell(_ row: [String], _ column: Int) -> String {
        column < row.count ? row[column] : ""
    }

    /// The rows drawn: at most `DashboardLimits.rows`.
    var capped: Capped<[String]> { Capped(rows, limit: DashboardLimits.rows) }

    var body: some View {
        let theme = PixelTheme(scheme)
        let rows = capped.shown
        VStack(alignment: .leading, spacing: PixelMetrics.steps(3)) {
            if let title { WidgetTitle(text: title) }
            if dynamicType.isAccessibilitySize {
                ForEach(rows.indices, id: \.self) { index in
                    VStack(alignment: .leading, spacing: PixelMetrics.steps(1)) {
                        ForEach(columns.indices, id: \.self) { column in
                            PixelText("\(columns[column]): \(cell(rows[index], column))", role: .detail)
                                .foregroundStyle(theme.text.color)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(PixelMetrics.steps(4))
                    .pixelBox(fill: theme.card, line: theme.line, shadow: nil, border: PixelMetrics.thinBorder)
                    .accessibilityElement(children: .ignore)
                    .accessibilityLabel(spoken(index))
                }
            } else {
                Grid(alignment: .leading, horizontalSpacing: PixelMetrics.steps(6),
                     verticalSpacing: PixelMetrics.steps(3)) {
                    GridRow {
                        ForEach(columns.indices, id: \.self) { column in
                            PixelText(columns[column], role: .smallLabel)
                                .foregroundStyle(theme.muted.color)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                    .accessibilityHidden(true)
                    Rectangle().fill(theme.line.color).frame(height: PixelMetrics.thinBorder)
                        .gridCellUnsizedAxes(.horizontal)
                        .accessibilityHidden(true)
                    ForEach(rows.indices, id: \.self) { index in
                        GridRow {
                            ForEach(columns.indices, id: \.self) { column in
                                PixelText(cell(rows[index], column), role: .detail)
                                    .foregroundStyle(theme.text.color)
                                    .fixedSize(horizontal: false, vertical: true)
                            }
                        }
                        .accessibilityElement(children: .ignore)
                        .accessibilityLabel(spoken(index))
                    }
                }
                .padding(PixelMetrics.steps(5))
                .frame(maxWidth: .infinity, alignment: .leading)
                .pixelBox(fill: theme.card, line: theme.line, shadow: nil, border: PixelMetrics.thinBorder)
            }
            MoreLine(text: capped.moreLine("row", "rows"))
        }
    }

    /// "Volume: harry_data, Size: 1.1 GB"
    func spoken(_ index: Int) -> String {
        let row = index < spokenRows.count ? spokenRows[index] : rows[index]
        return columns.indices.map { "\(columns[$0]): \(cell(row, $0).isEmpty ? "empty" : cell(row, $0))" }
            .joined(separator: ", ")
    }
}

/// A bar or line chart with Swift Charts, in the project colours. VoiceOver hears every value in words.
struct ChartView: View {
    var chart: DashboardWidget.Chart

    @Environment(\.colorScheme) private var scheme

    /// What is drawn: capped, only drawable values, and the axis range.
    var drawn: DrawnChart { DrawnChart(chart) }

    /// "Bar chart, Growth per week, in GB. plex_media_cache: w37 30.1, w38 33.4, w39 35.9, w40 38.2."
    var spoken: String {
        let kind = chart.kind == .bar ? "Bar chart" : "Line chart"
        let head = [kind, chart.title, chart.unit.map { "in \($0)" }].compactMap { $0 }.joined(separator: ", ")
        let drawn = drawn
        let lines = chart.series.map { series in
            let values = drawn.points.filter { $0.series == series.name }
                .map { "\($0.label) \(DashboardWidget.Chart.format($0.value))" }
            return "\(series.name): \(values.isEmpty ? "no values" : values.joined(separator: ", "))"
        }
        let note = drawn.note.map { String($0.dropLast()) }
        return ([head] + lines + [note].compactMap { $0 }).joined(separator: ". ") + "."
    }

    static func palette(_ theme: PixelTheme) -> [Color] {
        [PixelColor.deep, .teal, .gold, .rose, PixelColor(0x4F6396), PixelColor(0x4E6A38)].map(\.color)
    }

    var body: some View {
        let theme = PixelTheme(scheme)
        let colors = Self.palette(theme)
        let names = chart.series.map(\.name)
        let drawn = drawn
        VStack(alignment: .leading, spacing: PixelMetrics.steps(3)) {
            if let title = chart.title {
                WidgetTitle(text: [title, chart.unit.map { "(\($0))" }].compactMap { $0 }.joined(separator: " "))
            }
            Chart(drawn.points) { point in
                switch chart.kind {
                case .bar:
                    BarMark(x: .value("Label", point.label), y: .value("Value", point.value))
                        .foregroundStyle(by: .value("Series", point.series))
                        .position(by: .value("Series", point.series))
                case .line:
                    LineMark(x: .value("Label", point.label), y: .value("Value", point.value))
                        .foregroundStyle(by: .value("Series", point.series))
                        .lineStyle(StrokeStyle(lineWidth: 3))
                    PointMark(x: .value("Label", point.label), y: .value("Value", point.value))
                        .foregroundStyle(by: .value("Series", point.series))
                        .symbolSize(30)
                }
            }
            // The axis range is worked out here: Swift Charts' own stops the app on values like 1e308.
            .chartYScale(domain: drawn.domain)
            .chartForegroundStyleScale(domain: names, range: Array(colors.prefix(max(1, names.count))))
            .chartLegend(names.count > 1 ? .visible : .hidden)
            .chartXAxis {
                AxisMarks { _ in
                    AxisValueLabel().font(.custom(PixelFonts.bodyFace, size: 12)).foregroundStyle(theme.text.color)
                }
            }
            .chartYAxis {
                AxisMarks { _ in
                    AxisGridLine().foregroundStyle(theme.muted.color.opacity(0.4))
                    AxisValueLabel().font(.custom(PixelFonts.bodyFace, size: 12)).foregroundStyle(theme.text.color)
                }
            }
            .frame(height: 180)
            .padding(PixelMetrics.steps(5))
            .pixelBox(fill: theme.card, line: theme.line, shadow: nil, border: PixelMetrics.thinBorder)
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(spoken)
            MoreLine(text: drawn.note)
                .accessibilityHidden(true)
        }
        .onAppear { PixelFonts.register() }
    }
}

/// Marcel's "what this means" line (`board_note`), like a line of dialogue.
struct NoteView: View {
    var note: DashboardWidget.Note

    @Environment(\.colorScheme) private var scheme

    var byline: String? {
        let when = note.at.flatMap(ItemsJSON.parse).map { date in
            let formatter = DateFormatter()
            formatter.locale = Locale(identifier: "en_GB")
            formatter.timeZone = TimeZone(identifier: "UTC")
            formatter.dateFormat = "d MMM HH:mm"
            return formatter.string(from: date)
        }
        let parts = [note.author, when].compactMap { $0 }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }

    var body: some View {
        let theme = PixelTheme(scheme)
        VStack(alignment: .leading, spacing: PixelMetrics.steps(2)) {
            PixelText(note.text, role: .body)
                .foregroundStyle(theme.text.color)
                .fixedSize(horizontal: false, vertical: true)
            if let byline {
                PixelText(byline, role: .detail)
                    .foregroundStyle(theme.muted.color)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(PixelMetrics.padding)
        .pixelBox(fill: theme.dialog, line: theme.line, shadow: theme.shadow, border: PixelMetrics.thickBorder,
                  offset: PixelMetrics.smallShadow)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Note\(note.author.map { " from \($0)" } ?? ""): \(note.text)")
    }
}
