import RealityKit
import SwiftUI

struct ContentView: View {
    // Launch arguments (-style pbr -mode working -yaw 1 -hud 0) set the starting state, for repeatable screenshots.
    @State private var style: AvatarStyle = UserDefaults.standard.string(forKey: "style") == "pbr" ? .pbr : .unlit
    @State private var mode: AvatarMode = UserDefaults.standard.string(forKey: "mode") == "working" ? .working : .idle
    @State private var yaw = UserDefaults.standard.bool(forKey: "yaw")
    @State private var brightnessFix = UserDefaults.standard.object(forKey: "fix") == nil || UserDefaults.standard.bool(forKey: "fix")
    private let showHUD = UserDefaults.standard.object(forKey: "hud") == nil || UserDefaults.standard.bool(forKey: "hud")

    @StateObject private var fps = FPSMeter()
    private let recipe = GiraffeRecipe.load()
    private let snow = UIColor(hex: "#faf5f8")

    var body: some View {
        ZStack {
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

            VStack {
                if showHUD {
                    HStack {
                        Text("\(fps.current) fps  ·  avg \(fps.average)  ·  1% low \(fps.onePercentLow)  ·  worst \(fps.maxFrameMs) ms")
                            .font(.system(.footnote, design: .monospaced))
                            .padding(8)
                            .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 8))
                        Button("Reset") { fps.reset() }.buttonStyle(.bordered)
                    }
                }
                Spacer()
                if showHUD {
                    VStack(spacing: 8) {
                        Picker("Material", selection: $style) {
                            ForEach(AvatarStyle.allCases) { Text($0.rawValue).tag($0) }
                        }
                        Picker("Pose", selection: $mode) {
                            ForEach(AvatarMode.allCases) { Text($0.rawValue).tag($0) }
                        }
                        Toggle("Turn 25°", isOn: $yaw)
                        Toggle("Flat-colour brightness fix", isOn: $brightnessFix)
                    }
                    .pickerStyle(.segmented)
                    .padding(12)
                    .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 12))
                    .padding(.horizontal, 16)
                }
            }
            .padding(.top, 8)
            .padding(.bottom, 12)
        }
        .onAppear { fps.start() }
    }
}
