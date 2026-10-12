import SwiftUI

enum AvatarMode: String, CaseIterable, Identifiable {
    case idle = "Idle"
    case thinking = "Thinking"
    case working = "Working"
    case happy = "Happy"
    var id: String { rawValue }
    var loop: String { rawValue.lowercased() }
}

enum AvatarStyle: String, CaseIterable, Identifiable {
    case clawd = "Clawd style"
    case detailed = "Detailed"
    var id: String { rawValue }
}

struct ContentView: View {
    // Launch arguments (-style detailed -mode thinking -hud 0) set the starting state, for repeatable screenshots.
    @State private var mode: AvatarMode = AvatarMode.allCases.first { $0.loop == UserDefaults.standard.string(forKey: "mode") } ?? .idle
    @State private var style: AvatarStyle = UserDefaults.standard.string(forKey: "style") == "detailed" ? .detailed : .clawd
    private let showHUD = UserDefaults.standard.object(forKey: "hud") == nil || UserDefaults.standard.bool(forKey: "hud")
    @StateObject private var fps = FPSMeter()

    var body: some View {
        ZStack {
            Color(red: 0xfa / 255, green: 0xf5 / 255, blue: 0xf8 / 255).ignoresSafeArea()
            GiraffeSpriteView(mode: mode, style: style)
                .padding(.horizontal, 4)
                .padding(.vertical, 120)

            VStack {
                if showHUD {
                    HStack {
                        Text("\(fps.current) fps  ·  avg \(fps.average)  ·  1% low \(fps.onePercentLow)")
                            .font(.system(.footnote, design: .monospaced))
                            .padding(8)
                            .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 8))
                        Button("Reset") { fps.reset() }.buttonStyle(.bordered)
                    }
                }
                Spacer()
                if showHUD {
                    VStack(spacing: 8) {
                        Picker("Style", selection: $style) {
                            ForEach(AvatarStyle.allCases) { Text($0.rawValue).tag($0) }
                        }
                        Picker("Mode", selection: $mode) {
                            ForEach(AvatarMode.allCases) { Text($0.rawValue).tag($0) }
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
