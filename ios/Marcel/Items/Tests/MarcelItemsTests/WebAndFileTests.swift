import Foundation
import QuickLook
import WebKit
import XCTest

@testable import MarcelItems

/// The web view's rules for HTML items, checked on its configuration and on a real `WKWebView` in the simulator, and
/// QuickLook getting a file it can open.
@MainActor
final class WebAndFileTests: XCTestCase {
    // MARK: The configuration

    func testUntrustedHTMLHasJavaScriptOff() {
        let configuration = WebPolicy.configuration(trusted: false)
        XCTAssertFalse(configuration.defaultWebpagePreferences.allowsContentJavaScript)
        XCTAssertFalse(configuration.preferences.javaScriptCanOpenWindowsAutomatically)
        XCTAssertFalse(configuration.websiteDataStore.isPersistent, "nothing is kept between pages")
        XCTAssertEqual(configuration.dataDetectorTypes, [])
        XCTAssertEqual(configuration.mediaTypesRequiringUserActionForPlayback, .all, "sound and video need a tap")
    }

    func testOnlyHTMLTheHubMadeItselfRunsJavaScript() {
        XCTAssertTrue(WebPolicy.configuration(trusted: true).defaultWebpagePreferences.allowsContentJavaScript)
    }

    func testUntrustedPagesGetAPolicyThatBlocksScriptsAndTheNetwork() {
        let page = WebPolicy.page("<p>Hi</p>", trusted: false)
        XCTAssertTrue(page.hasPrefix(#"<meta http-equiv="Content-Security-Policy" content="default-src 'none';"#))
        XCTAssertTrue(page.hasSuffix("<p>Hi</p>"))
        XCTAssertFalse(WebPolicy.page("<p>Hi</p>", trusted: true).contains("Content-Security-Policy"))
    }

    // MARK: Navigation

    func testTheWebViewNeverLeavesTheItemWithoutATap() {
        XCTAssertEqual(WebPolicy.decide(url: URL(string: "about:blank"), linkActivated: false), .allow,
                       "loading the item itself")
        XCTAssertEqual(WebPolicy.decide(url: URL(string: "about:blank#prices"), linkActivated: true), .allow,
                       "an in-page anchor")
        let offer = URL(string: "https://www.luminus.be/offer")!
        XCTAssertEqual(WebPolicy.decide(url: offer, linkActivated: true), .openOutside(offer), "a tap opens Safari")
        XCTAssertEqual(WebPolicy.decide(url: offer, linkActivated: false), .cancel, "a redirect or a script does not")
        for url in ["javascript:alert(1)", "file:///etc/passwd", "tel:123", "marcel://pair?code=1",
                    "data:text/html,hi"] {
            XCTAssertEqual(WebPolicy.decide(url: URL(string: url), linkActivated: true), .cancel, url)
        }
        XCTAssertEqual(WebPolicy.decide(url: nil, linkActivated: true), .cancel)
    }

    // MARK: The navigation delegate

    /// A navigation as WebKit hands it to the delegate.
    final class FakeAction: WKNavigationAction {
        let url: URL
        let type: WKNavigationType
        init(_ url: String, _ type: WKNavigationType) {
            self.url = URL(string: url)!
            self.type = type
            super.init()
        }
        override var request: URLRequest { URLRequest(url: url) }
        override var navigationType: WKNavigationType { type }
    }

    private func decide(_ coordinator: HTMLView.Coordinator, _ action: WKNavigationAction) -> WKNavigationActionPolicy? {
        var policy: WKNavigationActionPolicy?
        coordinator.webView(WKWebView(), decidePolicyFor: action) { policy = $0 }
        return policy
    }

    func testTheDelegateCancelsNavigationsAndSendsTappedLinksToSafari() {
        let coordinator = HTMLView.Coordinator()
        var opened: [URL] = []
        coordinator.open = { opened.append($0) }

        XCTAssertEqual(decide(coordinator, FakeAction("about:blank", .other)), .allow, "the item itself loads")
        XCTAssertEqual(decide(coordinator, FakeAction("https://evil.example/", .other)), .cancel, "a redirect")
        XCTAssertEqual(decide(coordinator, FakeAction("https://evil.example/", .formSubmitted)), .cancel, "a form")
        XCTAssertEqual(decide(coordinator, FakeAction("tel:123", .linkActivated)), .cancel)
        XCTAssertEqual(opened, [], "nothing opened without a tapped web link")

        XCTAssertEqual(decide(coordinator, FakeAction("https://www.luminus.be/offer", .linkActivated)), .cancel,
                       "the web view stays on the item")
        XCTAssertEqual(opened, [URL(string: "https://www.luminus.be/offer")!], "Safari opens the tapped link")

        // target="_blank": never a second web view.
        XCTAssertNil(coordinator.webView(WKWebView(), createWebViewWith: WKWebViewConfiguration(),
                                         for: FakeAction("https://example.com/", .linkActivated),
                                         windowFeatures: WKWindowFeatures()))
        XCTAssertEqual(opened.last, URL(string: "https://example.com/")!)
    }

    func testAPageCannotNavigateItselfAwayInARealWebView() async throws {
        let view = WKWebView(frame: CGRect(x: 0, y: 0, width: 390, height: 600),
                             configuration: WebPolicy.configuration(trusted: false))
        let coordinator = HTMLView.Coordinator()
        var opened: [URL] = []
        coordinator.open = { opened.append($0) }
        view.loadHTMLString("<p>Item</p>", baseURL: nil)
        try await Task.sleep(for: .milliseconds(500))
        view.navigationDelegate = coordinator
        _ = try? await view.evaluateJavaScript("window.location.href = 'https://example.com/'; 1")
        try await Task.sleep(for: .milliseconds(500))
        XCTAssertEqual(view.url?.scheme, "about", "the page navigated away: \(view.url?.absoluteString ?? "")")
        XCTAssertEqual(opened, [], "a navigation nobody tapped does not open Safari")
    }

    // MARK: A real web view

    /// Loads `page` exactly as given into a web view with the item's configuration.
    private func load(page: String, trusted: Bool) async throws -> WKWebView {
        let view = WKWebView(frame: CGRect(x: 0, y: 0, width: 390, height: 600),
                             configuration: WebPolicy.configuration(trusted: trusted))
        final class Waiter: NSObject, WKNavigationDelegate {
            var done: CheckedContinuation<Void, Never>?
            func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) { done?.resume(); done = nil }
            func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: any Error) {
                done?.resume(); done = nil
            }
        }
        let waiter = Waiter()
        view.navigationDelegate = waiter
        await withCheckedContinuation { continuation in
            waiter.done = continuation
            view.loadHTMLString(page, baseURL: nil)
        }
        _ = waiter
        return view
    }

