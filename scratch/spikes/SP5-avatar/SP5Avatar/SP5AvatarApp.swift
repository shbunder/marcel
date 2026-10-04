import RealityKit
import SwiftUI

@main
struct SP5AvatarApp: App {
    init() {
        AvatarPartComponent.registerComponent()
        BreathComponent.registerComponent()
        AvatarSystem.registerSystem()
    }

    var body: some SwiftUI.Scene {
        WindowGroup { ContentView() }
    }
}
