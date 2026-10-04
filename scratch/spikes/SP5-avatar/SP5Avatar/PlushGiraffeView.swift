import RealityKit
import SwiftUI
import UIKit

/// The plush giraffe: soft ellipsoids instead of boxes, a few fuzzy "shell" layers over the big parts,
/// floppy ears, a swinging tail and horns, and a head that looks around. Same layout as the logo.
struct PlushGiraffeView: View {
    var mode: AvatarMode
    var yaw: Bool
    private let snow = UIColor(hex: "#faf5f8")

    var body: some View {
        RealityView { content in
            content.camera = .virtual
            content.renderingEffects.dynamicRange = .standard
            content.renderingEffects.cameraGrain = .disabled
            content.renderingEffects.motionBlur = .disabled
            content.renderingEffects.depthOfField = .disabled

            let backdrop = ModelEntity(mesh: .generatePlane(width: 8, height: 8),
                                       materials: [UnlitMaterial(color: AvatarMaterials.flatColor(snow))])
            backdrop.position = [0, 0.62, -1.4]
            content.add(backdrop)

            let camera = PerspectiveCamera()
            camera.camera.fieldOfViewInDegrees = 22
            camera.position = [0, 0.62, 4.4]
            content.add(camera)

            let key = DirectionalLight()
            key.light.intensity = PlushLook.keyIntensity
            key.look(at: .zero, from: [0.8, 1.4, 2.2], relativeTo: nil)
            content.add(key)
            let fill = DirectionalLight()
            fill.light.intensity = PlushLook.fillIntensity
            fill.look(at: .zero, from: [-1.6, 0.4, 1.0], relativeTo: nil)
            content.add(fill)
            let rim = DirectionalLight()                                   // from behind: makes the fuzz edge glow
            rim.light.intensity = PlushLook.rimIntensity
            rim.look(at: .zero, from: [0.2, 0.9, -2.0], relativeTo: nil)
            content.add(rim)

            content.add(PlushBuilder.build())
        } update: { content in
            AvatarControl.shared.mode = mode
            if let plush = content.entities.first(where: { $0.name == "plush" }) {
                plush.orientation = simd_quatf(angle: yaw ? -.pi / 5 : -.pi / 12, axis: [0, 1, 0])
            }
        }
        .ignoresSafeArea()
    }
}

enum PlushLook {
    static var keyIntensity: Float = 1300
    static var fillIntensity: Float = 380
    static var rimIntensity: Float = 700
    static var furLayers = 5
    static var furThickness: Float = 0.045   // how far the outermost layer sits beyond the surface, as a fraction of size
}

// MARK: - Motion data

struct PlushMotion: Component {
    enum Kind {
        case torso, head, eye, ear(side: Float), horn(index: Int), tail(index: Int), spot(index: Int), none
    }
    var kind: Kind
    var basePosition: SIMD3<Float> = .zero
    var baseScale: SIMD3<Float> = .one
    var baseOrientation = simd_quatf(ix: 0, iy: 0, iz: 0, r: 1)
    var color: UIColor = .white
    var glows = false
    var glow: Float = 0
    var lastDrawnGlow: Float = -1
}

// MARK: - Building

enum PlushBuilder {
    static let rose = UIColor(hex: "#cc5e76")
    static let muzzle = UIColor(hex: "#d4748a")
    static let blush = UIColor(hex: "#e48599")
    static let maroon = UIColor(hex: "#8e3447")
    static let orange = UIColor(hex: "#f6aa1c")

    static func material(_ color: UIColor, glow: Float = 0) -> PhysicallyBasedMaterial {
        var m = PhysicallyBasedMaterial()
        m.baseColor = .init(tint: color)
        m.roughness = 0.95
        m.metallic = 0.0
        if glow > 0 { m.emissiveColor = .init(color: orange); m.emissiveIntensity = glow * 2.5 }
        return m
    }

