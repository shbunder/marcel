import MarcelKit
import SwiftUI
import XCTest

@testable import MarcelLook

/// A hub for the picker: previews from `Fixtures/` (drawn by the Pixel Zoo), and saves it remembers.
final class FakeLookService: LookService, @unchecked Sendable {
    static let fixtures = URL(fileURLWithPath: #filePath).deletingLastPathComponent().appending(path: "Fixtures")

    var offline = false
    var refuse: String?
    private(set) var asked: [KnownLook] = []
    private(set) var saves: [KnownLook] = []

    func preview(_ look: KnownLook) async throws -> Data {
        asked.append(look)
        if offline { throw LookError.unreachable }
        let file = Self.fixtures.appending(path: "\(look.animal.rawValue)-\(look.color.rawValue)-\(look.personality.rawValue).png")
        return (try? Data(contentsOf: file)) ?? Data()
    }

    func save(_ look: KnownLook, agentId: String) async throws -> Agent {
        if offline { throw LookError.unreachable }
        if let refuse { throw LookError(refuse) }
        saves.append(look)
        let json: [String: Any] = [
            "id": agentId, "name": "Pip", "animal": look.animal.rawValue, "main_conversation_id": "cnv_1",
            "look": ["animal": look.animal.rawValue, "color": look.color.rawValue,
                     "personality": look.personality.rawValue],
            "state": ["kind": "idle"], "model_brain": "opus", "model_worker": "sonnet",
        ]
        return try JSONDecoder().decode(Agent.self, from: JSONSerialization.data(withJSONObject: json))
    }
}

@MainActor
final class LookTests: XCTestCase {
    static let foxTealCool = KnownLook(animal: .fox, color: .teal, personality: .cool)

    // MARK: Pick from my name

    /// The Pixel Zoo's own picks (`python -c "import zoo.core as c; c.from_name(name)"`): the phone must agree with
    /// the hub, which gives a helper made without a look the one its name picks.
    func testPickFromMyNameGivesThePixelZoosLook() {
        let zoo: [(String, LookAnimal, LookColor, LookPersonality)] = [
            ("Emma", .dog, .blue, .curious), ("Marcel", .pig, .rose, .shy), ("Pip", .rabbit, .natural, .grumpy),
            ("  Shaun ", .cat, .natural, .cozy), ("Zoë", .frog, .natural, .cool), ("李雷", .pig, .ink, .sporty),
            ("Łukasz", .fox, .natural, .cozy), ("", .cat, .natural, .shy), ("emma", .dog, .blue, .curious),
            ("A", .crocodile, .natural, .curious), ("Grandma Jo", .bear, .natural, .curious),
            ("😀 Smiley", .panda, .coral, .grumpy),
            // Trimmed by code point ("\r\n" is two), and a word-final capital sigma lower-cased to "ς".
            ("Marcel\r\n", .pig, .rose, .shy), ("\u{2003}Emma\u{FEFF}", .dog, .blue, .curious),
            ("ΟΔΟΣ", .cat, .natural, .shy), ("Σ", .dog, .coral, .sporty), ("ΣΑΣ Β", .pig, .natural, .professional),
            ("ΑΣ1", .cat, .plum, .curious),
        ]
        for (name, animal, color, personality) in zoo {
            XCTAssertEqual(LookFromName.pick(name), KnownLook(animal: animal, color: color, personality: personality),
                           "from_name(\"\(name)\")")
        }
    }

    func testTheListsMatchTheContract() {
        XCTAssertEqual(Set(LookFromName.animals), Set(LookAnimal.allCases))
        XCTAssertEqual(Set(LookFromName.colors + [.natural]), Set(LookColor.allCases))
        XCTAssertEqual(Set(LookFromName.personalities), Set(LookPersonality.allCases))
    }

    // MARK: Choosing and saving

    func testANewHelperStartsFromItsNameAndCanSave() {
        let model = LookModel(service: FakeLookService(), agentId: "agt_pip", name: "Pip", current: nil)
        XCTAssertEqual(model.look, LookFromName.pick("Pip"))
        XCTAssertTrue(model.canSave, "no look yet: saving the suggested one is a choice too")
    }

    func testPickFoxTealCoolAndSave() async {
        let hub = FakeLookService()
        let current = KnownLook(animal: .giraffe, color: .rose, personality: .professional)
        let model = LookModel(service: hub, agentId: "agt_pip", name: "Pip", current: current)
        XCTAssertFalse(model.canSave, "nothing changed yet")
        model.choose(animal: .fox)
        model.choose(color: .teal)
        model.choose(personality: .cool)
        XCTAssertTrue(model.canSave)
        let agent = await model.save()
        XCTAssertEqual(hub.saves, [Self.foxTealCool])
        XCTAssertEqual(agent?.drawnLook, Self.foxTealCool)
        XCTAssertEqual(model.saved, Self.foxTealCool)
        XCTAssertFalse(model.canSave, "saved: nothing left to save")
        model.pickFromName()
        XCTAssertEqual(model.look, LookFromName.pick("Pip"))
    }

    func testASaveTheHubRefusesSaysWhyAndKeepsTheChoice() async {
        let hub = FakeLookService()
        hub.refuse = "Pick a known animal."
        let model = LookModel(service: hub, agentId: "agt_pip", name: "Pip", current: nil)
        model.choose(animal: .frog)
        let agent = await model.save()
        XCTAssertNil(agent)
        XCTAssertEqual(model.problem, "Pick a known animal.")
        XCTAssertEqual(model.look.animal, .frog)
        XCTAssertTrue(model.canSave, "you can try again")
    }

    func testPreviewsAreAskedOnceAndOfflineSaysSo() async {
        let hub = FakeLookService()
        let model = LookModel(service: hub, agentId: "agt_pip", name: "Pip", current: Self.foxTealCool)
        await model.loadPreview(model.look)
        await model.loadPreview(model.look)
        XCTAssertEqual(hub.asked, [Self.foxTealCool], "the same look draws the same image: asked once")
        XCTAssertNotNil(model.previews[Self.foxTealCool])

        hub.offline = true
        let frog = model.trying(animal: .frog)
        await model.loadPreview(frog)
        XCTAssertEqual(model.previewProblem, LookError.unreachable.message)
        hub.offline = false
        await model.loadPreview(frog)
        XCTAssertNotNil(model.previews[frog], "back online, it is asked again")
        XCTAssertNil(model.previewProblem)
    }

    // MARK: How it looks

    func testThePicker() async {
        // Picked fox, teal and cool over the giraffe the helper has: Save is ready.
        let giraffe = KnownLook(animal: .giraffe, color: .rose, personality: .professional)
        let model = LookModel(service: FakeLookService(), agentId: "agt_pip", name: "Pip", current: giraffe)
        model.choose(animal: .fox)
        model.choose(color: .teal)
        model.choose(personality: .cool)
        for animal in LookAnimal.allCases { await model.loadPreview(model.trying(animal: animal)) }
        Snapshot.assert(LookPicker(model: model), named: "Look-picker")
    }

    func testEveryColourHasItsWordsAndASwatchThatShows() {
        for color in LookColor.allCases {
            XCTAssertFalse(LookWords.color(color).isEmpty)
        }
        XCTAssertEqual(LookWords.describe(Self.foxTealCool), "A teal fox, cool")
        XCTAssertEqual(LookWords.describe(KnownLook(animal: .pig, color: .natural, personality: .shy)),
                       "A pig in its own colours, shy")
    }
}
