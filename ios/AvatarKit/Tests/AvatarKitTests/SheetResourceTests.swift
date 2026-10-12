import XCTest
@testable import AvatarKit

/// AvatarKit plays a copy of shared/avatar/pixel/out/giraffe.{png,json}. This fails when the copy drifts from the
/// source; fix it with `make -C ios/AvatarKit sheet`.
final class SheetResourceTests: XCTestCase {
    static let repoRoot = URL(fileURLWithPath: #filePath)
        .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
        .deletingLastPathComponent().deletingLastPathComponent()

    func testBundledSheetMatchesTheSource() throws {
        for file in ["giraffe.png", "giraffe.json"] {
            let source = Self.repoRoot.appendingPathComponent("shared/avatar/pixel/out/\(file)")
            let parts = file.split(separator: ".")
            let bundled = try XCTUnwrap(Bundle.module.url(forResource: String(parts[0]), withExtension: String(parts[1])),
                                        "\(file) is not in AvatarKit's bundle")
            let want = try Data(contentsOf: source)
            let have = try Data(contentsOf: bundled)
            XCTAssertEqual(have, want, "\(file) in AvatarKit differs from shared/avatar/pixel/out; run `make -C ios/AvatarKit sheet`")
        }
    }
}
