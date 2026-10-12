/// The pixel-classic design system (B-29a): colour tokens for a light and a dark theme (`PixelTheme`), the 2-pt
/// pixel grid (`PixelMetrics`), the bundled Silkscreen and Pixelify Sans fonts (`PixelFonts`, `pixelText(_:)`), and
/// the components the app is drawn with. See README.md.
public enum PixelKit {
    /// Registers the bundled fonts. Components do this themselves; calling it at launch just does it early.
    @discardableResult
    public static func registerFonts() -> Bool { PixelFonts.register() }
}
