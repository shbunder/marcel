import AvatarKit
import ImageIO
import PixelKit
import SwiftUI
import UniformTypeIdentifiers
import XCTest

@testable import MarcelChat

/// Design renders (for review, not checked): the logo's bow tiling behind the chat. Renders the chat with real messages
/// in light and dark with fewer, some and more coloured bows, the header's poses and moods, into
/// `ios/.build/bow-tiling/`, and checks the text stays readable.
@MainActor
final class BowTilingDesignTests: XCTestCase {
    static let out = URL(fileURLWithPath: #filePath)
        .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
        .deletingLastPathComponent().deletingLastPathComponent().appending(path: ".build/bow-tiling")

    func chat(_ accents: BowTiling.Accents, header: ChatHeaderState = ChatHeaderState(avatar: .working(1), running: 1),
              scrolled: CGFloat = 0, under: UnderHeader = .medium) -> some View {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "Europe/Brussels")!
        func at(_ hour: Int, _ minute: Int) -> Date {
            calendar.date(from: DateComponents(year: 2026, month: 10, day: 13, hour: hour, minute: minute))!
        }
        return ChatStill(header: header, items: [
            .time(TimeLine(id: "time-1", date: at(8, 12))),
            .user(UserLine(id: "u1", text: "What's on Thursday?")),
            .marcel(MarcelLine(id: "m1", markdown: "Dentist at **9:30**, then nothing until school pickup at 15:20.")),
            .user(UserLine(id: "u2", text: "Compare the heat-pump offers in my Downloads")),
            .quest(QuestLine(id: "q1", taskId: "t1", title: "Compare heat-pump offers", state: .working,
                             project: "House", tone: .teal, detail: nil, location: .nuc)),
            .time(TimeLine(id: "time-2", date: at(8, 31))),
            .marcel(MarcelLine(id: "m2", markdown: """
                ### Short version
                - Daikin is cheapest over **10 years**.
                - Vaillant can install in May.
                """)),
            .user(UserLine(id: "u3", text: "Great, book Daikin for a visit")),
        ], tiling: accents, scrolled: scrolled, under: under)
        .environment(\.chatNow, at(8, 40))
        .environment(\.timeZone, calendar.timeZone)
        .environment(\.locale, Locale(identifier: "en_GB"))
    }

    func testRenderTheVariants() throws {
        try FileManager.default.createDirectory(at: Self.out, withIntermediateDirectories: true)
        for accents in BowTiling.Accents.allCases {
            for variant in [Snapshot.Variant.light, .dark] {
                let image = try XCTUnwrap(Snapshot.render(chat(accents), variant: variant, padding: 0))
                let url = Self.out.appending(path: "chat-\(accents)-\(variant.rawValue).png")
                let dest = try XCTUnwrap(CGImageDestinationCreateWithURL(url as CFURL, UTType.png.identifier as CFString,
                                                                         1, nil))
                CGImageDestinationAddImage(dest, image, nil)
                XCTAssertTrue(CGImageDestinationFinalize(dest))
            }
        }
    }

    private func save(_ image: CGImage, _ name: String) throws {
        let url = Self.out.appending(path: name)
        let dest = try XCTUnwrap(CGImageDestinationCreateWithURL(url as CFURL, UTType.png.identifier as CFString, 1,
                                                                 nil))
        CGImageDestinationAddImage(dest, image, nil)
        XCTAssertTrue(CGImageDestinationFinalize(dest))
    }

    /// The owner's header (96 pt, centred, a speech bubble): thinking at rest (the first message clear of it),
    /// working scrolled (messages fade out under it) and needs you, in light and dark.
    func testRenderTheHeader() throws {
        try FileManager.default.createDirectory(at: Self.out, withIntermediateDirectories: true)
        let poses: [(String, ChatHeaderState, CGFloat)] = [
            ("thinking", ChatHeaderState(action: .thinking), 0),
            ("working", ChatHeaderState(action: .reading, running: 1), 90),
            ("needs-you", ChatHeaderState(action: .waiting, running: 1, needsYou: 1), 40),
        ]
        for (name, header, scrolled) in poses {
            for variant in [Snapshot.Variant.light, .dark] {
                let image = try XCTUnwrap(Snapshot.render(chat(.none, header: header, scrolled: scrolled),
                                                          variant: variant, padding: 0))
                try save(image, "header-\(name)-\(variant.rawValue).png")
            }
        }
    }