    /// The spot glow in nine fixed steps, made once. Making a new material every frame caused visible frame drops.
    static let glowSteps = 8
    static let glowMaterials: [PhysicallyBasedMaterial] = (0...glowSteps).map { material(maroon, glow: Float($0) / Float(glowSteps)) }

    /// Small deterministic random numbers so the fur looks the same every launch.
    struct RNG {
        var state: UInt64
        mutating func next() -> Double {
            state &+= 0x9E3779B97F4A7C15
            var z = state
            z = (z ^ (z >> 30)) &* 0xBF58476D1CE4E5B9
            z = (z ^ (z >> 27)) &* 0x94D049BB133111EB
            return Double(z ^ (z >> 31)) / Double(UInt64.max)
        }
    }

    /// One transparent texture per fur layer: white fibres that thin out towards the tip. Tinted by the part colour.
    @MainActor
    static func furTextures() -> [TextureResource] {
        let n = PlushLook.furLayers, size = 512
        var rng = RNG(state: 42)
        return (0..<n).compactMap { layer in
            guard let ctx = CGContext(data: nil, width: size, height: size, bitsPerComponent: 8, bytesPerRow: 0,
                                      space: CGColorSpace(name: CGColorSpace.sRGB)!,
                                      bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { return nil }
            ctx.clear(CGRect(x: 0, y: 0, width: size, height: size))
            let density = 1.0 - Double(layer) / Double(n) * 0.75
            for _ in 0..<Int(14000 * density) {
                let x = rng.next() * Double(size), y = rng.next() * Double(size)
                let r = 1.1 + rng.next() * 1.3
                let shade = 0.88 + rng.next() * 0.12
                ctx.setFillColor(CGColor(red: shade, green: shade, blue: shade, alpha: 0.9))
                ctx.fillEllipse(in: CGRect(x: x - r, y: y - r, width: r * 2, height: r * 2))
            }
            guard let image = ctx.makeImage() else { return nil }
            return try? TextureResource(image: image, options: .init(semantic: .color))
        }
    }

    static func furMaterial(color: UIColor, layer: Int, texture: TextureResource) -> PhysicallyBasedMaterial {
        var m = PhysicallyBasedMaterial()
        let tip = CGFloat(layer + 1) / CGFloat(PlushLook.furLayers)
        m.baseColor = .init(tint: color.mixed(with: .white, tip * 0.18), texture: .init(texture))
        m.roughness = 1.0
        m.metallic = 0.0
        m.blending = .transparent(opacity: .init(scale: 1, texture: .init(texture)))
        return m
    }

    @MainActor
    static func ellipsoid(_ name: String, size: SIMD3<Float>, at p: SIMD3<Float>, color: UIColor, fur: [TextureResource]? = nil,
                          kind: PlushMotion.Kind = .none) -> ModelEntity {
        let e = ModelEntity(mesh: .generateSphere(radius: 0.5), materials: [material(color)])
        e.name = name
        e.scale = size
        e.position = p
        if let fur {
            for (i, tex) in fur.enumerated() {
                let shell = ModelEntity(mesh: .generateSphere(radius: 0.5), materials: [furMaterial(color: color, layer: i, texture: tex)])
                shell.scale = SIMD3<Float>(repeating: 1 + PlushLook.furThickness * Float(i + 1) / Float(fur.count))
                e.addChild(shell)
            }
        }
        var m = PlushMotion(kind: kind)
        m.basePosition = e.position; m.baseScale = e.scale; m.color = color
        e.components.set(m)
        return e
    }

    @MainActor
    static func pivot(_ name: String, at p: SIMD3<Float>, kind: PlushMotion.Kind) -> Entity {
        let e = Entity()
        e.name = name
        e.position = p
        var m = PlushMotion(kind: kind)
        m.basePosition = p
        e.components.set(m)
        return e
    }

    /// z of the surface of an ellipsoid at (x, y), so flat patches sit on the fur and not inside it.
    static func surfaceZ(x: Float, y: Float, center c: SIMD3<Float>, half h: SIMD3<Float>) -> Float {
        let u = (x - c.x) / h.x, v = (y - c.y) / h.y
        return h.z * sqrt(max(0.05, 1 - u * u - v * v))
    }

    @MainActor
    static func build() -> Entity {
        let fur = furTextures()
        let root = Entity()
        root.name = "plush"

        // --- legs: soft capsules, far pair dark like the logo ---
        let legs = Entity(); legs.name = "legs"
        for (x, z, c) in [(-0.07, -0.085, maroon), (0.17, -0.085, maroon), (-0.17, 0.085, rose), (0.03, 0.085, rose)] as [(Float, Float, UIColor)] {
            let leg = ModelEntity(mesh: .generateBox(size: [0.09, 0.22, 0.09], cornerRadius: 0.043), materials: [material(c)])
            leg.position = [x, 0.11, z]
            if c == rose {
                for (i, tex) in fur.enumerated() {
                    let shell = ModelEntity(mesh: .generateBox(size: [0.09, 0.22, 0.09], cornerRadius: 0.043), materials: [furMaterial(color: c, layer: i, texture: tex)])
                    shell.scale = SIMD3<Float>(repeating: 1 + PlushLook.furThickness * 1.6 * Float(i + 1) / Float(fur.count))
                    leg.addChild(shell)
                }
            }
            legs.addChild(leg)
        }
        root.addChild(legs)

        // --- torso group (breathes) ---
        let torso = pivot("torso", at: [0, 0.0, 0], kind: .torso)
        root.addChild(torso)

        let bodyC = SIMD3<Float>(0, 0.32, 0), bodyH = SIMD3<Float>(0.25, 0.185, 0.17)
        let neckC = SIMD3<Float>(0.145, 0.68, 0), neckH = SIMD3<Float>(0.095, 0.31, 0.095)
        torso.addChild(ellipsoid("body", size: bodyH * 2, at: bodyC, color: rose, fur: fur))
        torso.addChild(ellipsoid("neck", size: neckH * 2, at: neckC, color: rose, fur: fur))

        // spots, on both sides so a turned view still shows them
        let spotSpecs: [(Float, Float, Bool)] = [(0.19, 0.84, true), (0.105, 0.70, true), (0.175, 0.53, true),
                                                 (-0.07, 0.37, false), (0.03, 0.27, false), (-0.17, 0.26, false)]
        for (i, s) in spotSpecs.enumerated() {
            for side: Float in [1, -1] {
                let z = surfaceZ(x: s.0, y: s.1, center: s.2 ? neckC : bodyC, half: s.2 ? neckH : bodyH) * side
                let spot = ModelEntity(mesh: .generateSphere(radius: 0.5), materials: [material(maroon)])
                spot.name = "spot-\(i + 1)"
                spot.scale = [0.062, 0.062, 0.03]
                spot.position = [s.0, s.1, z + side * 0.006]   // proud of the fur, like a felt patch
                var m = PlushMotion(kind: .spot(index: i))
                m.color = maroon
                m.glows = [0, 2, 4].contains(i)
                spot.components.set(m)
                torso.addChild(spot)
            }
        }

        // tail: three soft segments that swing one after the other, maroon tuft at the end
        let tail = pivot("tail-0", at: [0.245, 0.40, 0], kind: .tail(index: 0))
        torso.addChild(tail)
        var parent = tail
        for i in 0..<3 {
            let seg = ellipsoid("tail-seg-\(i)", size: [0.05 - Float(i) * 0.006, 0.11, 0.05 - Float(i) * 0.006], at: [0, -0.05, 0], color: rose)
            parent.addChild(seg)
            if i < 2 {
                let joint = pivot("tail-\(i + 1)", at: [0, -0.10, 0], kind: .tail(index: i + 1))
                parent.addChild(joint)
                parent = joint
            } else {
                parent.addChild(ellipsoid("tuft", size: [0.075, 0.11, 0.075], at: [0, -0.14, 0], color: maroon, fur: Array(fur.prefix(3))))
            }
        }

        // --- head group ---
        let headOrigin = SIMD3<Float>(0.145, 0.95, 0)
        let head = pivot("headGroup", at: headOrigin, kind: .head)
        torso.addChild(head)
        func h(_ p: SIMD3<Float>) -> SIMD3<Float> { p - headOrigin }

        let headC = SIMD3<Float>(-0.03, 1.03, 0), headH = SIMD3<Float>(0.25, 0.12, 0.13)
        head.addChild(ellipsoid("head", size: headH * 2, at: h(headC), color: rose, fur: fur))
        head.addChild(ellipsoid("muzzle", size: [0.17, 0.15, 0.17], at: h([-0.23, 1.0, 0]), color: muzzle, fur: Array(fur.prefix(3))))
        for side: Float in [1, -1] {
            let eye = ellipsoid("eye", size: [0.052, 0.052, 0.052], at: h([0.0, 1.06, 0.118 * side]), color: .black, kind: .eye)
            let glint = ModelEntity(mesh: .generateSphere(radius: 0.5), materials: [UnlitMaterial(color: .white)])
            glint.scale = [0.3, 0.3, 0.3]
            glint.position = [-0.13, 0.11, 0.40 * side]
            eye.addChild(glint)
            head.addChild(eye)
            head.addChild(ellipsoid("nostril", size: [0.022, 0.036, 0.03], at: h([-0.292, 0.995, 0.058 * side]), color: .black))
            head.addChild(ellipsoid("cheek", size: [0.055, 0.035, 0.02], at: h([-0.12, 0.975, 0.118 * side]), color: blush))

            // floppy ear: pivot at the base, ellipsoid hangs out sideways
            let ear = pivot("ear", at: h([-0.085, 1.115, 0.09 * side]), kind: .ear(side: side))
            var em = ear.components[PlushMotion.self]!
            em.baseOrientation = simd_quatf(angle: 0.7 * side, axis: [1, 0, 0])
            ear.components.set(em)
            ear.orientation = em.baseOrientation
            ear.addChild(ellipsoid("ear-flap", size: [0.05, 0.075, 0.022], at: [0, 0.03, 0], color: rose))
            ear.addChild(ellipsoid("ear-inner", size: [0.03, 0.05, 0.016], at: [0, 0.03, 0.006 * side], color: blush))
            head.addChild(ear)
        }
        // horns: a soft stem with a round maroon tip, each sways a little behind the head
        for (i, x) in [Float(0.05), 0.145].enumerated() {
            let horn = pivot("horn-\(i)", at: h([x, 1.135, 0]), kind: .horn(index: i))
            horn.addChild(ellipsoid("horn-stem", size: [0.036, 0.17, 0.036], at: [0, 0.075, 0], color: maroon))
            horn.addChild(ellipsoid("horn-tip", size: [0.06, 0.06, 0.06], at: [0, 0.165, 0], color: maroon, fur: Array(fur.prefix(3))))
            head.addChild(horn)
        }

        // soft ground shadow
        let shadow = ModelEntity(mesh: .generateSphere(radius: 0.5), materials: [UnlitMaterial(color: AvatarMaterials.flatColor(UIColor(hex: "#eadde3")))])
        shadow.scale = [0.62, 0.004, 0.40]
        shadow.position = [0.0, 0.0, 0.0]
        root.addChild(shadow)
        return root
    }
}

// MARK: - Animation

struct PlushSystem: System {
    static let query = EntityQuery(where: .has(PlushMotion.self))
    static let start = CACurrentMediaTime()

