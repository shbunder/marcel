import AvatarKit
import Foundation
import PixelKit
import SwiftUI
import XCTest

@testable import MarcelSettings

/// Settings and the status screen, drawn as the live screen draws them but without the scroll view (which
/// `ImageRenderer` leaves blank), compared pixel for pixel with `__Snapshots__/*.png` in light and dark.
@MainActor
final class SettingsSnapshotTests: XCTestCase {
    static let both: [Snapshot.Variant] = [.light, .dark]

    /// What the app hands Settings: the helper's avatar, the look picker's hub, Face ID on, and the notebook.
    static let links = SettingsAppLinks(
        avatar: AnyView(AvatarFrameView(frame: SpriteSheet.giraffe.loop(for: .idle).firstFrame, box: 56)),
        looks: NoLooks(), faceID: .constant(true), openNotebook: {})

    struct SettingsStill: View {
        var model: SettingsModel
        var links = SettingsSnapshotTests.links

        var body: some View {
            VStack(spacing: 0) {
                SettingsHeader(title: "Settings", back: {})
                SettingsContent(model: model, text: Fx.text, links: links, actions: SettingsActions())
                    .padding(PixelMetrics.padding)
            }
            .environment(\.settingsStill, true)
        }
    }

    struct StatusStill: View {
        var model: StatusModel

        var body: some View {
            VStack(spacing: 0) {
                SettingsHeader(title: "System status", back: {})
                StatusContent(model: model, text: Fx.text).padding(PixelMetrics.padding)
            }
        }
    }

    private func model(_ hub: FakeHub) async -> SettingsModel {
        let defaults = UserDefaults.scratch("snapshot")
        defaults.set("marcel-k3v9q2m7x4p8r1t6w5y0z2ab", forKey: NtfyTopicStore.key)
        let model = SettingsModel(service: hub.service, tokens: FakeTokens(), topics: NtfyTopicStore(defaults: defaults))
        await model.load()
        return model
    }

    /// Everything there: notifications through ntfy, the usage guard holding work back, a digest and a paused
    /// schedule, this phone and the kitchen iPad.
    func testSettings() async {
        Snapshot.assert(SettingsStill(model: await model(FakeHub())), named: "Settings", variants: Self.both,
                        padding: 0)
    }

    /// "Edit avatar and name": the name box over the look picker (wireframe Q6 moved the name here from Settings).
    func testTheNameOnTheAvatarAndNameScreen() async {
        let model = await model(FakeHub())
        Snapshot.assert(SettingsCard("Name") { NameBox(model: model, save: {}) }
                            .padding(PixelMetrics.padding)
                            .environment(\.settingsStill, true),
                        named: "Settings-name", variants: Self.both, padding: 0)
    }

    /// A hub without schedules (S-09.1) or usage (S-10.1) yet, and notifications still off.
    func testSettingsOnAHubThatIsNotFinished() async {
        let hub = FakeHub()
        hub["GET /schedules"] = nil
        hub["GET /usage"] = (501, "")
        hub["GET /devices"] = (200, Fx.json(Fx.devices(phonePush: "none")))
        Snapshot.assert(SettingsStill(model: await model(hub)), named: "Settings-not-yet", variants: Self.both,
                        padding: 0)
    }

    /// S-12.12: deleting a schedule the hub keeps as a built-in digest (409 `schedule_builtin`): why, and "Pause it
    /// instead?".
    func testDeletingABuiltInDigestOffersPause() async {
        let hub = FakeHub()
        hub["GET /schedules"] = (200, Fx.json([Fx.schedules[0], Fx.schedules[1].merging(["paused": false]) { $1 }]))
        hub["DELETE /schedules/sch_deps"] = (409, Fx.error("schedule_builtin", "Built-in."))
        let model = await model(hub)
        await model.delete(model.schedules.value![1])
        XCTAssertNotNil(model.pauseInstead)
        Snapshot.assert(SettingsStill(model: model), named: "Settings-builtin-pause", variants: Self.both, padding: 0)
    }

    /// S-12.11: the runner has not read plan usage yet (503 `no_reading`): "usage unavailable".
    func testSettingsWithNoUsageReadingYet() async {
        let hub = FakeHub()
        hub["GET /usage"] = (503, Fx.error("no_reading", "No usage reading yet."))
        Snapshot.assert(SettingsStill(model: await model(hub)), named: "Settings-usage-unavailable",
                        variants: Self.both, padding: 0)
    }

    /// `marcel://status` from a `broke` push: the runner unreachable, and that push as the last alert.
    func testStatus() async {
        let alert = PushAlert(title: "Something broke", body: "The NUC runner stopped answering.", category: "broke",
                              at: Fx.now.addingTimeInterval(-300))
        let model = StatusModel(service: FakeHub().service, alert: alert, alerts: LastAlertStore(defaults: .scratch()))
        await model.load()
        Snapshot.assert(StatusStill(model: model), named: "Status", variants: Self.both, padding: 0)
    }
}
