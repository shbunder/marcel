import SwiftUI

enum AvatarLook: String, CaseIterable, Identifiable {
    case plush = "Plush"
    case pixel = "Pixel"
    case box = "Boxes v1"
    var id: String { rawValue }
}

struct ContentView: View {
    // Launch arguments (-look pixel -style pbr -mode working -yaw 1 -hud 0 -fix 0) set the starting state, for repeatable screenshots.
    @State private var look: AvatarLook = {
        switch UserDefaults.standard.string(forKey: "look") {
        case "pixel": return .pixel
        case "box": return .box
        default: return .plush
        }
    }()
    @State private var style: AvatarStyle = UserDefaults.standard.string(forKey: "style") == "pbr" ? .pbr : .unlit
    @State private var mode: AvatarMode = UserDefaults.standard.string(forKey: "mode") == "working" ? .working : .idle
    @State private var yaw = UserDefaults.standard.bool(forKey: "yaw")
    @State private var brightnessFix = UserDefaults.standard.object(forKey: "fix") == nil || UserDefaults.standard.bool(forKey: "fix")
    private let showHUD = UserDefaults.standard.object(forKey: "hud") == nil || UserDefaults.standard.bool(forKey: "hud")

    @StateObject private var fps = FPSMeter()
    private let snow = Color(UIColor(hex: "#faf5f8"))

    var body: some View {
        ZStack {
            snow.ignoresSafeArea()
            switch look {
            case .box: BoxLookView(style: style, mode: mode, yaw: yaw, brightnessFix: brightnessFix)
            case .pixel: PixelGiraffeView(mode: mode).padding(.vertical, 90).ignoresSafeArea(edges: .horizontal)
            case .plush: PlushGiraffeView(mode: mode, yaw: yaw)
            }

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
                        Picker("Look", selection: $look) {
                            ForEach(AvatarLook.allCases) { Text($0.rawValue).tag($0) }
                        }
                        Picker("Pose", selection: $mode) {
                            ForEach(AvatarMode.allCases) { Text($0.rawValue).tag($0) }
                        }
                        if look != .pixel { Toggle("Turn 25°", isOn: $yaw) }
                        if look == .box {
                            Picker("Material", selection: $style) {
                                ForEach(AvatarStyle.allCases) { Text($0.rawValue).tag($0) }
                            }
                            Toggle("Flat-colour brightness fix", isOn: $brightnessFix)
                        }
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
