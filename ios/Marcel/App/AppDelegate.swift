import MarcelKit
import MarcelSettings
import UIKit
import UserNotifications

/// Set up before any screen: the notification centre's delegate and the push categories, so a push tapped while the
/// app was not running is delivered to `PushHandler` (and routed by `LinkRouter`) instead of being lost. Also passes
/// the APNs device token on to Settings.
final class AppDelegate: NSObject, UIApplicationDelegate {
    /// One router for the app's life: links that arrive before the tabs are up wait in it.
    let router = LinkRouter()
    /// Approve and Deny answer with the hub and token pairing saved, read when the action comes in: iOS may start the
    /// app in the background just for the action, and then no screen ever loads.
    lazy var push = PushHandler(router: router, answer: PushHandler.answering(
        hubStore: UserDefaultsPairedHubStore(), tokenStore: KeychainTokenStore()))

    func application(_ application: UIApplication,
                     didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil) -> Bool {
        let center = UNUserNotificationCenter.current()
        center.delegate = push
        center.setNotificationCategories(PushCategories.make())
        return true
    }

    func application(_ application: UIApplication, didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data) {
        APNsRegistrar.shared.didRegister(deviceToken: deviceToken)
    }

    func application(_ application: UIApplication, didFailToRegisterForRemoteNotificationsWithError error: any Error) {
        APNsRegistrar.shared.didFail(error)
    }
}
