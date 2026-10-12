import AvatarKit
import CoreGraphics
import Foundation
import XCTest

@testable import MarcelChat

/// The app icon (S-13.3) is drawn from the sprite sheet's idle frame by `AppIcon` below, not by hand: the giraffe at
/// the largest whole-number zoom that fits, every sprite pixel a sharp square, on the cream of the app's screens.
///
/// The PNGs live in `ios/Marcel/App/AppIcon/`, named the way iOS looks them up (`AppIcon60x60@3x.png`), and
/// `Config/Marcel-Info.plist` lists them (`CFBundleIconFiles`): loose files, because actool cannot compile an asset
/// catalog on a Mac without the iOS platform component (ios/README.md). `AppIcon-1024.png` is the App Store size, for
/// an asset catalog later. This test redraws each file and fails when a committed one differs (the giraffe changed
/// and the icon was not redrawn). To redraw them after a change to the sheet: `make -C ios chat-record` (it sets
/// MARCELCHAT_RECORD=1), look at the PNGs, and commit them.
final class AppIconTests: XCTestCase {
    static var iconFolder: URL {
        // Tests/MarcelChatTests → Tests → Chat → Marcel, then App/AppIcon
        marcelFolder.appendingPathComponent("App/AppIcon")
    }

    static var marcelFolder: URL {
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
            .deletingLastPathComponent()
    }

    func testEveryIconSizeIsDrawnFromTheIdleFrame() throws {
        for icon in AppIcon.files {
            let image = try XCTUnwrap(AppIcon.render(side: icon.pixels), "\(icon.name) did not render")
            Snapshot.compare(image, fileName: icon.name, folder: Self.iconFolder, file: #filePath, line: #line)
        }
        let onDisk = try FileManager.default.contentsOfDirectory(atPath: Self.iconFolder.path).filter { $0.hasSuffix(".png") }
        XCTAssertEqual(Set(onDisk), Set(AppIcon.files.map(\.name)), "no stray or missing icon files")
    }

    func testTheInfoPlistListsEveryIcon() throws {
        let data = try Data(contentsOf: Self.marcelFolder.deletingLastPathComponent()
            .appendingPathComponent("Config/Marcel-Info.plist"))
        let plist = try XCTUnwrap(try PropertyListSerialization.propertyList(from: data, format: nil) as? [String: Any])
        let primary = (plist["CFBundleIcons"] as? [String: Any])?["CFBundlePrimaryIcon"] as? [String: Any]
        let listed = try XCTUnwrap(primary?["CFBundleIconFiles"] as? [String])
        // Each listed base name has its @2x and @3x file; every home-screen size is listed.
        let bases = Set(AppIcon.files.compactMap { $0.name.split(separator: "@").first.map(String.init) })
            .subtracting(["AppIcon-1024.png"])
        XCTAssertEqual(Set(listed), bases)
    }

    func testThePixelsAreWholeSpritePixels() throws {
        // At 180 px (the home screen on a 3× phone) the giraffe is 3× : every sprite pixel is a 3 × 3 block.
        XCTAssertEqual(AppIcon.layout(side: 180).zoom, 3)
        XCTAssertEqual(AppIcon.layout(side: 1024).zoom, 17)
        // Too small for the whole giraffe even at 1×: the head and neck instead.
        XCTAssertTrue(AppIcon.layout(side: 40).headOnly)
        XCTAssertFalse(AppIcon.layout(side: 58).headOnly)
        let image = try XCTUnwrap(AppIcon.render(side: 180))
        let layout = AppIcon.layout(side: 180)
        let pixels = Snapshot.rgba(image)
        for y in stride(from: layout.origin.y, to: layout.origin.y + layout.rows * 3, by: 3) {
            for x in stride(from: layout.origin.x, to: layout.origin.x + layout.columns * 3, by: 3) {
                let first = Array(pixels[(y * 180 + x) * 4..<(y * 180 + x) * 4 + 4])
                for dy in 0..<3 {
                    for dx in 0..<3 {
                        let i = ((y + dy) * 180 + x + dx) * 4
                        XCTAssertEqual(Array(pixels[i..<i + 4]), first, "a blurred sprite pixel at \(x + dx),\(y + dy)")
                    }
                }
            }
        }
        XCTAssertEqual(image.alphaInfo, .noneSkipLast, "App Store icons have no transparency")
    }
}

/// Draws the app icon from the giraffe's idle frame. Pure CoreGraphics on raw pixels: no interpolation anywhere.
enum AppIcon {
    /// Every iPhone icon (20, 29, 40 and 60 pt at 2× and 3×), named the way iOS looks them up, and the App Store's
    /// 1024 px.
    static let files: [(name: String, pixels: Int)] = [20, 29, 40, 60].flatMap { points in
        [2, 3].map { scale in ("AppIcon\(points)x\(points)@\(scale)x.png", points * scale) }
    } + [("AppIcon-1024.png", 1024)]
    /// The screens' cream (`PixelColor.cream`).
    static let background: (r: UInt8, g: UInt8, b: UInt8) = (0xF6, 0xEF, 0xE4)
    /// The giraffe fills at most this share of the icon, so the rounded corners never cut it.
    static let fill = 0.82

