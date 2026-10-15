import Foundation
import MarcelKit
import MarcelLook
import Observation

/// Which screen the app shows (S-17.5): the welcome screen until the phone is paired, "Meet your helper" until the
/// helper is onboarded (`agent.onboarded_at`), then the chat. Signing out goes back to the welcome screen.
@MainActor
@Observable
public final class AppFlow {
    public enum Screen: Equatable, Sendable {
        case welcome
        /// Paired; asking the hub whether the helper is onboarded.
        case checking
        case onboarding
        case main
    }

    public private(set) var screen: Screen = .welcome
    /// "Meet your helper", while `screen` is `.onboarding`.
    public private(set) var onboarding: OnboardingModel?

    @ObservationIgnored public let connection: HubConnection
    @ObservationIgnored private let defaults: UserDefaults
    @ObservationIgnored private let makeService: @MainActor (PairedHub) -> any WelcomeService
    @ObservationIgnored private let makeLookService: @MainActor (PairedHub) -> any LookService

    static let onboardedKey = "welcome.onboardedHub"

    /// - Parameters:
    ///   - makeService: the hub's answers for a paired hub (`LiveWelcomeService` in the app).
    ///   - makeLookService: the look picker's previews for that hub.
    public init(connection: HubConnection, defaults: UserDefaults = .standard,
                makeService: @escaping @MainActor (PairedHub) -> any WelcomeService,
                makeLookService: @escaping @MainActor (PairedHub) -> any LookService) {
        self.connection = connection
        self.defaults = defaults
        self.makeService = makeService
        self.makeLookService = makeLookService
        screen = Self.initial(connection.phase, onboarded: defaults.string(forKey: Self.onboardedKey))
    }

    static func initial(_ phase: HubConnection.Phase, onboarded: String?) -> Screen {
        switch phase {
        case .unpaired: .welcome
        case .paired(let hub): onboarded == hub.serverURL.absoluteString ? .main : .checking
        }
    }

    /// Follows the connection: call when the app starts and whenever `connection.phase` changes.
    public func update() async {
        switch connection.phase {
        case .unpaired:
            onboarding = nil
            defaults.removeObject(forKey: Self.onboardedKey)
            screen = .welcome
        case .paired(let hub):
            guard screen != .main, screen != .onboarding else { return }
            screen = .checking
            let service = makeService(hub)
            do {
                let me = try await service.me()
                // The buddy this phone starts on (a pairing link's `&buddy=`, B-49), else the first.
                guard let agent = CurrentBuddy.shared.pick(from: me.agents), agent.onboardedAt == nil else {
                    return enter(hub)
                }
                onboarding = OnboardingModel(me: me, agent: agent, service: service,
                                             looks: makeLookService(hub))
                screen = .onboarding
            } catch {
                // Offline right after a launch: the chat shows what is saved; onboarding is asked again next time.
                screen = .main
            }
        }
    }

    /// "Start chatting" went through: on to the chat, and remembered for this hub.
    public func finishedOnboarding() {
        guard case .paired(let hub) = connection.phase else { return }
        enter(hub)
    }

    private func enter(_ hub: PairedHub) {
        defaults.set(hub.serverURL.absoluteString, forKey: Self.onboardedKey)
        onboarding = nil
        screen = .main
    }

    /// "Sign out of this phone": tells the hub to forget this phone, then forgets the hub, the sign-in and the cache
    /// here, and shows the welcome screen. Returns a note when the hub could not be told.
    @discardableResult
    public func signOut() async -> String? {
        var note: String?
        if case .paired(let hub) = connection.phase {
            do {
                try await makeService(hub).signOut(deviceId: hub.deviceId)
            } catch let error as WelcomeError where error == .unpaired {
                // The hub had forgotten this phone already.
            } catch {
                note = "Signed out, but the hub wasn't told, so it may still send to this phone. "
                    + "Remove this phone under Phones on another phone."
            }
        }
        connection.unpair()
        await update()
        return note
    }
}
