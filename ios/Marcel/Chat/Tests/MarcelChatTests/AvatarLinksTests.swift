import SwiftUI
import UIKit
import XCTest

@testable import MarcelChat

/// The avatar header opens Marcel's notebook and Settings (S-12.9): what VoiceOver hears, and that each named action
/// opens its own screen. Checked on the accessibility tree SwiftUI builds, as PixelKit's audit does.
@MainActor
final class AvatarLinksTests: XCTestCase {
    override class func setUp() {
        super.setUp()
        // Outside an app, UIKit builds no accessibility tree unless accessibility is switched on for the process.
        let handle = dlopen("/usr/lib/libAccessibility.dylib", RTLD_NOW)
        typealias Enable = @convention(c) (Bool) -> Void
        if let symbol = dlsym(handle, "_AXSApplicationAccessibilitySetEnabled") {
            unsafeBitCast(symbol, to: Enable.self)(true)
        }
    }

    private var windows: [UIWindow] = []

    override func tearDown() {
        windows.forEach { $0.isHidden = true }
        windows = []
        super.tearDown()
    }

    func testTheWords() {
        let both = AvatarLinks(notebook: {}, settings: {})
        XCTAssertEqual(both.hint, "Opens Marcel's notebook or Settings")
        XCTAssertEqual(both.actions.map(\.name), ["Marcel's notebook", "Settings"])
        XCTAssertEqual(AvatarLinks(settings: {}).hint, "Opens Settings")
        XCTAssertTrue(AvatarLinks().actions.isEmpty)
    }

    /// The header element: the hint, and one custom action per screen, each opening its own.
    func testVoiceOverHearsBothAndEachActionOpensItsScreen() throws {
        var opened: [String] = []
        let layout = ChatLayout(header: ChatHeaderState(), problem: nil,
                                avatar: AvatarLinks(notebook: { opened.append("notebook") },
                                                    settings: { opened.append("settings") }),
                                composer: { EmptyView() }) { Color.clear.frame(height: 100) }
        let header = try XCTUnwrap(element(in: layout) { $0.accessibilityHint != nil })
        XCTAssertEqual(header.accessibilityHint, "Opens Marcel's notebook or Settings")
        XCTAssertTrue(header.accessibilityTraits.contains(.button))
        let actions = header.accessibilityCustomActions ?? []
        // SwiftUI lists the actions in its own order; each must be there and open its own screen.
        XCTAssertEqual(Set(actions.map(\.name)), ["Marcel's notebook", "Settings"])
        for action in actions {
            opened = []
            _ = action.actionHandler?(action)
            XCTAssertEqual(opened, [action.name == "Settings" ? "settings" : "notebook"], action.name)
        }
    }

    private func element<V: View>(in view: V, where match: (NSObject) -> Bool) -> NSObject? {
        let host = UIHostingController(rootView: view.frame(width: 390, height: 700))
        let window = UIWindow(frame: CGRect(x: 0, y: 0, width: 390, height: 700))
        window.rootViewController = host
        window.makeKeyAndVisible()
        windows.append(window)
        host.view.layoutIfNeeded()
        RunLoop.main.run(until: Date().addingTimeInterval(0.3))

        var found: NSObject?
        func walk(_ object: NSObject, depth: Int) {
            guard found == nil, depth < 25 else { return }
            if object.isAccessibilityElement, match(object) {
                found = object
                return
            }
            if let children = object.accessibilityElements as? [NSObject], !children.isEmpty {
                children.forEach { walk($0, depth: depth + 1) }
            } else if object.accessibilityElementCount() != NSNotFound, object.accessibilityElementCount() > 0 {
                for index in 0..<object.accessibilityElementCount() {
                    if let child = object.accessibilityElement(at: index) as? NSObject { walk(child, depth: depth + 1) }
                }
            } else if let view = object as? UIView {
                view.subviews.forEach { walk($0, depth: depth + 1) }
            }
        }
        walk(host.view, depth: 0)
        return found
    }
}
