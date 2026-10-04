import RealityKit
import SwiftUI

/// Version 1: the giraffe from rounded boxes (kept for comparison with the new looks).
struct BoxLookView: View {
    var style: AvatarStyle
    var mode: AvatarMode
    var yaw: Bool
    var brightnessFix: Bool
    private let recipe = GiraffeRecipe.load()
    private let snow = UIColor(hex: "#faf5f8")

    var body: some View {
        RealityView { content in
                content.camera = .virtual
                AvatarMaterials.brightnessFix = brightnessFix
                // Colours must reach the screen as written, so switch off every camera effect.
                content.renderingEffects.dynamicRange = .standard
                content.renderingEffects.cameraGrain = .disabled
                content.renderingEffects.motionBlur = .disabled
                content.renderingEffects.depthOfField = .disabled

                // iOS 18 has no plain-colour background, so the brand "Snow" is a big unlit panel behind the giraffe.
                let backdrop = ModelEntity(mesh: .generatePlane(width: 8, height: 8),
                                           materials: [UnlitMaterial(color: AvatarMaterials.flatColor(snow))])
                backdrop.name = "backdrop"
                backdrop.position = [0, 0.31, -1.0]
                content.add(backdrop)

                // A narrow lens from far away keeps the unlit look close to the flat logo.
                let camera = PerspectiveCamera()
                camera.camera.fieldOfViewInDegrees = 22
                camera.position = [0, 0.31, 3.0]
                content.add(camera)

                let key = DirectionalLight()
                key.light.intensity = 300
                key.look(at: .zero, from: [0.8, 1.2, 2.0], relativeTo: nil)
                content.add(key)
                let fill = DirectionalLight()
                fill.light.intensity = 60
                fill.look(at: .zero, from: [-1.5, 0.3, 1.0], relativeTo: nil)
                content.add(fill)

                AvatarControl.shared.glowColor = recipe.color(recipe.glow.color)
                AvatarControl.shared.style = style
                AvatarMaterials.brightnessFix = brightnessFix
                AvatarControl.shared.fixApplied = brightnessFix
                content.add(GiraffeBuilder.build(recipe, style: style))
            } update: { content in
                AvatarControl.shared.mode = mode
                AvatarMaterials.brightnessFix = brightnessFix
                guard let giraffe = content.entities.first(where: { $0.name == "giraffe" }) else { return }
                if AvatarControl.shared.style != style || AvatarControl.shared.fixApplied != brightnessFix {
                    AvatarControl.shared.fixApplied = brightnessFix
                    AvatarControl.shared.style = style
                    GiraffeBuilder.restyle(giraffe, style: style, glowColor: AvatarControl.shared.glowColor)
                    (content.entities.first(where: { $0.name == "backdrop" }) as? ModelEntity)?.model?.materials =
                        [UnlitMaterial(color: AvatarMaterials.flatColor(snow))]
                }
                giraffe.orientation = simd_quatf(angle: yaw ? -.pi / 7 : 0, axis: [0, 1, 0])
            }
            .ignoresSafeArea()
    }
}
