import QuartzCore
import RealityKit
import UIKit

enum AvatarStyle: String, CaseIterable, Identifiable {
    case unlit = "Unlit"   // the flat logo look
    case pbr = "PBR"       // soft depth from lighting
    var id: String { rawValue }
}

enum AvatarMode: String, CaseIterable, Identifiable {
    case idle = "Idle"
    case working = "Working(3)"
    var id: String { rawValue }
}

/// What the animation system should be doing right now. The SwiftUI view writes it, the system reads it.
final class AvatarControl {
    static let shared = AvatarControl()
    var mode: AvatarMode = .idle
    var style: AvatarStyle = .unlit
    var glowColor: UIColor = .orange
    var fixApplied = true
}

/// Attached to every box of the giraffe.
struct AvatarPartComponent: Component {
    enum Anim { case none, blink, spot }
    var baseColor: UIColor
    var anim: Anim = .none
    var glowsWhenWorking = false
    var glow: Float = 0          // 0...1, eased towards its target each frame
    var lastDrawnGlow: Float = -1
    var lastDrawnStyle: AvatarStyle?
}

/// Attached to the torso group: the thing that breathes.
struct BreathComponent: Component {}

enum AvatarMaterials {
    /// On the iOS 18.5 simulator RealityKit draws flat (unlit) colours darker than asked for
    /// (sRGB 192 comes out as about 168, and plain white as about 214). These measured points
    /// (asked-for value, value on screen, 0...255 scale) let us ask for a brighter colour so the
    /// screen shows the palette colour. Values above 255 are accepted by UnlitMaterial.
    /// To be checked on the phone: switch the fix off in the app and compare.
    static var brightnessFix = true
    private static let curve: [(ask: CGFloat, got: CGFloat)] = [
        (0, 0), (32, 17), (64, 49), (96, 81), (128, 110), (160, 140), (190, 167), (200, 174), (210, 183),
        (220, 189), (230, 197), (240, 203), (250, 210), (255, 214), (265, 219), (280, 228), (295, 235),
        (310, 242), (330, 251), (345, 255),
    ]

    /// The colour to hand to UnlitMaterial so the screen shows `target`.
    static func flatColor(_ target: UIColor) -> UIColor {
        guard brightnessFix else { return target }
        var r: CGFloat = 0, g: CGFloat = 0, b: CGFloat = 0, a: CGFloat = 0
        target.getRed(&r, green: &g, blue: &b, alpha: &a)
        func ask(_ v: CGFloat) -> CGFloat {
            let want = v * 255
            for i in 1..<curve.count where want <= curve[i].got {
                let lo = curve[i - 1], hi = curve[i]
                return (lo.ask + (hi.ask - lo.ask) * (want - lo.got) / (hi.got - lo.got)) / 255
            }
            return curve.last!.ask / 255
        }
        return UIColor(red: ask(r), green: ask(g), blue: ask(b), alpha: 1)
    }

    static func make(base: UIColor, glow: Float, style: AvatarStyle, glowColor: UIColor) -> Material {
        switch style {
        case .unlit:
            return UnlitMaterial(color: flatColor(base.mixed(with: glowColor, CGFloat(glow))))
        case .pbr:
            var m = PhysicallyBasedMaterial()
            m.baseColor = .init(tint: base)
            m.roughness = 0.65
            m.metallic = 0.0
            if glow > 0 {
                m.emissiveColor = .init(color: glowColor)
                m.emissiveIntensity = glow * 2.5
            }
            return m
        }
    }
}

/// Idle (breathing + blinking) and working(3) (three spots glow), one pass per frame.
struct AvatarSystem: System {
    static let partQuery = EntityQuery(where: .has(AvatarPartComponent.self))
    static let breathQuery = EntityQuery(where: .has(BreathComponent.self))
    static let startTime = CACurrentMediaTime()

    static let breathPeriod: Float = 4.0
    static let breathDepth: Float = 0.015
    static let blinkPeriod: Float = 4.2
    static let blinkLength: Float = 0.14
    static let pulsePeriod: Float = 1.2

    init(scene: RealityKit.Scene) {}

    func update(context: SceneUpdateContext) {
        let control = AvatarControl.shared
        let t = Float(CACurrentMediaTime() - Self.startTime)
        let dt = Float(context.deltaTime)

        for torso in context.entities(matching: Self.breathQuery, updatingSystemWhen: .rendering) {
            let k = 1 + Self.breathDepth * 0.5 * (1 - cos(2 * .pi * t / Self.breathPeriod))
            torso.scale = SIMD3<Float>(1, k, 1 + (k - 1) * 0.5)
        }

        let working = control.mode == .working
        let pulse = 0.72 + 0.28 * sin(2 * .pi * t / Self.pulsePeriod)

        for entity in context.entities(matching: Self.partQuery, updatingSystemWhen: .rendering) {
            guard var part = entity.components[AvatarPartComponent.self] else { continue }

            if part.anim == .blink {
                let phase = t.truncatingRemainder(dividingBy: Self.blinkPeriod)
                let open: Float = phase < Self.blinkLength ? 1 - 0.9 * sin(.pi * phase / Self.blinkLength) : 1
                entity.scale = SIMD3<Float>(1, open, 1)
            }

            if part.anim == .spot {
                let target: Float = (working && part.glowsWhenWorking) ? pulse : 0
                part.glow += (target - part.glow) * min(1, dt * 8)
                if abs(part.glow) < 0.002 { part.glow = 0 }
                let changed = abs(part.glow - part.lastDrawnGlow) > 0.004 || part.lastDrawnStyle != control.style
                if changed {
                    entity.components[ModelComponent.self]?.materials = [
                        AvatarMaterials.make(base: part.baseColor, glow: part.glow,
                                             style: control.style, glowColor: control.glowColor)
                    ]
                    part.lastDrawnGlow = part.glow
                    part.lastDrawnStyle = control.style
                }
                entity.components.set(part)
            }
        }
    }
}
