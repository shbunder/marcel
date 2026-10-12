import Foundation
import MarcelKit

/// The first lines of an item, for its card in the chat. Documents show their opening lines, boards and
/// dashboards their first entries, pull requests their state; anything else what the hub says about it.
public enum ArtifactPreview {
    public static let maxRows = 4

    /// Previews belong to one version: a new version is fetched again.
    public static func key(_ artifact: Artifact) -> String { "\(artifact.id)@\(artifact.currentVersion)" }

    /// Whether the content is worth fetching for a preview (small, readable kinds only).
    public static func fetchesContent(_ artifact: Artifact) -> Bool {
        switch artifact.kind {
        case .doc: artifact.contentType?.hasPrefix("text/markdown") == true || artifact.contentType?.hasPrefix("text/plain") == true
        case .board, .dashboard: true
        default: false
        }
    }

    /// Rows from what the artifact itself says, used until (or instead of) its content.
    public static func summaryRows(_ artifact: Artifact) -> [PreviewRow] {
        switch artifact.kind {
        case .pr:
            guard let pr = artifact.pr else { return [PreviewRow("Pull request")] }
            var rows = [PreviewRow("Pull request #\(pr.number)", value: prState(pr.state.rawValue), emphasised: true)]
            if let checks = pr.checks, checks != .none { rows.append(PreviewRow("Checks", value: checks.rawValue.capitalized)) }
            if let review = pr.review, review != .none {
                rows.append(PreviewRow("Review", value: review.rawValue.replacingOccurrences(of: "_", with: " ").capitalized))
            }
            return rows
        case .link, .branch:
            let host = artifact.url.flatMap { URL(string: $0)?.host() } ?? "a web page"
            return [PreviewRow(artifact.kind == .branch ? "A branch on \(host)" : "A link to \(host)")]
        default:
            var rows: [PreviewRow] = []
            if let by = artifact.updatedBy { rows.append(PreviewRow("Made by", value: by)) }
            rows.append(PreviewRow("Version", value: "\(artifact.currentVersion)"))
            return rows
        }
    }

    /// Rows from the content's bytes; nil when there is nothing readable in them.
    public static func contentRows(_ artifact: Artifact, data: Data) -> [PreviewRow]? {
        switch artifact.kind {
        case .board, .dashboard: return dashboardRows(data)
        case .doc:
            guard let text = String(data: data, encoding: .utf8) else { return nil }
            let lines = ChatMarkdown.render(text).text
                .split(separator: "\n", omittingEmptySubsequences: true)
                .map { $0.trimmingCharacters(in: .whitespaces) }
                .filter { !$0.isEmpty }
            let rows = lines.prefix(maxRows).enumerated().map { PreviewRow($1, emphasised: $0 == 0) }
            return rows.isEmpty ? nil : rows
        default: return nil
        }
    }

    /// A dashboard's first entries (`contracts/dashboard.schema.json`): list and checklist lines, stats, notes. A
    /// board with nothing in it shows each section's "empty" line instead. A project room's pinned board shows more
    /// of them (`limit`).
    public static func dashboardRows(_ data: Data, limit: Int = maxRows) -> [PreviewRow]? {
        guard let root = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let widgets = root["widgets"] as? [[String: Any]] else { return nil }
        var rows: [PreviewRow] = []
        var empties: [PreviewRow] = []
        func visit(_ widget: [String: Any]) {
            switch widget["type"] as? String {
            case "section":
                let inner = widget["widgets"] as? [[String: Any]] ?? []
                if inner.isEmpty, let title = widget["title"] as? String {
                    empties.append(PreviewRow(title, value: widget["empty_text"] as? String))
                }
                inner.forEach(visit)
            case "list", "checklist":
                for entry in widget["items"] as? [[String: Any]] ?? [] {
                    if let text = entry["text"] as? String { rows.append(PreviewRow(text, value: entry["detail"] as? String)) }
                }
            case "stat":
                if let label = widget["label"] as? String {
                    rows.append(PreviewRow(label, value: (widget["value"]).map { "\($0)" }, emphasised: true))
                }
            case "note":
                if let text = widget["text"] as? String { rows.append(PreviewRow(text)) }
            default: break
            }
        }
        widgets.forEach(visit)
        let chosen = rows.isEmpty ? empties : rows
        return chosen.isEmpty ? nil : Array(chosen.prefix(limit))
    }

    private static func prState(_ state: String) -> String {
        switch state {
        case "open": "Open"
        case "draft": "Draft"
        case "merged": "Merged"
        default: "Closed"
        }
    }
}
