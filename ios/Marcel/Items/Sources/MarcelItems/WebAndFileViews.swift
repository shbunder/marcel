import Foundation
import PixelKit
import QuickLook
import SwiftUI
import UIKit
import WebKit

/// The rules for an HTML item's web view. HTML comes from workers, so it is treated as untrusted unless the hub made
/// it itself (`Artifact.trusted`): JavaScript off, nothing loaded from the network, nothing kept between pages, and
/// no way to leave the item except a tapped web link, which opens in Safari.
public enum WebPolicy {
    /// What a navigation inside the web view may do.
    public enum Decision: Equatable, Sendable {
        /// Loading the item itself (`about:blank`, and in-page `#anchors`).
        case allow
        /// A tapped http(s) link: Safari opens it; the web view stays on the item.
        case openOutside(URL)
        /// Anything else: redirects, scripts, forms, other schemes.
        case cancel
    }

    /// A fresh configuration for one item. Page JavaScript runs only when `trusted` is true.
    @MainActor
    public static func configuration(trusted: Bool) -> WKWebViewConfiguration {
        let configuration = WKWebViewConfiguration()
        configuration.defaultWebpagePreferences.allowsContentJavaScript = trusted
        configuration.preferences.javaScriptCanOpenWindowsAutomatically = false
        configuration.websiteDataStore = .nonPersistent()
        configuration.dataDetectorTypes = []
        configuration.mediaTypesRequiringUserActionForPlayback = .all
        return configuration
    }

    public static func decide(url: URL?, linkActivated: Bool) -> Decision {
        guard let url else { return .cancel }
        if url.scheme?.lowercased() == "about" { return .allow }
        if linkActivated, let web = ItemMarkdown.webLink(url) { return .openOutside(web) }
        return .cancel
    }

    /// The page as the web view loads it: sized for the phone, and for untrusted HTML a content security policy that
    /// blocks scripts and every load from the network (pictures only from inside the page).
    public static func page(_ html: String, trusted: Bool) -> String {
        let viewport = #"<meta name="viewport" content="width=device-width, initial-scale=1">"#
        guard !trusted else { return viewport + html }
        let policy = #"<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; "#
            + #"style-src 'unsafe-inline'; font-src data:; media-src data:; form-action 'none'">"#
        return policy + viewport + html
    }

    /// The page's words, for a snapshot (a web view cannot be drawn off screen) and for VoiceOver's summary.
    public static func text(of html: String) -> String {
        var text = html
        for pattern in [#"(?is)<(script|style|head)[^>]*>.*?</\1>"#, #"(?i)<br\s*/?>"#, #"(?i)</(p|h[1-6]|li|div|tr)>"#] {
            text = text.replacingOccurrences(of: pattern, with: "\n", options: .regularExpression)
        }
        text = text.replacingOccurrences(of: #"<[^>]+>"#, with: "", options: .regularExpression)
        for (entity, character) in [("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", "\""), ("&#39;", "'"),
                                    ("&nbsp;", " "), ("&euro;", "€")] {
            text = text.replacingOccurrences(of: entity, with: character)
        }
        return text.split(separator: "\n").map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }
            .joined(separator: "\n")
    }
}

/// An HTML item in a `WKWebView`, under `WebPolicy`.
struct HTMLView: UIViewRepresentable {
    var html: String
    var trusted: Bool

    @Environment(\.openURL) private var openURL

    func makeCoordinator() -> Coordinator { Coordinator() }

    func makeUIView(context: Context) -> WKWebView {
        let view = WKWebView(frame: .zero, configuration: WebPolicy.configuration(trusted: trusted))
        view.navigationDelegate = context.coordinator
        view.uiDelegate = context.coordinator
        view.isOpaque = false
        view.allowsLinkPreview = false
        view.accessibilityLabel = "Web page"
        return view
    }

    func updateUIView(_ view: WKWebView, context: Context) {
        context.coordinator.open = { url in openURL(url) }
        let page = WebPolicy.page(html, trusted: trusted)
        guard context.coordinator.loaded != page else { return }
        context.coordinator.loaded = page
        view.loadHTMLString(page, baseURL: nil)
    }

    @MainActor
    final class Coordinator: NSObject, WKNavigationDelegate, WKUIDelegate {
        var loaded: String?
        var open: (URL) -> Void = { _ in }

