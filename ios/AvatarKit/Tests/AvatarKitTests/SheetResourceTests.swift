import XCTest
@testable import AvatarKit

/// The bundled default is Marcel's Pixel Zoo look at the zoo's native 32 × 32 (giraffe, rose, professional). That the
/// file equals what the zoo draws is checked by shared/avatar/zoo/tests/test_app_sheet.py; refresh it with
/// `make -C ios/AvatarKit sheet`.
final class SheetResourceTests: XCTestCase {
    func testTheBundledSheetIsMarcelsZooLook() throws {
        let sheet = SpriteSheet.giraffe
        XCTAssertEqual(sheet.name, "giraffe")
        XCTAssertEqual(sheet.frameWidth, 32, "the zoo's native size, not the old 48 × 54 giraffe")
        XCTAssertEqual(sheet.frameHeight, 32)
        for state in [AvatarState.idle, .thinking, .working(1), .working(2), .working(3), .needsYou, .done] {
            XCTAssertNotNil(sheet.loops[state.loopName], "a \(state.loopName) loop")
        }
        let url = try XCTUnwrap(Bundle.module.url(forResource: "giraffe-rose-professional", withExtension: "json"))
        let info = try JSONSerialization.jsonObject(with: Data(contentsOf: url)) as? [String: Any]
        XCTAssertEqual(info?["palette"] as? String, "rose")
        XCTAssertNil(Bundle.module.url(forResource: "giraffe", withExtension: "png"), "the old giraffe is gone")
    }
}
