import XCTest

@testable import MarcelMap

/// S-12.11 (lead follow-up): a room's quest cards open the quest view. XCUITest cannot run on this Mac's Xcode (see
/// ios/README.md), so this checks the Map's navigation path the screen is bound to: a tile opens its room, a quest
/// card in that room opens the quest on top of it, and back returns to the room.
@MainActor
final class MapNavigationTests: XCTestCase {
    func testARoomsQuestCardOpensTheQuestView() throws {
        let fed = try FedCache.seeded()
        let room = try XCTUnwrap(try fed.room("prj_energy"))
        let card = try XCTUnwrap(room.quests.sections.first?.rows.first)

        var navigation = MapNavigation()
        navigation.openRoom(room.id)
        navigation.openQuest(card)
        XCTAssertEqual(navigation.path, [.room(projectId: "prj_energy"), .quest(taskId: card.id)])
        XCTAssertEqual(card.id, room.questIds.first, "the card's id is its task's")

        navigation.back()
        XCTAssertEqual(navigation.path, [.room(projectId: "prj_energy")], "back to the room")

        // A project link opens the room straight from the map, whatever was open.
        navigation.openQuest(card)
        navigation.openLink(projectId: "prj_family")
        XCTAssertEqual(navigation.path, [.room(projectId: "prj_family")])
        navigation.back()
        navigation.back()
        XCTAssertEqual(navigation.path, [])
    }
}
