import MarcelKit
import UserNotifications

/// Opens a push that came through Marcel's push relay (S-22.16). The relay's visible alert is only "Marcel · You have
/// news"; the real notification is sealed for this phone (app-api.yaml `SealedPush`). This opens it with the phone's
/// private key from the Keychain group it shares with the app, and shows the real title, body and link. On any failure
/// the notification stays "You have news" and leads nowhere. A push not sent through the relay is left as it came.
/// The logic is `SealedNotification` in MarcelKit, tested there (`PushKeyTests`). Nothing here logs a push's words.
final class NotificationService: UNNotificationServiceExtension {
    override func didReceive(_ request: UNNotificationRequest,
                             withContentHandler contentHandler: @escaping (UNNotificationContent) -> Void) {
        guard let content = request.content.mutableCopy() as? UNMutableNotificationContent else {
            contentHandler(request.content)
            return
        }
        SealedNotification.open(content, keys: KeychainPushKeyStore())
        contentHandler(content)
    }

    // Opening is synchronous and finishes well inside the 30 seconds iOS gives, so there is nothing left to hand over
    // when time runs out: the content handler has already been called.
    override func serviceExtensionTimeWillExpire() {}
}
