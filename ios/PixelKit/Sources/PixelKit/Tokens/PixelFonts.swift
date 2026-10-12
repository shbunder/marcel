import CoreText
import SwiftUI

#if canImport(UIKit)
import UIKit
#endif

/// The two bundled OFL fonts (licences next to them in `Resources/Fonts`).
/// - **Silkscreen** for labels and headings: shown in capitals, scales with Dynamic Type up to a cap so a label
///   never outgrows its box.
/// - **Pixelify Sans** for body text: scales with Dynamic Type without a cap (B-29a: body stays readable).
public enum PixelFonts {
    public static let labelFace = "Silkscreen-Regular"
    public static let bodyFace = "PixelifySans-Regular"

    /// Registers the fonts with the process. Safe to call more than once; components call it themselves.
    @discardableResult
    public static func register() -> Bool { registered }

    private static let registered: Bool = {
        guard let folder = Bundle.module.url(forResource: "Fonts", withExtension: nil),
              let files = try? FileManager.default.contentsOfDirectory(at: folder, includingPropertiesForKeys: nil)
        else { return false }
        var ok = true
        for file in files where file.pathExtension == "ttf" {
            var error: Unmanaged<CFError>?
            if !CTFontManagerRegisterFontsForURL(file as CFURL, .process, &error) {
                // Already registered (e.g. a second bundle copy) is fine; anything else is a missing font.
                let code = (error?.takeRetainedValue()).map { CFErrorGetCode($0) } ?? 0
                if code != CTFontManagerError.alreadyRegistered.rawValue { ok = false }
            }
        }
        return ok
    }()
}

/// The type roles. Sizes are the wireframes' sizes scaled from their 290-pt phone to a 390-pt iPhone.
public enum PixelTextRole: Sendable, CaseIterable {
    /// Header titles ("Marcel", "Quest log"). Silkscreen.
    case title
    /// Card titles, artifact headers, choices. Silkscreen.
    case label
    /// Speaker labels, badges, chips, tabs, section labels. Silkscreen.
    case smallLabel
    /// Dialogue and message text. Pixelify Sans.
    case body
    /// Card details and subtitles. Pixelify Sans.
    case detail

    /// Size at the default Dynamic Type setting ("Large").
    public var baseSize: CGFloat {
        switch self {
        case .title: 16
        case .label: 14
        case .smallLabel: 12
        case .body: 17
        case .detail: 14
        }
    }

    /// Labels stop growing here; body text has no cap.
    public var maximumSize: CGFloat? {
        switch self {
        case .title: 24
        case .label: 20
        case .smallLabel: 18
        case .body, .detail: nil
        }
    }

    public var isLabel: Bool { maximumSize != nil }


    /// The point size this role is drawn at for a Dynamic Type setting.
    public func size(for dynamicType: DynamicTypeSize) -> CGFloat {
        let scaled = baseSize * Self.scale(for: dynamicType)
        return maximumSize.map { min(scaled, $0) } ?? scaled
    }

    /// How much Apple's body style grows at each Dynamic Type setting (17 pt at "Large").
    static func scale(for size: DynamicTypeSize) -> CGFloat {
        let body: CGFloat = switch size {
        case .xSmall: 14
        case .small: 15
        case .medium: 16
        case .large: 17
        case .xLarge: 19
        case .xxLarge: 21
        case .xxxLarge: 23
        case .accessibility1: 28
        case .accessibility2: 33
        case .accessibility3: 40
        case .accessibility4: 47
        case .accessibility5: 53
        @unknown default: 17
        }
        return body / 17
    }
}

struct PixelTextModifier: ViewModifier {
    let role: PixelTextRole
    let capitals: Bool
    @Environment(\.dynamicTypeSize) private var dynamicType

    func body(content: Content) -> some View {
        PixelFonts.register()
        let size = role.size(for: dynamicType)
        return Group {
            if role.isLabel {
                content
                    .font(.custom(PixelFonts.labelFace, fixedSize: size))
                    .textCase(capitals ? .uppercase : nil)
                    .lineSpacing(size * 0.2)
            } else {
                content
                    .font(.custom(PixelFonts.bodyFace, fixedSize: size))
                    .lineSpacing(size * 0.15)
            }
        }
    }
}

extension View {
    /// Sets the pixel font for a type role. Labels are drawn in capitals unless `capitals` is false; VoiceOver
    /// still reads the words as written, so pass sentence-case text ("Needs you", not "NEEDS YOU").
    public func pixelText(_ role: PixelTextRole, capitals: Bool = true) -> some View {
        modifier(PixelTextModifier(role: role, capitals: capitals))
    }
}
