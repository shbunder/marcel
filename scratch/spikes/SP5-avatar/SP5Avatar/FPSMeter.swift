import Combine
import os
import QuartzCore

/// Counts frames with a display link. Shows the last half-second, the average since reset,
/// and the "1% low" (the speed of the slowest 1% of frames).
@MainActor
final class FPSMeter: ObservableObject {
    @Published var current = 0
    @Published var average = 0
    @Published var onePercentLow = 0
    @Published var maxFrameMs = 0

    private var link: CADisplayLink?
    private var lastTimestamp: CFTimeInterval = 0
    private var windowStart: CFTimeInterval = 0
    private var windowFrames = 0
    private var intervals: [Double] = []
    private var lastLog: CFTimeInterval = 0
    private let log = Logger(subsystem: "com.shbunder.marcel.sp5avatar", category: "fps")

    func start() {
        guard link == nil else { return }
        let l = CADisplayLink(target: self, selector: #selector(tick(_:)))
        l.add(to: .main, forMode: .common)
        link = l
    }

    func reset() {
        intervals.removeAll()
        lastTimestamp = 0
        windowStart = 0
        windowFrames = 0
    }

    @objc private func tick(_ l: CADisplayLink) {
        let now = l.timestamp
        if lastTimestamp > 0 { intervals.append(now - lastTimestamp) }
        lastTimestamp = now
        if windowStart == 0 { windowStart = now }
        windowFrames += 1

        if now - windowStart >= 0.5 {
            current = Int((Double(windowFrames) / (now - windowStart)).rounded())
            windowStart = now
            windowFrames = 0

            if !intervals.isEmpty {
                average = Int((Double(intervals.count) / intervals.reduce(0, +)).rounded())
                let sorted = intervals.sorted()
                let p99 = sorted[min(sorted.count - 1, Int(Double(sorted.count) * 0.99))]
                onePercentLow = Int((1 / p99).rounded())
                maxFrameMs = Int((sorted.last! * 1000).rounded())
            }
            if now - lastLog >= 2 {
                lastLog = now
                log.notice("SP5 fps now=\(self.current) avg=\(self.average) low1pct=\(self.onePercentLow) worstFrameMs=\(self.maxFrameMs) frames=\(self.intervals.count)")
            }
        }
    }
}
