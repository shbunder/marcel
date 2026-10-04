import SwiftUI
import UIKit

/// The pixel-art giraffe: a recipe of rounded pixel rectangles painted onto a small grid every frame,
/// so every part can move by whole pixels (which is what makes it read as pixel art, not a blurry sprite).
/// Facing left like the logo. Other animals later = another recipe, same painter.
struct PixelGiraffe {
    static let width = 36
    static let height = 78
    private static let ox = 4   // margin so head glances and the tail swish stay on the grid
    private static let oy = 2

    enum Shade: UInt8 {
        case none, rose, roseHi, roseLo, maroon, ink, white, orange, glowA, glowB, shadow
    }

    enum Group { case legs, torso, head }

    struct Part {
        var group: Group
        var rect: (x0: Int, y0: Int, x1: Int, y1: Int)   // inclusive
        var radii: (tl: Double, tr: Double, br: Double, bl: Double)
        var shade: Shade
        var tag = ""
        var lit = false                                   // adds a light top edge and a darker bottom edge
        var mask: [(x: Int, y: Int, edge: Int)] = []      // edge: 1 = top row, 2 = bottom row, 0 = inside
    }

    static let palette: [Color] = {
        func c(_ hex: String) -> Color { Color(UIColor(hex: hex)) }
        let maroon = UIColor(hex: "#8e3447"), orange = UIColor(hex: "#f6aa1c")
        return [.clear, c("#cc5e76"), c("#dc7a8e"), c("#b3506a"), c("#8e3447"), c("#000000"), c("#ffffff"), c("#f6aa1c"),
                Color(maroon.mixed(with: orange, 0.4)), Color(maroon.mixed(with: orange, 0.75)), c("#eadde3")]
    }()

    static func mask(_ r: (x0: Int, y0: Int, x1: Int, y1: Int), _ k: (tl: Double, tr: Double, br: Double, bl: Double)) -> [(x: Int, y: Int, edge: Int)] {
        let w = r.x1 - r.x0 + 1, h = r.y1 - r.y0 + 1
        func inside(_ x: Int, _ y: Int) -> Bool {
            guard x >= 0, y >= 0, x < w, y < h else { return false }
            let px = Double(x) + 0.5, py = Double(y) + 0.5
            func corner(_ rad: Double, _ cx: Double, _ cy: Double, _ test: Bool) -> Bool {
                guard rad > 0, test else { return true }
                return hypot(px - cx, py - cy) <= rad
            }
            return corner(k.tl, k.tl, k.tl, px < k.tl && py < k.tl)
                && corner(k.tr, Double(w) - k.tr, k.tr, px > Double(w) - k.tr && py < k.tr)
                && corner(k.br, Double(w) - k.br, Double(h) - k.br, px > Double(w) - k.br && py > Double(h) - k.br)
                && corner(k.bl, k.bl, Double(h) - k.bl, px < k.bl && py > Double(h) - k.bl)
        }
        var out: [(x: Int, y: Int, edge: Int)] = []
        for y in 0..<h { for x in 0..<w where inside(x, y) {
            out.append((r.x0 + x, r.y0 + y, !inside(x, y - 1) ? 1 : (!inside(x, y + 1) ? 2 : 0)))
        } }
        return out
    }

    static func part(_ g: Group, _ x0: Int, _ y0: Int, _ x1: Int, _ y1: Int, _ r: Double = 0, tl: Double? = nil, tr: Double? = nil,
                     br: Double? = nil, bl: Double? = nil, _ s: Shade, lit: Bool = false, tag: String = "") -> Part {
        let rect = (x0: x0, y0: y0, x1: x1, y1: y1)
        let radii = (tl: tl ?? r, tr: tr ?? r, br: br ?? r, bl: bl ?? r)
        var p = Part(group: g, rect: rect, radii: radii, shade: s, tag: tag, lit: lit)
        p.mask = mask(rect, radii)
        return p
    }

