import Foundation
import MarcelKit
import OpenAPIRuntime
import UserNotifications

/// The words of a push, kept for the status screen ("Last alert").
public struct PushAlert: Codable, Equatable, Hashable, Sendable {
    public var title: String
    public var body: String
    public var category: String
    public var at: Date

    public init(title: String, body: String, category: String, at: Date) {
        self.title = title
        self.body = body
        self.category = category
        self.at = at
    }
}

/// A push as the app reads it (`PushNotification` in `contracts/app-api.yaml`): on APNs, `title` and `body` are in
/// `aps.alert`, the category is the notification's category, and `deep_link`, `approval_id` and `task_id` sit beside
/// `aps`. Everything in it is untrusted: the link is parsed by `DeepLink`, the ids are checked.
public struct PushPayload: Equatable, Sendable {
    public var title: String
    public var body: String
    public var category: String
    public var deepLink: String?
    public var approvalId: String?
    public var taskId: String?

    public init(title: String, body: String, category: String, deepLink: String?, approvalId: String? = nil,
                taskId: String? = nil) {
        self.title = title
        self.body = body
        self.category = category
        self.deepLink = deepLink
        self.approvalId = approvalId.flatMap { DeepLink.isId($0) ? $0 : nil }
        self.taskId = taskId.flatMap { DeepLink.isId($0) ? $0 : nil }
    }

    /// From a delivered notification: its title, body and category, and the payload's own keys.
    public init(title: String, body: String, category: String, userInfo: [AnyHashable: Any]) {
        self.init(title: title, body: body, category: category,
                  deepLink: userInfo["deep_link"] as? String,
                  approvalId: userInfo["approval_id"] as? String,
                  taskId: userInfo["task_id"] as? String)
    }

    public func alert(at date: Date = .now) -> PushAlert {
        PushAlert(title: String(title.prefix(80)), body: String(body.prefix(300)), category: category, at: date)
    }
}

/// The contract's push categories and their actions. Every category is registered so iOS groups and shows them;
/// only `approval` has actions (Approve, Deny), which the app answers with its own token (`POST /approvals/{id}`).
public enum PushCategories {
    /// `PushNotification.category` in the contract.
    public static let all = ["approval", "task", "broke", "digest", "note"]
    public static let approve = "approve"
    public static let deny = "deny"

    public static func make() -> Set<UNNotificationCategory> {
        Set(all.map { id in
            let actions: [UNNotificationAction] = id == "approval" ? [
                // Answering changes something on the NUC: unlock the phone first.
                UNNotificationAction(identifier: approve, title: "Approve", options: [.authenticationRequired]),
                UNNotificationAction(identifier: deny, title: "Deny",
                                     options: [.authenticationRequired, .destructive]),
            ] : []
            return UNNotificationCategory(identifier: id, actions: actions, intentIdentifiers: [], options: [])
        })
    }
}

/// The last `broke` push this phone saw, for the status screen. Kept in UserDefaults (not secret: it was on the lock
/// screen already).
public struct LastAlertStore: @unchecked Sendable {
    public static let key = "settings.lastAlert"
    private let defaults: UserDefaults

    public init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
    }

    public func load() -> PushAlert? {
        guard let data = defaults.data(forKey: Self.key) else { return nil }
        return try? JSONDecoder().decode(PushAlert.self, from: data)
    }

    public func save(_ alert: PushAlert) {
        defaults.set(try? JSONEncoder().encode(alert), forKey: Self.key)
    }
}

/// Receives pushes: a tap opens its link through the router; Approve and Deny answer without opening the app; a
/// push that arrives while the app is open still shows as a banner. Set as the notification centre's delegate at
/// launch, before any push can be delivered, so a push that cold-launches the app is not lost.
@MainActor
public final class PushHandler: NSObject, UNUserNotificationCenterDelegate {
    public typealias Answer = (_ approvalId: String, _ approve: Bool) async throws -> Void

