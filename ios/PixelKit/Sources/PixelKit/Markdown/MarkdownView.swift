import SwiftUI

/// A Markdown document, natively (`ItemMarkdown`): headings in the label font; paragraphs, lists and quotes in the body
/// font with bold, italic, inline code and links; code blocks; tables (scrolling sideways when wider than the screen,
/// at most `MarkdownLimits.tableRows` rows); and each block's web links as buttons under it. Used by the item viewer,
/// Marcel's notebook and (ADR-007) Marcel's replies, with the same rules everywhere: a link opens only when it is
/// tapped, and only an http(s) one; HTML stays words.
public struct MarkdownView: View {
    var parts: [ItemMarkdown.Part]

    @Environment(\.colorScheme) private var scheme
    @Environment(\.dynamicTypeSize) private var dynamicType
    @Environment(\.openURL) private var openURL

    public init(parts: [ItemMarkdown.Part]) {
        self.parts = parts
    }

    public var body: some View {
        let theme = PixelTheme(scheme)
        VStack(alignment: .leading, spacing: PixelMetrics.steps(5)) {
            if parts.isEmpty {
                PixelText("This document is empty.", role: .body).foregroundStyle(theme.muted.color)
            }
            ForEach(Array(parts.enumerated()), id: \.offset) { _, part in
                block(part, theme: theme)
                ForEach(Array(part.links.enumerated()), id: \.offset) { _, link in
                    Button { openURL(link.url) } label: {
                        PixelText("Open \(link.title) →", role: .smallLabel)
                            .foregroundStyle(ProjectChip.Tone.blue.color(in: theme).color)
                            .fixedSize(horizontal: false, vertical: true)
                            .frame(minHeight: PixelMetrics.minTapTarget, alignment: .leading)
                            .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel("Open \(link.title) in Safari")
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    @ViewBuilder private func block(_ part: ItemMarkdown.Part, theme: PixelTheme) -> some View {
        switch part.block {
        case .heading(let level, let text):
            PixelText(text, role: level <= 1 ? .title : level == 2 ? .label : .smallLabel)
                .foregroundStyle(theme.text.color)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.top, level <= 2 ? PixelMetrics.steps(2) : 0)
                .accessibilityAddTraits(.isHeader)
        case .paragraph(let text):
            inline(part.spans, text, theme: theme)
                .foregroundStyle(theme.text.color)
        case .listItem(let marker, let text, let depth):
            HStack(alignment: .firstTextBaseline, spacing: PixelMetrics.steps(3)) {
                PixelText(marker.trimmingCharacters(in: .whitespaces), role: .body)
                    .foregroundStyle(theme.muted.color)
                    .accessibilityHidden(marker.hasPrefix("•"))
                inline(part.spans, text, theme: theme)
                    .foregroundStyle(theme.text.color)
            }
            .padding(.leading, CGFloat(depth) * PixelMetrics.steps(8))
            .accessibilityElement(children: .combine)
        case .quote(let text):
            inline(part.spans, text, theme: theme)
                .foregroundStyle(theme.muted.color)
                .padding(.leading, PixelMetrics.steps(5))
                .overlay(alignment: .leading) {
                    Rectangle().fill(theme.muted.color).frame(width: PixelMetrics.thinBorder)
                }
        case .code(let text):
            Text(text)
                .font(.system(.footnote, design: .monospaced))
                .foregroundStyle(theme.text.color)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(PixelMetrics.steps(4))
                .background(theme.bar.color)
        case .table(let header, let rows):
            MarkdownTable(header: header, rows: rows)
        case .rule:
            Rectangle().fill(theme.line.color).frame(height: PixelMetrics.thinBorder)
                .accessibilityHidden(true)
        }
    }

    /// Body text with its inline style. VoiceOver reads the plain words; the links are also buttons under the block.
    @ViewBuilder private func inline(_ spans: [ItemMarkdown.Span], _ text: String, theme: PixelTheme) -> some View {
        if spans.allSatisfy({ !$0.bold && !$0.italic && !$0.code && $0.link == nil }) {
            PixelText(text, role: .body)
                .fixedSize(horizontal: false, vertical: true)
        } else {
            let size = PixelTextRole.body.size(for: dynamicType)
            MarkdownInline.text(spans, size: size, theme: theme)
                .lineSpacing(size * 0.15)
                .fixedSize(horizontal: false, vertical: true)
                .accessibilityLabel(Text(verbatim: text))
        }
    }
}

/// Builds one `Text` from styled spans in the body font: bold uses the font's bold weight; italic is drawn in the rose
/// tone, because Pixelify Sans has no slanted face and iOS does not fake one; inline code uses the system monospaced
/// font in teal; a link is underlined in the link colour and opens through `openURL` when tapped.
enum MarkdownInline {
    static func text(_ spans: [ItemMarkdown.Span], size: CGFloat, theme: PixelTheme) -> Text {
        var result = Text(verbatim: "")
        for span in spans {
            result = Text("\(result)\(piece(span, size: size, theme: theme))")
        }
        return result
    }

    private static func piece(_ span: ItemMarkdown.Span, size: CGFloat, theme: PixelTheme) -> Text {
        if span.code {
            return Text(verbatim: span.text)
                .font(.system(size: size * 0.88, design: .monospaced))
                .foregroundStyle(ProjectChip.Tone.teal.color(in: theme).color)
        }
        var text: Text
        if let url = span.link {
            var words = AttributedString(span.text)
            words.link = url
            words.font = .custom(PixelFonts.bodyFace, fixedSize: size)
            words.foregroundColor = ProjectChip.Tone.blue.color(in: theme).color
            words.underlineStyle = .single
            text = Text(words)
        } else {
            text = PixelGlyphFallback.text(span.text, face: .pixelify, size: size)
        }
        if span.bold { text = text.fontWeight(.bold) }
        if span.italic { text = text.italic().foregroundStyle(ProjectChip.Tone.rose.color(in: theme).color) }
        return text
    }
}

/// How much of a Markdown table is drawn: like a dashboard table, the rest is counted, not drawn.
public enum MarkdownLimits {
    public static let tableRows = 200
}

/// A Markdown table: a header row and cells in the detail font. Wider than the screen, it scrolls sideways; at
/// accessibility text sizes each row becomes a card of "column: value" lines. VoiceOver reads a row as one line.
struct MarkdownTable: View {
    var header: [String]
    var rows: [[String]]

    @Environment(\.colorScheme) private var scheme
    @Environment(\.dynamicTypeSize) private var dynamicType

    private func cell(_ row: [String], _ column: Int) -> String { column < row.count ? row[column] : "" }

    var shown: [[String]] { Array(rows.prefix(MarkdownLimits.tableRows)) }
    var more: Int { max(0, rows.count - MarkdownLimits.tableRows) }

    /// "Offer: Current, Per year: €1,496"
    func spoken(_ row: [String]) -> String {
        header.indices.map { "\(header[$0]): \(cell(row, $0).isEmpty ? "empty" : cell(row, $0))" }
            .joined(separator: ", ")
    }

    var body: some View {
        let theme = PixelTheme(scheme)
        VStack(alignment: .leading, spacing: PixelMetrics.steps(3)) {
            if dynamicType.isAccessibilitySize {
                ForEach(shown.indices, id: \.self) { index in
                    VStack(alignment: .leading, spacing: PixelMetrics.steps(1)) {
                        ForEach(header.indices, id: \.self) { column in
                            PixelText("\(header[column]): \(cell(shown[index], column))", role: .detail)
                                .foregroundStyle(theme.text.color)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(PixelMetrics.steps(4))
                    .pixelBox(fill: theme.card, line: theme.line, shadow: nil, border: PixelMetrics.thinBorder)
                    .accessibilityElement(children: .ignore)
                    .accessibilityLabel(spoken(shown[index]))
                }
            } else {
                // A table that fits is drawn as it is; a wider one scrolls sideways inside its box.
                ViewThatFits(in: .horizontal) {
                    grid(theme).frame(maxWidth: .infinity, alignment: .leading)
                    ScrollView(.horizontal) { grid(theme) }
                }
                .pixelBox(fill: theme.card, line: theme.line, shadow: nil, border: PixelMetrics.thinBorder)
            }
            if more > 0 {
                PixelText("…and \(more.formatted(.number.locale(Locale(identifier: "en_GB")))) more \(more == 1 ? "row" : "rows").",
                          role: .detail)
                    .foregroundStyle(theme.muted.color)
            }
        }
    }

    private func grid(_ theme: PixelTheme) -> some View {
        Grid(alignment: .leading, horizontalSpacing: PixelMetrics.steps(6), verticalSpacing: PixelMetrics.steps(3)) {
            GridRow {
                ForEach(header.indices, id: \.self) { column in
                    PixelText(header[column], role: .smallLabel)
                        .foregroundStyle(theme.muted.color)
                        .fixedSize()
                }
            }
            .accessibilityHidden(true)
            Rectangle().fill(theme.line.color).frame(height: PixelMetrics.thinBorder)
                .gridCellUnsizedAxes(.horizontal)
                .accessibilityHidden(true)
            ForEach(shown.indices, id: \.self) { index in
                GridRow {
                    ForEach(header.indices, id: \.self) { column in
                        PixelText(cell(shown[index], column), role: .detail)
                            .foregroundStyle(theme.text.color)
                            .fixedSize()
                    }
                }
                .accessibilityElement(children: .ignore)
                .accessibilityLabel(spoken(shown[index]))
            }
        }
        .padding(PixelMetrics.steps(5))
    }
}
