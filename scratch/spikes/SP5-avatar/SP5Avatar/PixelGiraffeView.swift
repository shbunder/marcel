import SwiftUI

/// Draws PixelGiraffe at a whole-number zoom so every sprite pixel is a crisp square.
struct PixelGiraffeView: View {
    var mode: AvatarMode
    @State private var workingSince: Date? = nil
    @State private var idleSince = Date()

    var body: some View {
        TimelineView(.animation) { timeline in
            let now = timeline.date
            let t = now.timeIntervalSinceReferenceDate
            // Ease in and out of working so the glow does not snap.
            let working: Double = {
                if mode == .working { return min(1, now.timeIntervalSince(workingSince ?? now) / 0.4) }
                return max(0, 1 - now.timeIntervalSince(idleSince) / 0.4)
            }()
            Canvas(rendersAsynchronously: false) { context, size in
                let grid = PixelGiraffe.frame(at: t, working: working)
                let w = PixelGiraffe.width, h = PixelGiraffe.height
                let zoom = max(1, Int(min(size.width / CGFloat(w), size.height / CGFloat(h))))
                let z = CGFloat(zoom)
                let originX = ((size.width - CGFloat(w) * z) / 2).rounded()
                let originY = ((size.height - CGFloat(h) * z) / 2).rounded()
                for y in 0..<h {
                    var x = 0
                    while x < w {
                        let s = grid[y * w + x]
                        if s == .none { x += 1; continue }
                        var run = 1                       // merge a horizontal run of the same colour into one rectangle
                        while x + run < w && grid[y * w + x + run] == s { run += 1 }
                        let rect = CGRect(x: originX + CGFloat(x) * z, y: originY + CGFloat(y) * z, width: CGFloat(run) * z, height: z)
                        context.fill(Path(rect), with: .color(PixelGiraffe.palette[Int(s.rawValue)]))
                        x += run
                    }
                }
            }
        }
        .onChange(of: mode) { _, new in
            if new == .working { workingSince = Date() } else { idleSince = Date() }
        }
        .onAppear { if mode == .working { workingSince = Date(timeIntervalSinceNow: -1) } }
    }
}
