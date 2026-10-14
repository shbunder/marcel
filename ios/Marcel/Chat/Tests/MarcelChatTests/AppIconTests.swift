import CoreGraphics
import CryptoKit
import Foundation
import ImageIO
import XCTest

/// The app icon is a puffy bow that tiles, on rose, the centre bow white with a teal knot (ADR-008). It is drawn by
/// `shared/brand/app-icon/generate.py` (a dev script that needs Pillow; `uv run` installs it), not by the app.
///
/// The script writes the PNGs to `ios/Marcel/App/AppIcon/`, named the way iOS looks them up (`AppIcon60x60@3x.png`),
/// and records their SHA-256 in `shared/brand/app-icon/out/checksums.txt`. `Config/Marcel-Info.plist` lists them
/// (`CFBundleIconFiles`): loose files, because actool cannot compile an asset catalog on a Mac without the iOS
/// platform component (ios/README.md). `AppIcon-1024.png` is the App Store size; the dark 1024 icon waits in
/// `shared/brand/app-icon/out/` for the asset catalog. These tests fail when a committed icon is not the script's
/// output, has the wrong size, or has transparency. To change the icon: edit the script, run it, look, commit.
final class AppIconTests: XCTestCase {
    /// Tests/MarcelChatTests → Tests → Chat → Marcel
    static var marcelFolder: URL {
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
            .deletingLastPathComponent()
    }

    static var iconFolder: URL { marcelFolder.appendingPathComponent("App/AppIcon") }
    /// ios/Marcel → ios → the repository
    static var repo: URL { marcelFolder.deletingLastPathComponent().deletingLastPathComponent() }
    static var brandFolder: URL { repo.appendingPathComponent("shared/brand/app-icon/out") }

    /// Every iPhone icon (20, 29, 40 and 60 pt at 2× and 3×), named the way iOS looks them up, and the App Store's
    /// 1024 px.
    static let files: [(name: String, pixels: Int)] = [20, 29, 40, 60].flatMap { points in
        [2, 3].map { scale in ("AppIcon\(points)x\(points)@\(scale)x.png", points * scale) }
    } + [("AppIcon-1024.png", 1024)]

    /// `checksums.txt`: one "<sha256>  <path from the repo root>" line per file the script writes.
    func checksums() throws -> [String: String] {
        let text = try String(contentsOf: Self.brandFolder.appendingPathComponent("checksums.txt"), encoding: .utf8)
        var sums: [String: String] = [:]
        for line in text.split(separator: "\n") {
            let parts = line.split(separator: " ", omittingEmptySubsequences: true)
            guard parts.count == 2 else { continue }
            sums[String(parts[1])] = String(parts[0])
        }
        return sums
    }

    func sha256(_ url: URL) throws -> String {
        SHA256.hash(data: try Data(contentsOf: url)).map { String(format: "%02x", $0) }.joined()
    }

    func image(_ url: URL) throws -> CGImage {
        let source = try XCTUnwrap(CGImageSourceCreateWithURL(url as CFURL, nil), "\(url.lastPathComponent) is not an image")
        return try XCTUnwrap(CGImageSourceCreateImageAtIndex(source, 0, nil), "\(url.lastPathComponent) did not decode")
    }

    func testEveryIconIsTheGeneratorsOutput() throws {
        let sums = try checksums()
        for icon in Self.files {
            let key = "ios/Marcel/App/AppIcon/\(icon.name)"
            let expected = try XCTUnwrap(sums[key], "checksums.txt has no line for \(key): run generate.py")
            XCTAssertEqual(try sha256(Self.iconFolder.appendingPathComponent(icon.name)), expected,
                           "\(icon.name) is not what shared/brand/app-icon/generate.py draws: run it and commit")
        }
        let onDisk = try FileManager.default.contentsOfDirectory(atPath: Self.iconFolder.path).filter { $0.hasSuffix(".png") }
        XCTAssertEqual(Set(onDisk), Set(Self.files.map(\.name)), "no stray or missing icon files")
    }

    func testEveryIconHasItsSizeAndNoTransparency() throws {
        let opaque: Set<CGImageAlphaInfo> = [.none, .noneSkipLast, .noneSkipFirst]
        for icon in Self.files {
            let picture = try image(Self.iconFolder.appendingPathComponent(icon.name))
            XCTAssertEqual(picture.width, icon.pixels, icon.name)
            XCTAssertEqual(picture.height, icon.pixels, icon.name)
            XCTAssertTrue(opaque.contains(picture.alphaInfo), "\(icon.name) has transparency; App Store icons may not")
        }
    }

    func testTheDarkIconWaitsForTheAssetCatalog() throws {
        let url = Self.brandFolder.appendingPathComponent("app-icon-dark-1024.png")
        let expected = try XCTUnwrap(try checksums()["shared/brand/app-icon/out/app-icon-dark-1024.png"])
        XCTAssertEqual(try sha256(url), expected, "the dark icon is not what generate.py draws")
        let picture = try image(url)
        XCTAssertEqual(picture.width, 1024)
        XCTAssertEqual(picture.height, 1024)
        let opaque: Set<CGImageAlphaInfo> = [.none, .noneSkipLast, .noneSkipFirst]
        XCTAssertTrue(opaque.contains(picture.alphaInfo), "the dark icon has transparency; App Store icons may not")
    }

    func testTheInfoPlistListsEveryIcon() throws {
        let data = try Data(contentsOf: Self.marcelFolder.deletingLastPathComponent()
            .appendingPathComponent("Config/Marcel-Info.plist"))
        let plist = try XCTUnwrap(try PropertyListSerialization.propertyList(from: data, format: nil) as? [String: Any])
        let primary = (plist["CFBundleIcons"] as? [String: Any])?["CFBundlePrimaryIcon"] as? [String: Any]
        let listed = try XCTUnwrap(primary?["CFBundleIconFiles"] as? [String])
        // Each listed base name has its @2x and @3x file; every home-screen size is listed.
        let bases = Set(Self.files.compactMap { $0.name.split(separator: "@").first.map(String.init) })
            .subtracting(["AppIcon-1024.png"])
        XCTAssertEqual(Set(listed), bases)
    }
}
