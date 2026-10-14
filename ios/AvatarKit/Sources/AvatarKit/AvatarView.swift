import SwiftUI

/// The sizes the app shows the avatar at, in points. The box is square; the sprite sits in its middle.
public enum AvatarSize: CGFloat, CaseIterable, Sendable {
    case tiny = 24
    case small = 32
    case medium = 44
    case large = 120

    public var points: CGFloat { rawValue }

    /// Screen pixels per sprite pixel: the largest whole number that fits the frame in the box, and at least 1 so
    /// a sprite pixel is never dropped. Whole numbers keep every sprite pixel a crisp square.
    public func zoom(frameWidth: Int, frameHeight: Int, displayScale: CGFloat) -> Int {
        Self.zoom(box: points, frameWidth: frameWidth, frameHeight: frameHeight, displayScale: displayScale)
    }

    /// The same rule for a box of any size in points (a 44 pt box is 4× on a 3× screen for a 32 × 32 frame).
    public static func zoom(box points: CGFloat, frameWidth: Int, frameHeight: Int, displayScale: CGFloat) -> Int {
        let box = points * displayScale
        let fit = min(box / CGFloat(frameWidth), box / CGFloat(frameHeight))
        return max(1, Int(fit.rounded(.down)))
    }
}

/// An agent's avatar (the bundled giraffe unless given its own sheet), animated for `state`. Pauses while off screen
/// or in the background; with Reduce Motion it
/// shows each state's first frame and does not move.
public struct AvatarView: View {
    let state: AvatarState
    /// The square box, in points.
    let box: CGFloat
    let sheet: SpriteSheet
    /// The agent's name, for VoiceOver ("Marcel is thinking").
    let name: String

    @State private var player: AvatarPlayer
    @State private var onScreen = false
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.scenePhase) private var scenePhase

    public init(state: AvatarState, size: AvatarSize, sheet: SpriteSheet = .giraffe, name: String = "Marcel") {
        self.init(state: state, box: size.points, sheet: sheet, name: name)
    }

    /// A box of any size (the chat header's is 44 pt); the sprite still zooms in whole numbers.
    public init(state: AvatarState, box: CGFloat, sheet: SpriteSheet = .giraffe, name: String = "Marcel") {
        self.state = state
        self.box = box
        self.sheet = sheet
        self.name = name
        _player = State(initialValue: AvatarPlayer(sheet: sheet, state: state, at: Date.timeIntervalSinceReferenceDate))
    }

    /// Whether the timeline ticks. Off screen, in the background or with Reduce Motion, nothing redraws.
    static func animates(onScreen: Bool, reduceMotion: Bool, scenePhase: ScenePhase) -> Bool {
        onScreen && !reduceMotion && scenePhase == .active
    }

    /// The frame to draw at `time`: the player's frame, or with Reduce Motion the first frame of `state`.
    static func frame(player: AvatarPlayer, state: AvatarState, reduceMotion: Bool, at time: TimeInterval) -> Int {
        reduceMotion ? player.sheet.loop(for: state).firstFrame : player.frame(at: time)
    }

    public var body: some View {
        TimelineView(.animation(minimumInterval: 1.0 / 30,
                                paused: !Self.animates(onScreen: onScreen, reduceMotion: reduceMotion,
                                                       scenePhase: scenePhase))) { context in
            AvatarFrameView(sheet: sheet,
                            frame: Self.frame(player: player, state: state, reduceMotion: reduceMotion,
                                              at: context.date.timeIntervalSinceReferenceDate),
                            box: box)
        }
        .onAppear { onScreen = true }
        .onDisappear { onScreen = false }
        .onChange(of: state) { _, new in player.setState(new, at: Date.timeIntervalSinceReferenceDate) }
        // A new sheet (the agent's look changed, or its own sheet arrived): play it from the current state.
        .onChange(of: ObjectIdentifier(sheet)) {
            player = AvatarPlayer(sheet: sheet, state: state, at: Date.timeIntervalSinceReferenceDate)
        }
        .accessibilityElement()
        .accessibilityLabel(state.accessibilityDescription(name: name))
    }
}

/// One frame of the sheet in a `size` box, at a whole-number zoom with nearest-neighbour scaling. No animation:
/// `AvatarView` draws it every frame, and it is what the snapshot tests render.
public struct AvatarFrameView: View {
    let sheet: SpriteSheet
    let frame: Int
    /// The square box, in points.
    let box: CGFloat
    @Environment(\.displayScale) private var displayScale

    public init(sheet: SpriteSheet = .giraffe, frame: Int, size: AvatarSize) {
        self.init(sheet: sheet, frame: frame, box: size.points)
    }

    /// A box of any size in points; the sprite zooms by the largest whole number that fits.
    public init(sheet: SpriteSheet = .giraffe, frame: Int, box: CGFloat) {
        self.sheet = sheet
        self.frame = frame
        self.box = box
    }

    public var body: some View {
        let zoom = CGFloat(AvatarSize.zoom(box: box, frameWidth: sheet.frameWidth, frameHeight: sheet.frameHeight,
                                           displayScale: displayScale))
        Image(decorative: sheet.image(frame: frame), scale: displayScale)
            .interpolation(.none)
            .antialiased(false)
            .resizable()
            .frame(width: CGFloat(sheet.frameWidth) * zoom / displayScale,
                   height: CGFloat(sheet.frameHeight) * zoom / displayScale)
            .frame(width: box, height: box)
    }
}