    struct Layout {
        var zoom: Int
        /// The part of the frame drawn (the giraffe's bounding box, or its head and neck).
        var crop: (x: Int, y: Int)
        var columns: Int
        var rows: Int
        var origin: (x: Int, y: Int)
        var headOnly: Bool
    }

    static var frame: CGImage {
        let sheet = SpriteSheet.giraffe
        return sheet.image(frame: sheet.loop(for: .idle).firstFrame)
    }

    /// Where the giraffe goes in a `side`-pixel icon: the largest whole zoom that fits in `fill` of it, else 1× edge to
    /// edge, else (the 40-px icon) just the head and neck at 1×. Centred on whole pixels.
    static func layout(side: Int) -> Layout {
        let pixels = Snapshot.rgba(frame)
        let width = frame.width, height = frame.height
        var minX = width, minY = height, maxX = -1, maxY = -1
        for y in 0..<height {
            for x in 0..<width where pixels[(y * width + x) * 4 + 3] > 0 {
                minX = min(minX, x); maxX = max(maxX, x); minY = min(minY, y); maxY = max(maxY, y)
            }
        }
        var columns = maxX - minX + 1, rows = maxY - minY + 1
        var zoom = Int(Double(side) * fill) / max(columns, rows)
        var headOnly = false
        if zoom < 1 {
            if max(columns, rows) <= side {
                zoom = 1
            } else {
                // The head and neck: the top rows of the giraffe, as tall as fits.
                headOnly = true
                zoom = 1
                rows = min(rows, Int(Double(side) * 0.66))
                columns = min(columns, side)
            }
        }
        let origin = ((side - columns * zoom) / 2, (side - rows * zoom) / 2)
        return Layout(zoom: zoom, crop: (minX, minY), columns: columns, rows: rows, origin: origin,
                      headOnly: headOnly)
    }

    static func render(side: Int) -> CGImage? {
        let source = Snapshot.rgba(frame)  // premultiplied
        let width = frame.width
        let layout = layout(side: side)
        var out = [UInt8](repeating: 255, count: side * side * 4)
        for i in stride(from: 0, to: out.count, by: 4) {
            out[i] = background.r; out[i + 1] = background.g; out[i + 2] = background.b
        }
        for row in 0..<layout.rows {
            for column in 0..<layout.columns {
                let s = ((layout.crop.y + row) * width + layout.crop.x + column) * 4
                let alpha = Int(source[s + 3])
                guard alpha > 0 else { continue }
                // Over the cream: premultiplied colour + background × (1 − alpha).
                let r = UInt8(min(255, Int(source[s]) + Int(background.r) * (255 - alpha) / 255))
                let g = UInt8(min(255, Int(source[s + 1]) + Int(background.g) * (255 - alpha) / 255))
                let b = UInt8(min(255, Int(source[s + 2]) + Int(background.b) * (255 - alpha) / 255))
                for dy in 0..<layout.zoom {
                    for dx in 0..<layout.zoom {
                        let x = layout.origin.x + column * layout.zoom + dx
                        let y = layout.origin.y + row * layout.zoom + dy
                        let o = (y * side + x) * 4
                        out[o] = r; out[o + 1] = g; out[o + 2] = b
                    }
                }
            }
        }
        let space = CGColorSpace(name: CGColorSpace.sRGB)!
        guard let provider = CGDataProvider(data: Data(out) as CFData) else { return nil }
        return CGImage(width: side, height: side, bitsPerComponent: 8, bitsPerPixel: 32, bytesPerRow: side * 4,
                       space: space, bitmapInfo: CGBitmapInfo(rawValue: CGImageAlphaInfo.noneSkipLast.rawValue),
                       provider: provider, decode: nil, shouldInterpolate: false, intent: .defaultIntent)
    }
}