    /// The owner's choice of how messages look under the header: soft, medium and strong blur and fade, scrolled so a
    /// pink bubble and a text reply sit under the helper (light; medium also dark). The tiling stays whole.
    func testRenderTheBlurStrengths() throws {
        try FileManager.default.createDirectory(at: Self.out, withIntermediateDirectories: true)
        let header = ChatHeaderState(action: .thinking)
        let strengths: [(String, UnderHeader)] = [("soft", .soft), ("medium", .medium), ("strong", .strong)]
        for (name, under) in strengths {
            for variant in [Snapshot.Variant.light, .dark] where variant == .light || name == "medium" {
                let view = chat(.none, header: header, scrolled: 118, under: under)
                let image = try XCTUnwrap(Snapshot.render(view, variant: variant, padding: 0))
                try save(image, "under-\(name)-\(variant.rawValue).png")
            }
        }
    }

    /// The chat at an iPhone 17 Pro's width (402 pt) with the phone's top safe area (62 pt: status bar and Dynamic
    /// Island) reserved the way the phone reserves it, and the island drawn at its size: the helper's ears and its
    /// bubble must sit below it. A stand-in for a device shot (the test runner has no real status bar).
    func testRenderUnderTheDynamicIsland() throws {
        try FileManager.default.createDirectory(at: Self.out, withIntermediateDirectories: true)
        let view = chat(.none, header: ChatHeaderState(action: .reading, running: 1, needsYou: 1), scrolled: 118,
                        under: .soft)
            .safeAreaInset(edge: .top, spacing: 0) {
                ZStack(alignment: .top) {
                    HStack {
                        Text("22:16").font(.system(size: 17, weight: .semibold)).padding(.leading, 44).padding(.top, 17)
                        Spacer()
                    }
                    Capsule().fill(.black).frame(width: 126, height: 37).padding(.top, 11)
                }
                .frame(height: 62, alignment: .top)
                .frame(maxWidth: .infinity)
            }
        let image = try XCTUnwrap(Snapshot.render(view, variant: .light, width: 402, padding: 0))
        try save(image, "safe-area-17pro-light.png")
    }

    /// A frame strip of some mood picks: each row one mood, four frames across its loop, at 3x.
    func testRenderAMoodStrip() throws {
        try FileManager.default.createDirectory(at: Self.out, withIntermediateDirectories: true)
        let rows: [(String, String)] = [("done", "laughing"), ("needs you", "shocked"), ("a problem", "confused"),
                                        ("3+ quests", "dizzy"), ("free, now and then", "singing"),
                                        ("a long quest you stopped", "crying"), ("after 10 quiet minutes", "sleepy")]
        let sheet = SpriteSheet.giraffe
        let strip = VStack(alignment: .leading, spacing: 6) {
            ForEach(rows, id: \.1) { situation, mood in
                HStack(spacing: 4) {
                    let loop = sheet.loop(named: MoodBook.loop(mood))
                    ForEach([0, 560, 1120, 1680], id: \.self) { ms in
                        AvatarFrameView(sheet: sheet, frame: loop.frame(at: ms), box: 64)
                    }
                    Text("\(situation) → \(mood)").font(.footnote).padding(.leading, 8)
                }
            }
        }
        let image = try XCTUnwrap(Snapshot.render(strip, variant: .light, padding: 12, scale: 3))
        try save(image, "mood-strip.png")
    }

    /// Marcel's plain text sits right on the tiling: it must keep 4.5:1 on the ground, the outline and every fill.
    func testTextStaysReadableOnEveryBow() {
        for scheme in [ColorScheme.light, .dark] {
            let palette = ChatPalette(scheme)
            let ink = BowTiling.ink(scheme)
            for colour in [ink.ground, ink.outline] + ink.fills {
                XCTAssertGreaterThanOrEqual(palette.ink.contrast(with: colour), 4.5, "\(scheme): text on \(colour)")
                XCTAssertGreaterThanOrEqual(palette.muted.contrast(with: colour), 3.0, "\(scheme): muted on \(colour)")
            }
        }
    }

    /// The same bows are coloured on every launch.
    func testTheScatterIsFixed() {
        let first = (-6...6).flatMap { r in (-6...6).map { BowTiling.fill(column: $0, row: r, accents: .some) } }
        let again = (-6...6).flatMap { r in (-6...6).map { BowTiling.fill(column: $0, row: r, accents: .some) } }
        XCTAssertEqual(first, again)
        let coloured = first.compactMap { $0 }.count
        XCTAssertGreaterThan(coloured, 8)
        XCTAssertLessThan(coloured, 40)
    }
}
