import UIKit

/// The giraffe as data: boxes measured off docs/design/logo.png, coloured by palette role.
/// Mirrors the file that will live at shared/avatar/recipes/giraffe.json.
struct GiraffeRecipe: Codable {
    struct Part: Codable {
        var name: String
        var group: String      // "torso" or "legs": the two things that can move as one
        var color: String      // palette role
        var rect: [Float]      // x0, y0, x1, y1 in logo pixels, y down
        var depth: Float       // logo pixels
        var z: Float           // box centre, logo pixels (+ is towards the camera)
        var radius: Float      // corner radius, logo pixels
        var rotZ: Float?       // degrees, counter-clockwise
        var anim: String?      // "blink" or "spot"
    }

    struct Glow: Codable {
        var color: String
        var parts: [String]    // which spots light up in working(3)
    }

    var unit: Float            // metres per logo pixel
    var originX: Float
    var hipY: Float
    var palette: [String: String]
    var glow: Glow
    var parts: [Part]

    static func load() -> GiraffeRecipe {
        guard let url = Bundle.main.url(forResource: "giraffe", withExtension: "json"),
              let data = try? Data(contentsOf: url),
              let recipe = try? JSONDecoder().decode(GiraffeRecipe.self, from: data)
        else { fatalError("giraffe.json is missing from the app bundle or does not parse") }
        return recipe
    }

    func color(_ role: String) -> UIColor {
        guard let hex = palette[role] else { fatalError("giraffe.json has no palette role '\(role)'") }
        return UIColor(hex: hex)
    }
}

extension UIColor {
    convenience init(hex: String) {
        let v = UInt32(hex.dropFirst(), radix: 16) ?? 0
        self.init(red: CGFloat((v >> 16) & 0xff) / 255,
                  green: CGFloat((v >> 8) & 0xff) / 255,
                  blue: CGFloat(v & 0xff) / 255,
                  alpha: 1)
    }

    func mixed(with other: UIColor, _ t: CGFloat) -> UIColor {
        var a: (CGFloat, CGFloat, CGFloat, CGFloat) = (0, 0, 0, 0)
        var b: (CGFloat, CGFloat, CGFloat, CGFloat) = (0, 0, 0, 0)
        getRed(&a.0, green: &a.1, blue: &a.2, alpha: &a.3)
        other.getRed(&b.0, green: &b.1, blue: &b.2, alpha: &b.3)
        return UIColor(red: a.0 + (b.0 - a.0) * t,
                       green: a.1 + (b.1 - a.1) * t,
                       blue: a.2 + (b.2 - a.2) * t,
                       alpha: 1)
    }
}
