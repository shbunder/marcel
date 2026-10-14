import CoreGraphics
import Foundation
@testable import AvatarKit

/// A fixed sheet for the player's timing tests: the loops of the first giraffe sheet (S-13.1, `Fixtures/
/// timing-sheet.json`) over blank frames. The tests check how the player switches and wraps, so they use a table that
/// never changes, not whatever art the app bundles.
enum TimingSheet {
    static let fixture = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
        .appending(path: "Fixtures/timing-sheet.json")

    static let sheet: SpriteSheet = {
        let info = try! JSONDecoder().decode(SheetJSON.self, from: Data(contentsOf: fixture))
        let rows = (info.frame_count + info.columns - 1) / info.columns
        let context = CGContext(data: nil, width: info.columns * info.frame.width, height: rows * info.frame.height,
                                bitsPerComponent: 8, bytesPerRow: 0, space: CGColorSpaceCreateDeviceRGB(),
                                bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
        return try! SpriteSheet(info: info, image: context.makeImage()!)
    }()
}