    func testAnUntrustedPagesScriptDoesNotRunAndATrustedOnesDoes() async throws {
        let html = String(decoding: Fx.htmlContent, as: UTF8.self)
        // The configuration alone (no content security policy) keeps the page's <script> from running; the app's
        // own `evaluateJavaScript` still works.
        let untrusted = try await load(page: html, trusted: false)
        let title = try await untrusted.evaluateJavaScript("document.title") as? String
        XCTAssertEqual(title, "", "the page's script ran in an untrusted page")
        let words = try await untrusted.evaluateJavaScript("document.body.innerText") as? String
        XCTAssertTrue(words?.contains("Luminus & Engie, per year.") == true, "the page itself is drawn: \(words ?? "")")

        let trusted = try await load(page: WebPolicy.page(html, trusted: true), trusted: true)
        let ran = try await trusted.evaluateJavaScript("document.title") as? String
        XCTAssertEqual(ran, "ran", "the hub's own page runs its scripts")

        // The policy put in front of an untrusted page blocks its scripts too, even if JavaScript were on.
        let guarded = try await load(page: WebPolicy.page(html, trusted: false), trusted: true)
        let blocked = try await guarded.evaluateJavaScript("document.title") as? String
        XCTAssertEqual(blocked, "", "the content security policy did not stop the script")
    }

    func testTheWordsOfAPageForItsSnapshot() {
        XCTAssertEqual(WebPolicy.text(of: String(decoding: Fx.htmlContent, as: UTF8.self)),
                       "Offers\nLuminus & Engie, per year.\nLuminus€1,084\nThe offer")
    }

    // MARK: QuickLook

    func testAFileIsWrittenWhereQuickLookCanOpenIt() throws {
        guard case .file(let file) = ItemBody.make(Fx.artifact(Fx.file), contentType: nil, content: .loaded(Fx.pdf))
        else { return XCTFail("not a file") }
        let folder = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: folder) }
        let url = try file.write(version: 1, in: folder)
        XCTAssertEqual(url.lastPathComponent, "Bills 2026.pdf")
        XCTAssertEqual(url.deletingLastPathComponent().lastPathComponent, "\(Fx.fileId)-v1")
        XCTAssertEqual(try Data(contentsOf: url), Fx.pdf)
        XCTAssertTrue(QLPreviewController.canPreview(url as NSURL), "QuickLook can show it")
    }
}