    public let router: LinkRouter
    /// Answers an approval with this phone's token. Made at launch (see `answering`), so Approve and Deny work when
    /// iOS starts the app in the background for the action and no screen ever loads.
    let answer: Answer
    /// Tells the person something went wrong when the app is not open (a local notification).
    let report: (String) async -> Void
    let alerts: LastAlertStore

    public init(router: LinkRouter, answer: @escaping Answer, alerts: LastAlertStore = LastAlertStore(),
                report: ((String) async -> Void)? = nil) {
        self.router = router
        self.answer = answer
        self.alerts = alerts
        self.report = report ?? Self.localNotification
    }

    /// Answers with the paired hub as the stores hold it at that moment: `POST /approvals/{id}` with the device token.
    /// Throws `SettingsError.notPaired` when the phone has no hub or no token.
    public static func answering(hubStore: any PairedHubStore, tokenStore: any TokenStore,
                                 transport: (any ClientTransport)? = nil) -> Answer {
        { approvalId, approve in
            let paired = if let transport {
                LiveSettingsService.paired(hubStore: hubStore, tokenStore: tokenStore, transport: transport)
            } else {
                LiveSettingsService.paired(hubStore: hubStore, tokenStore: tokenStore)
            }
            guard let (service, _) = paired else { throw SettingsError.notPaired }
            try await service.answer(approvalId: approvalId, approve: approve)
        }
    }

    /// What a response does. `action` is the action's identifier (`UNNotificationDefaultActionIdentifier` for a tap).
    public func respond(action: String, payload: PushPayload) async {
        remember(payload)
        switch action {
        case PushCategories.approve, PushCategories.deny:
            guard let approvalId = payload.approvalId else {
                await report("That question came without its id, so nothing was answered. Open Marcel to answer it.")
                return
            }
            do {
                try await answer(approvalId, action == PushCategories.approve)
            } catch {
                await report(Self.words(for: error))
            }
        case UNNotificationDismissActionIdentifier:
            break
        default:
            await router.handle(push: payload)
        }
    }

    /// A push arriving while the app is open: keep it for the status screen if it is about a fault.
    public func arrived(_ payload: PushPayload) {
        remember(payload)
    }

    private func remember(_ payload: PushPayload) {
        if payload.category == "broke" { alerts.save(payload.alert()) }
    }

    static func words(for error: any Error) -> String {
        switch error as? SettingsError {
        case .notPaired: "This phone isn't paired, so nothing was answered."
        case .unauthorized: "The hub no longer accepts this phone, so nothing was answered. Pair it again."
        case .offline: "Couldn't reach the hub, so nothing was answered. Open Marcel and answer it there."
        case .hubProblem: "The hub had a problem, so nothing was answered. Open Marcel and answer it there."
        case .some(let known): known.errorDescription ?? "Your answer didn't reach the hub."
        case nil: "Your answer didn't reach the hub. Open Marcel and answer it there."
        }
    }

    private static func localNotification(_ text: String) async {
        let content = UNMutableNotificationContent()
        content.title = "Marcel"
        content.body = text
        let request = UNNotificationRequest(identifier: UUID().uuidString, content: content, trigger: nil)
        try? await UNUserNotificationCenter.current().add(request)
    }

    // MARK: UNUserNotificationCenterDelegate

    public nonisolated func userNotificationCenter(_ center: UNUserNotificationCenter,
                                                   didReceive response: UNNotificationResponse) async {
        let content = response.notification.request.content
        let payload = PushPayload(title: content.title, body: content.body, category: content.categoryIdentifier,
                                  userInfo: content.userInfo)
        let action = response.actionIdentifier
        await respond(action: action, payload: payload)
    }

    public nonisolated func userNotificationCenter(_ center: UNUserNotificationCenter,
                                                   willPresent notification: UNNotification) async
        -> UNNotificationPresentationOptions
    {
        let content = notification.request.content
        let payload = PushPayload(title: content.title, body: content.body, category: content.categoryIdentifier,
                                  userInfo: content.userInfo)
        await arrived(payload)
        return [.banner, .list, .sound]
    }
}
