import SwiftUI

// Settings the snapshot tests of every screen package pin (through `@testable import PixelKit`). They lived with the
// pixel dialogue box and its choice list until S-18.6 retired those; the tests still set them, so they stay here.
extension EnvironmentValues {
    /// Tests pin Reduce Motion with this; the system setting is read-only.
    @Entry var pixelReduceMotionOverride: Bool? = nil
    /// Tests pin a blinking marker's clock with this, so a render shows a known half of the blink.
    @Entry var pixelBlinkDate: Date? = nil
    /// True while a view is drawn by `ImageRenderer` (the snapshot tests).
    @Entry var pixelRendersOffscreen = false
}
