import RealityKit
import UIKit

enum GiraffeBuilder {
    /// Builds the giraffe with its hip line at the origin; the feet end up just below it.
    @MainActor
    static func build(_ recipe: GiraffeRecipe, style: AvatarStyle) -> Entity {
        let root = Entity()
        root.name = "giraffe"

        let torso = Entity()
        torso.name = "torso"
        torso.components.set(BreathComponent())
        let legs = Entity()
        legs.name = "legs"
        root.addChild(legs)
        root.addChild(torso)

        let s = recipe.unit
        for part in recipe.parts {
            let r = part.rect
            let size = SIMD3<Float>((r[2] - r[0]) * s, (r[3] - r[1]) * s, part.depth * s)
            let radius = min(part.radius * s, min(size.x, size.y, size.z) / 2 - 0.0005)
            let base = recipe.color(part.color)
            let glows = recipe.glow.parts.contains(part.name)

            let entity = ModelEntity(
                mesh: .generateBox(size: size, cornerRadius: radius),
                materials: [AvatarMaterials.make(base: base, glow: 0, style: style, glowColor: recipe.color(recipe.glow.color))]
            )
            entity.name = part.name
            entity.position = SIMD3<Float>(
                ((r[0] + r[2]) / 2 - recipe.originX) * s,
                (recipe.hipY - (r[1] + r[3]) / 2) * s,
                part.z * s
            )
            if let deg = part.rotZ {
                entity.orientation = simd_quatf(angle: deg * .pi / 180, axis: [0, 0, 1])
            }

            var component = AvatarPartComponent(baseColor: base)
            component.glowsWhenWorking = glows
            component.lastDrawnStyle = style
            switch part.anim {
            case "blink": component.anim = .blink
            case "spot": component.anim = .spot
            default: break
            }
            entity.components.set(component)

            (part.group == "legs" ? legs : torso).addChild(entity)
        }
        return root
    }

    /// Re-dress every still box when the material style changes. Spots restyle themselves in the system.
    @MainActor
    static func restyle(_ root: Entity, style: AvatarStyle, glowColor: UIColor) {
        func walk(_ e: Entity) {
            if let part = e.components[AvatarPartComponent.self], part.anim != .spot {
                e.components[ModelComponent.self]?.materials = [
                    AvatarMaterials.make(base: part.baseColor, glow: 0, style: style, glowColor: glowColor)
                ]
            }
            e.children.forEach(walk)
        }
        walk(root)
    }
}
