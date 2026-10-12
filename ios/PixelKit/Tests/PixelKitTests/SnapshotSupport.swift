import ImageIO
import SwiftUI
import UniformTypeIdentifiers
import XCTest
@testable import PixelKit

/// Snapshot testing without a dependency: render a view with `ImageRenderer`, compare it pixel by pixel with the
/// PNG in `__Snapshots__/`, and fail on any visible change. To accept a deliberate change, re-record:
/// `make -C ios pixelkit-record` (sets PIXELKIT_RECORD=1), look at the new PNGs, and commit them.
/// A missing reference is recorded and fails the test, so a new snapshot is never silently accepted.
enum Snapshot {
    /// The three looks every component is checked in.
    enum Variant: String, CaseIterable {
        case light, dark, largestText = "ax5"

        var scheme: ColorScheme { self == .dark ? .dark : .light }
        var dynamicType: DynamicTypeSize { self == .largestText ? .accessibility5 : .large }
    }

    static let width: CGFloat = 390
    static let scale: CGFloat = 2

    static var referenceFolder: URL {
        URL(fileURLWithPath: #filePath).deletingLastPathComponent().appendingPathComponent("__Snapshots__")
    }

    static var failureFolder: URL {
        // ios/.build/pixelkit-snapshot-failures (ignored by git)
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
            .deletingLastPathComponent().appendingPathComponent(".build/pixelkit-snapshot-failures")
    }

    static var isRecording: Bool { ProcessInfo.processInfo.environment["PIXELKIT_RECORD"] == "1" }

    /// Renders `view` the way the snapshot tests see it: on the theme's background, 390 pt wide, Reduce Motion
    /// pinned on so the blinking ▼ is always drawn.
    @MainActor
    static func render<V: View>(_ view: V, variant: Variant, width: CGFloat = width, padding: CGFloat = 16) -> CGImage? {
        let content = view
            .padding(padding)
            .frame(width: width)
            .background(PixelTheme(variant.scheme).background.color)
            .environment(\.colorScheme, variant.scheme)
            .environment(\.dynamicTypeSize, variant.dynamicType)
            .environment(\.pixelReduceMotionOverride, true)
            .environment(\.pixelRendersOffscreen, true)
        let renderer = ImageRenderer(content: content)
        renderer.scale = scale
        renderer.proposedSize = ProposedViewSize(width: width, height: nil)
        return renderer.cgImage
    }

    /// Checks `view` in every variant against its reference images.
    @MainActor
    static func assert<V: View>(
        _ view: V, named name: String, variants: [Variant] = Variant.allCases, width: CGFloat = width,
        padding: CGFloat = 16, file: StaticString = #filePath, line: UInt = #line
    ) {
        for variant in variants {
            let fileName = "\(name).\(variant.rawValue).png"
            guard let image = render(view, variant: variant, width: width, padding: padding) else {
                XCTFail("\(fileName): the view did not render", file: file, line: line)
                continue
            }
            compare(image, fileName: fileName, file: file, line: line)
        }
    }

    static func compare(_ image: CGImage, fileName: String, file: StaticString, line: UInt) {
        let reference = referenceFolder.appendingPathComponent(fileName)
        let exists = FileManager.default.fileExists(atPath: reference.path)
        if isRecording || !exists {
            do {
                try FileManager.default.createDirectory(at: referenceFolder, withIntermediateDirectories: true)
                try png(image).write(to: reference)
            } catch {
                XCTFail("\(fileName): could not record (\(error))", file: file, line: line)
                return
            }
            if !exists || isRecording {
                XCTFail("\(fileName): recorded a new reference image. Look at it, then run the tests again.",
                        file: file, line: line)
            }
            return
        }
        guard let data = try? Data(contentsOf: reference), let expected = decode(data) else {
            XCTFail("\(fileName): the reference image could not be read", file: file, line: line)
            return
        }
        let result = difference(expected, image)
        guard result.differs else { return }
        try? FileManager.default.createDirectory(at: failureFolder, withIntermediateDirectories: true)
        let actualURL = failureFolder.appendingPathComponent(fileName)
        try? png(image).write(to: actualURL)
        XCTFail("\(fileName): \(result.message). This run's image is at \(actualURL.path)", file: file, line: line)
    }

    struct Difference {
        var differs: Bool
        var message: String
    }

    /// Pixels count as changed when any channel moves by more than 8 of 255; more than 0.02 % changed fails.
    /// (Rendering is deterministic on one machine; the slack only absorbs anti-aliasing noise.)
    static func difference(_ expected: CGImage, _ actual: CGImage) -> Difference {
        guard expected.width == actual.width, expected.height == actual.height else {
            return Difference(differs: true, message: "the size changed from \(expected.width)×\(expected.height) "
                + "to \(actual.width)×\(actual.height) pixels")
        }
        let a = rgba(expected), b = rgba(actual)
        var changed = 0
        var index = 0
        while index < a.count {
            if abs(Int(a[index]) - Int(b[index])) > 8 || abs(Int(a[index + 1]) - Int(b[index + 1])) > 8
                || abs(Int(a[index + 2]) - Int(b[index + 2])) > 8 || abs(Int(a[index + 3]) - Int(b[index + 3])) > 8 {
                changed += 1
            }
            index += 4
        }
        let total = expected.width * expected.height
        let allowed = total / 5000
        return Difference(differs: changed > allowed,
                          message: "\(changed) of \(total) pixels changed (allowed: \(allowed))")
    }

    static func rgba(_ image: CGImage) -> [UInt8] {
        var bytes = [UInt8](repeating: 0, count: image.width * image.height * 4)
        let space = CGColorSpace(name: CGColorSpace.sRGB)!
        bytes.withUnsafeMutableBytes { buffer in
            let context = CGContext(data: buffer.baseAddress, width: image.width, height: image.height,
                                    bitsPerComponent: 8, bytesPerRow: image.width * 4, space: space,
                                    bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
            context.draw(image, in: CGRect(x: 0, y: 0, width: image.width, height: image.height))
        }
        return bytes
    }

    static func png(_ image: CGImage) throws -> Data {
        let data = NSMutableData()
        guard let destination = CGImageDestinationCreateWithData(data, UTType.png.identifier as CFString, 1, nil)
        else { throw CocoaError(.fileWriteUnknown) }
        CGImageDestinationAddImage(destination, image, nil)
        guard CGImageDestinationFinalize(destination) else { throw CocoaError(.fileWriteUnknown) }
        return data as Data
    }

    static func decode(_ data: Data) -> CGImage? {
        guard let source = CGImageSourceCreateWithData(data as CFData, nil) else { return nil }
        return CGImageSourceCreateImageAtIndex(source, 0, nil)
    }
}