    // Drawn back to front. Coordinates are in sprite pixels (before the margin offset).
    // Proportions follow docs/design/logo.png: head as wide as the whole animal, long neck on the right,
    // rounded shoulder on the left, four legs (far pair dark).
    static let parts: [Part] = [
        part(.legs, 6, 62, 10, 71, 1, .maroon),                       // 0  far legs
        part(.legs, 21, 62, 25, 71, 1, .maroon),                      // 1
        part(.legs, 0, 62, 4, 71, 1, .rose, lit: true),               // 2  near legs
        part(.legs, 13, 62, 17, 71, 1, .rose, lit: true),             // 3
        part(.torso, 0, 44, 25, 61, 3, tl: 10, .rose, lit: true),     // 4  body with the big rounded shoulder
        part(.torso, 19, 52, 25, 61, 3, tl: 4, .maroon),              // 5  far haunch
        part(.torso, 17, 20, 25, 46, 2, .rose),                       // 6  neck
        part(.torso, 21, 25, 24, 28, 1.4, .maroon),                   // 7  spot 1
        part(.torso, 18, 33, 21, 36, 1.4, .maroon),                   // 8  spot 2
        part(.torso, 19, 42, 22, 45, 1.4, .maroon),                   // 9  spot 3
        part(.torso, 9, 46, 12, 49, 1.4, .maroon),                    // 10 spot 4
        part(.torso, 13, 51, 16, 54, 1.4, .maroon),                   // 11 spot 5
        part(.torso, 4, 52, 7, 55, 1.4, .maroon),                     // 12 spot 6
        part(.head, 15, 0, 16, 10, 0.6, .maroon),                     // 13 horns
        part(.head, 20, 0, 21, 10, 0.6, .maroon),                     // 14
        part(.head, 20, 0, 23, 2, 0.9, .maroon),                      // 15
        part(.head, 0, 8, 25, 21, 3, .rose, lit: true, tag: "head"),  // 16 head
        part(.head, 1, 16, 2, 19, 0.7, .ink),                         // 17 nostril
    ]

    // Parts that animate individually, by index into `parts`.
    static let spotIndexes = [7, 8, 9, 10, 11, 12]            // spot 1...6 in logo order
    static let glowingSpots = [0, 2, 4]                       // working(3): spots 1, 3 and 5
    static let hornIndexes = [13, 14, 15]

    static let eyeBox = (x0: 13, x1: 16, top: 11, bottom: 14)
    static let earBox = (x: 10, y: 5)

