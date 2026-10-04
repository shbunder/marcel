import SwiftUI
import UIKit

/// The pixel-art giraffe, played from a sprite sheet made by pixel-source/build.py.
/// The sheet is one row of 48x54 frames; giraffe-sheet.json says which frame to show for how long, per loop.
struct GiraffeSheet {
    struct Info: Decodable {
        var frameWidth: Int
        var frameHeight: Int
        var frames: Int
        var loops: [String: [[Int]]]          // loop name -> [[frame index, milliseconds]]
    }

    let info: Info
    let frames: [UIImage]

    static func load() -> GiraffeSheet {
        guard let jsonURL = Bundle.main.url(forResource: "giraffe-sheet", withExtension: "json"),
              let data = try? Data(contentsOf: jsonURL),
              let info = try? JSONDecoder().decode(Info.self, from: data),
              let pngURL = Bundle.main.url(forResource: "giraffe-sheet", withExtension: "png"),
              let sheet = UIImage(contentsOfFile: pngURL.path)?.cgImage
        else { fatalError("giraffe-sheet.png / .json are missing from the app bundle; run pixel-source/build.py and copy them in") }
        let frames = (0..<info.frames).compactMap { i in
            sheet.cropping(to: CGRect(x: i * info.frameWidth, y: 0, width: info.frameWidth, height: info.frameHeight))
                .map { UIImage(cgImage: $0) }
        }
        return GiraffeSheet(info: info, frames: frames)
    }

    /// Which frame to show `ms` milliseconds into a loop.
    func frame(loop: String, at ms: Int) -> UIImage {
        guard let steps = info.loops[loop], !steps.isEmpty else { return frames[0] }
        let total = steps.reduce(0) { $0 + $1[1] }
        var t = ms % total
        for step in steps {
            if t < step[1] { return frames[step[0]] }
            t -= step[1]
        }
        return frames[steps[0][0]]
    }
}

struct GiraffeSpriteView: View {
    var mode: AvatarMode
    private let sheet = GiraffeSheet.load()
    @State private var loopStart = Date()

    var body: some View {
        GeometryReader { geo in
            // Whole-number zoom so every sprite pixel is a crisp square.
            let w = CGFloat(sheet.info.frameWidth), h = CGFloat(sheet.info.frameHeight)
            let zoom = max(1, floor(min(geo.size.width / w, geo.size.height / h)))
            TimelineView(.animation(minimumInterval: 1.0 / 30)) { timeline in
                let ms = Int(timeline.date.timeIntervalSince(loopStart) * 1000)
                Image(uiImage: sheet.frame(loop: mode == .working ? "working" : "idle", at: ms))
                    .interpolation(.none)
                    .resizable()
                    .frame(width: w * zoom, height: h * zoom)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
        .onChange(of: mode) { _, _ in loopStart = Date() }
    }
}