        func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                     decisionHandler: @escaping @MainActor (WKNavigationActionPolicy) -> Void) {
            switch WebPolicy.decide(url: action.request.url, linkActivated: action.navigationType == .linkActivated) {
            case .allow: decisionHandler(.allow)
            case .openOutside(let url):
                decisionHandler(.cancel)
                open(url)
            case .cancel: decisionHandler(.cancel)
            }
        }

        /// `target="_blank"` links: never a second web view; a tapped web link opens in Safari.
        func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                     for action: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
            if case .openOutside(let url) = WebPolicy.decide(url: action.request.url,
                                                             linkActivated: action.navigationType == .linkActivated) {
                open(url)
            }
            return nil
        }
    }
}

/// An HTML item where a web view cannot be drawn (snapshots): its words in a page-like box.
struct HTMLStill: View {
    var html: String

    @Environment(\.colorScheme) private var scheme

    var body: some View {
        let theme = ChatPalette(scheme)
        VStack(alignment: .leading, spacing: PixelMetrics.steps(3)) {
            ForEach(Array(WebPolicy.text(of: html).split(separator: "\n").enumerated()), id: \.offset) { _, line in
                Text(String(line)).font(.system(.body)).foregroundStyle(Color.black)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(PixelMetrics.padding)
        .background(Color.white)
        .overlay(Rectangle().strokeBorder(theme.line.color, lineWidth: PixelMetrics.thinBorder))
    }
}

extension FileItem {
    /// Writes the bytes where QuickLook can read them: the app's temporary folder, one folder per item and version.
    func write(version: Int, in folder: URL = FileManager.default.temporaryDirectory) throws -> URL {
        let directory = folder.appendingPathComponent("items", isDirectory: true)
            .appendingPathComponent("\(id)-v\(version)", isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let file = directory.appendingPathComponent(fileName)
        try data.write(to: file, options: .atomic)
        return file
    }
}

/// A file item: what it is, and QuickLook showing it.
struct FileCard: View {
    var file: FileItem

    @Environment(\.colorScheme) private var scheme

    var body: some View {
        let theme = ChatPalette(scheme)
        VStack(alignment: .leading, spacing: PixelMetrics.steps(2)) {
            Text(file.fileName).font(.body.weight(.semibold))
                .foregroundStyle(theme.ink.color)
                .fixedSize(horizontal: false, vertical: true)
            Text("\(file.type) · \(ItemWords.size(file.size))").font(.footnote)
                .foregroundStyle(theme.muted.color)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(PixelMetrics.padding)
        .chatSurface()
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(file.fileName). \(file.spoken)")
    }
}

/// QuickLook's preview of a file, inside the viewer.
struct QuickLookView: UIViewControllerRepresentable {
    var url: URL

    func makeCoordinator() -> Coordinator { Coordinator(url: url) }

    func makeUIViewController(context: Context) -> QLPreviewController {
        let controller = QLPreviewController()
        controller.dataSource = context.coordinator
        return controller
    }

    func updateUIViewController(_ controller: QLPreviewController, context: Context) {
        guard context.coordinator.url != url else { return }
        context.coordinator.url = url
        controller.reloadData()
    }

    final class Coordinator: NSObject, QLPreviewControllerDataSource {
        var url: URL

        init(url: URL) {
            self.url = url
        }

        func numberOfPreviewItems(in controller: QLPreviewController) -> Int { 1 }

        func previewController(_ controller: QLPreviewController, previewItemAt index: Int) -> any QLPreviewItem {
            url as NSURL
        }
    }
}

/// A file item on screen: its card, then QuickLook (or a line saying why it cannot show).
struct FileItemView: View {
    var file: FileItem
    var version: Int

    @State private var written: Result<URL, CocoaError>?
    @Environment(\.colorScheme) private var scheme

    var body: some View {
        VStack(spacing: PixelMetrics.steps(4)) {
            FileCard(file: file)
                .padding(.horizontal, ItemsMetrics.inset.leading)
                .padding(.top, ItemsMetrics.inset.top)
            switch written {
            case .success(let url)?:
                QuickLookView(url: url)
            case .failure?:
                Text("The phone couldn't open this file.").font(.body)
                    .foregroundStyle(ChatPalette(scheme).muted.color)
                Spacer()
            case nil:
                Spacer()
            }
        }
        .task(id: "\(file.id)@\(version)") {
            do {
                written = .success(try file.write(version: version))
            } catch {
                written = .failure(CocoaError(.fileWriteUnknown))
            }
        }
    }
}