    /// One frame as a grid of shades.
    static func frame(at t: Double, working: Double) -> [Shade] {
        var g = [Shade](repeating: .none, count: width * height)
        func put(_ x: Int, _ y: Int, _ s: Shade) {
            let gx = x + ox, gy = y + oy
            if gx >= 0, gy >= 0, gx < width, gy < height { g[gy * width + gx] = s }
        }
        func every(_ time: Double, _ period: Double, offset: Double = 0, length: Double) -> Double? {   // 0...1 progress inside an event, or nil
            let p = (time + offset).truncatingRemainder(dividingBy: period)
            return p < length ? p / length : nil
        }
        func glanceAt(_ time: Double) -> Int { every(time, 9.1, offset: 3.0, length: 1.3) != nil ? -1 : 0 }   // looks ahead for a moment

        // --- motion for this moment ---
        let breath = sin(2 * .pi * t / 3.4) > 0.25 ? 1 : 0                          // chest rises one pixel
        let nod = every(t, 7.3, offset: 1.2, length: 0.5) != nil ? 1 : 0           // little nod
        let focus = working > 0.5 ? 1 : 0                                           // head dips while working
        let headDx = glanceAt(t)
        let headDy = -breath + nod + focus
        let torsoDy = -breath

        // shadow on the ground
        for x in 0...26 { put(x, 72, .shadow) }
        for x in 3...23 { put(x, 73, .shadow) }

        // --- tail (rose with maroon tuft), swishing ---
        let swing = sin(2 * .pi * t / 2.6)
        for k in 0..<10 {
            let x = 26 + Int((swing * Double(k) * 0.6).rounded())
            put(x, 46 + k + torsoDy, .rose); put(x + 1, 46 + k + torsoDy, .rose); put(x + 2, 46 + k + torsoDy, .roseLo)
        }
        let tx = 26 + Int((swing * 10 * 0.6).rounded())
        for y in 56...60 { for x in tx...(tx + 3) { put(x, y + torsoDy, .maroon) } }

        // --- ears (new: the logo has none, but a flicking ear is a big part of "alive"); drawn behind the head ---
        let flick = every(t, 5.2, offset: 0.4, length: 0.35).map { $0 < 0.5 ? 1 : 0 } ?? 0
        let ex = earBox.x + headDx, ey = earBox.y + headDy - flick
        for (dx, dy) in [(0, 2), (0, 3), (1, 1), (1, 2), (1, 3), (2, 0), (2, 1), (2, 2), (2, 3), (3, 1), (3, 2), (3, 3)] { put(ex + dx, ey + dy, .rose) }
        put(ex + 2, ey + 2, .maroon); put(ex + 2, ey + 3, .maroon)

        // --- parts ---
        for (index, p) in parts.enumerated() {
            var dx = 0, dy = 0
            switch p.group {
            case .legs: break
            case .torso: dy = torsoDy
            case .head:
                dx = hornIndexes.contains(index) ? glanceAt(t - 0.15) : headDx     // horns trail the head a moment
                dy = headDy
            }
            var shade = p.shade
            if let n = spotIndexes.firstIndex(of: index), glowingSpots.contains(n), working > 0 {
                // chase: each glowing spot peaks in turn
                let k = glowingSpots.firstIndex(of: n)!
                let phase = (t / 0.45 + Double(k) * 0.7).truncatingRemainder(dividingBy: 2.1)
                let level = max(0, 1 - abs(phase - 0.5) / 0.9) * working
                shade = level > 0.66 ? .orange : (level > 0.33 ? .glowB : (level > 0.08 ? .glowA : .maroon))
            }
            for m in p.mask {
                var s = shade
                if p.lit && shade == .rose { s = m.edge == 1 ? .roseHi : (m.edge == 2 ? .roseLo : .rose) }
                put(m.x + dx, m.y + dy, s)
            }
        }

        // --- eye ---
        var rows = [11, 12, 13, 14]
        if let b = every(t, 3.6, offset: 0.9, length: 0.16) ?? every(t, 11.0, offset: 0.4, length: 0.16) {
            rows = (b > 0.35 && b < 0.65) ? [13] : [12, 13]
        }
        if focus == 1 && rows.count == 4 { rows = [12, 13, 14] }                    // squint a little while working
        let eyeDx = headDx + (headDx == -1 ? -1 : 0)
        for y in rows { for x in eyeBox.x0...eyeBox.x1 {
            let corner = (y == eyeBox.top || y == eyeBox.bottom) && (x == eyeBox.x0 || x == eyeBox.x1) && rows.count == 4
            if !corner { put(x + eyeDx, y + headDy, .ink) }
        } }
        if rows.count >= 3 { put(eyeBox.x0 + 1 + eyeDx, eyeBox.bottom - 1 + headDy, .white) }   // glint, lower left like the logo

        // --- sparkles while working ---
        if working > 0.5 {
            for (i, n) in glowingSpots.enumerated() {
                let r = parts[spotIndexes[n]].rect
                let life = (t / 1.1 + Double(i) * 0.33).truncatingRemainder(dividingBy: 1.0)
                if life < 0.35 {
                    let cx = r.x1 + 3, cy = r.y0 - 1 + torsoDy
                    let color: Shade = life < 0.18 ? .white : .orange
                    let offsets = life < 0.18 ? [(0, 0)] : [(0, 0), (-1, 0), (1, 0), (0, -1), (0, 1)]
                    for (ox, oy) in offsets { put(cx + ox, cy + oy, color) }
                }
            }
        }
        return g
    }
}
