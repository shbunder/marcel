import SwiftUI

/// Marcel's reply as plain formatted text across the full width (ADR-007; `MarkdownView(parts:style: .chat)`): the
/// system font with Dynamic Type, real bold and italic, inline code, links in the text (only http(s), opened through
/// `openURL` when tapped), headings, lists, quotes, code blocks and tables. No bubble and no avatar. The same parser
/// and safety rules as the pixel style (`ItemMarkdown`): HTML stays words, nothing opens by itself, tables are capped.
struct ChatMarkdownBody: View {
    var parts: [ItemMarkdown.Part]

    @Environment(\.colorScheme) private var scheme

    var body: some View {
        let palette = ChatPalette(scheme)
        VStack(alignment: .leading, spacing: 8) {
            ForEach(Array(parts.enumerated()), id: \.offset) { _, part in
                block(part, palette: palette)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    @ViewBuilder private func block(_ part: ItemMarkdown.Part, palette: ChatPalette) -> some View {
        switch part.block {
        case .heading(let level, let text):
            Text(text)
                .font(level <= 1 ? .title3.weight(.bold) : level == 2 ? .headline : .subheadline.weight(.bold))
                .foregroundStyle(palette.ink.color)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.top, 2)
                .accessibilityAddTraits(.isHeader)
        case .paragraph(let text):
            Self.text(part.spans, plain: text, palette: palette)
                .foregroundStyle(palette.ink.color)
                .fixedSize(horizontal: false, vertical: true)
        case .listItem(let marker, let text, let depth):
            HStack(alignment: .firstTextBaseline, spacing: 6) {
                Text(marker.trimmingCharacters(in: .whitespaces))
                    .font(.body)
                    .foregroundStyle(palette.muted.color)
                    .accessibilityHidden(marker.hasPrefix("•"))
                Self.text(part.spans, plain: text, palette: palette)
                    .foregroundStyle(palette.ink.color)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .padding(.leading, CGFloat(depth) * 18)
        case .quote(let text):
            Self.text(part.spans, plain: text, palette: palette)
                .foregroundStyle(palette.muted.color)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.leading, 10)
                .overlay(alignment: .leading) {
                    Rectangle().fill(palette.line.color).frame(width: 3)
                }
        case .code(let text):
            Text(text)
                .font(.system(.footnote, design: .monospaced))
                .foregroundStyle(palette.ink.color)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(10)
                .background(palette.well.color, in: RoundedRectangle(cornerRadius: 8))
                .overlay(RoundedRectangle(cornerRadius: 8).strokeBorder(palette.line.color, lineWidth: 1))
        case .table(let header, let rows):
            ChatTable(header: header, rows: rows)
        case .rule:
            Rectangle().fill(palette.line.color).frame(height: 1)
                .accessibilityHidden(true)
        }
    }

    /// One paragraph, list item or quote with its inline style, as a single `Text` (VoiceOver reads it and offers its
    /// links). Body size follows Dynamic Type.
    static func text(_ spans: [ItemMarkdown.Span], plain: String, palette: ChatPalette) -> Text {
        guard !spans.isEmpty else { return Text(plain).font(.body) }
        return Text(attributed(spans, palette: palette))
    }

    static func attributed(_ spans: [ItemMarkdown.Span], palette: ChatPalette) -> AttributedString {
        var result = AttributedString()
        for span in spans {
            var piece = AttributedString(span.text)
            var font = Font.body
            if span.code { font = .system(.body, design: .monospaced) }
            if span.bold { font = font.bold() }
            if span.italic { font = font.italic() }
            piece.font = font
            if let url = span.link {
                piece.link = url
                piece.foregroundColor = palette.link.color
                piece.underlineStyle = .single
            }
            result += piece
        }
        return result
    }
}

/// A table in a reply: a header row in bold, thin rules, at most `MarkdownLimits.tableRows` rows (the rest counted).
/// Wider than the screen, it scrolls sideways; at accessibility text sizes each row becomes a "column: value" card.
struct ChatTable: View {
    var header: [String]
    var rows: [[String]]

    @Environment(\.colorScheme) private var scheme
    @Environment(\.dynamicTypeSize) private var dynamicType

    var shown: [[String]] { Array(rows.prefix(MarkdownLimits.tableRows)) }
    var more: Int { max(0, rows.count - MarkdownLimits.tableRows) }

    private func cell(_ row: [String], _ column: Int) -> String { column < row.count ? row[column] : "" }

    func spoken(_ row: [String]) -> String {
        header.indices.map { "\(header[$0]): \(cell(row, $0).isEmpty ? "empty" : cell(row, $0))" }
            .joined(separator: ", ")
    }

    var body: some View {
        let palette = ChatPalette(scheme)
        VStack(alignment: .leading, spacing: 4) {
            if dynamicType.isAccessibilitySize {
                ForEach(shown.indices, id: \.self) { index in
                    VStack(alignment: .leading, spacing: 2) {
                        ForEach(header.indices, id: \.self) { column in
                            Text("\(header[column]): \(cell(shown[index], column))")
                                .font(.subheadline)
                                .foregroundStyle(palette.ink.color)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(8)
                    .overlay(RoundedRectangle(cornerRadius: 8).strokeBorder(palette.line.color, lineWidth: 1))
                    .accessibilityElement(children: .ignore)
                    .accessibilityLabel(spoken(shown[index]))
                }
            } else {
                ViewThatFits(in: .horizontal) {
                    grid(palette)
                    ScrollView(.horizontal) { grid(palette) }
                }
            }
            if more > 0 {
                Text("…and \(more.formatted(.number.locale(Locale(identifier: "en_GB")))) more \(more == 1 ? "row" : "rows").")
                    .font(.footnote)
                    .foregroundStyle(palette.muted.color)
            }
        }
    }

    private func grid(_ palette: ChatPalette) -> some View {
        Grid(alignment: .leading, horizontalSpacing: 14, verticalSpacing: 4) {
            GridRow {
                ForEach(header.indices, id: \.self) { column in
                    Text(header[column])
                        .font(.footnote.weight(.bold))
                        .foregroundStyle(palette.muted.color)
                        .fixedSize()
                }
            }
            .accessibilityHidden(true)
            Rectangle().fill(palette.line.color).frame(height: 1).gridCellUnsizedAxes(.horizontal)
                .accessibilityHidden(true)
            ForEach(shown.indices, id: \.self) { index in
                GridRow {
                    ForEach(header.indices, id: \.self) { column in
                        Text(cell(shown[index], column))
                            .font(.subheadline)
                            .foregroundStyle(palette.ink.color)
                            .fixedSize()
                    }
                }
                .accessibilityElement(children: .ignore)
                .accessibilityLabel(spoken(shown[index]))
                Rectangle().fill(palette.line.color).frame(height: 1).gridCellUnsizedAxes(.horizontal)
                    .accessibilityHidden(true)
            }
        }
    }
}