    init(scene: RealityKit.Scene) {}

    /// 0...1 progress while a repeating event is happening, nil the rest of the time.
    static func event(_ t: Double, _ period: Double, offset: Double = 0, length: Double) -> Double? {
        let p = (t + offset).truncatingRemainder(dividingBy: period)
        return p < length ? p / length : nil
    }
    /// A smooth bump: 0 at the ends of the event, 1 in the middle.
    static func bump(_ p: Double?) -> Float { p.map { Float(sin($0 * .pi)) } ?? 0 }

    func update(context: SceneUpdateContext) {
        let t = CACurrentMediaTime() - Self.start
        let tf = Float(t)
        let working = AvatarControl.shared.mode == .working
        let dt = Float(context.deltaTime)

        func glanceAmount(_ time: Double) -> Float { Self.bump(Self.event(time, 9.1, offset: 3.0, length: 1.6)) }
        let glance = glanceAmount(t)
        let nod = Self.bump(Self.event(t, 7.3, offset: 1.2, length: 0.8))
        let focus: Float = working ? 1 : 0

        for e in context.entities(matching: Self.query, updatingSystemWhen: .rendering) {
            guard var m = e.components[PlushMotion.self] else { continue }
            switch m.kind {
            case .torso:
                let breath = 0.5 * (1 - cos(2 * .pi * tf / 3.6))
                e.scale = [1 + 0.012 * breath, 1 + 0.02 * breath, 1 + 0.012 * breath]
                e.orientation = simd_quatf(angle: 0.012 * sin(2 * .pi * tf / 7.0), axis: [0, 0, 1])   // slow weight shift
            case .head:
                let yawA = 0.10 * sin(2 * .pi * tf / 6.5) + 0.34 * glance
                let pitchA = 0.035 * sin(2 * .pi * tf / 3.6 + 1) + 0.14 * nod + 0.22 * focus
                e.orientation = simd_quatf(angle: yawA, axis: [0, 1, 0]) * simd_quatf(angle: pitchA, axis: [0, 0, 1])
            case .eye:
                let b = Self.event(t, 3.6, offset: 0.9, length: 0.18) ?? Self.event(t, 11.0, offset: 0.4, length: 0.18)
                let open = 1 - 0.92 * Self.bump(b)
                let squint: Float = 1 - 0.4 * focus
                e.scale = [m.baseScale.x, m.baseScale.y * open * squint, m.baseScale.z]
                e.position = m.basePosition + [-0.006 * glance, 0, 0]
            case .ear(let side):
                let flick = Self.bump(Self.event(t, 5.2, offset: 0.4, length: 0.35))
                let droop = 0.08 * sin(2 * .pi * tf / 3.1 + side)
                e.orientation = simd_quatf(angle: -0.55 * flick * side + droop * side, axis: [1, 0, 0]) * m.baseOrientation
            case .horn(let i):
                let lag = glanceAmount(t - 0.18)
                e.orientation = simd_quatf(angle: -0.10 * (lag - glance) + 0.05 * sin(2 * .pi * tf / 2.4 + Float(i)), axis: [0, 0, 1])
            case .tail(let i):
                let swing = 0.30 * sin(2 * .pi * tf / 2.6 - Float(i) * 0.7)
                e.orientation = simd_quatf(angle: swing + (i == 0 ? 0.35 : 0), axis: [0, 0, 1])
            case .spot:
                let pulse = 0.72 + 0.28 * sin(2 * .pi * tf / 1.2 - Float(m.glows ? 0 : 0))
                let target: Float = (working && m.glows) ? pulse : 0
                m.glow += (target - m.glow) * min(1, dt * 8)
                if abs(m.glow) < 0.002 { m.glow = 0 }
                let step = Float((m.glow * Float(PlushBuilder.glowSteps)).rounded())
                if step != m.lastDrawnGlow {
                    e.components[ModelComponent.self]?.materials = [PlushBuilder.glowMaterials[Int(step)]]
                    m.lastDrawnGlow = step
                }
                e.components.set(m)
            case .none:
                break
            }
        }
    }
}
