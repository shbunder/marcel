import Foundation
import MarcelKit
import Observation

/// One section's data: still loading, there, not built on the hub yet, or a problem in plain words.
public enum Loadable<Value: Equatable>: Equatable {
    case loading
    case loaded(Value)
    /// The hub answered 404 / 405 / 501: the contract has the route, this hub has not built it yet.
    case notAvailableYet
    /// The hub is fine but has nothing to show yet (usage before the first reading): a plain note, not a problem.
    case unavailable(String)
    case failed(String)

    public var value: Value? {
        if case .loaded(let value) = self { return value }
        return nil
    }

    static func from(_ error: any Error) -> Loadable {
        switch error as? SettingsError {
        case .notAvailableYet: .notAvailableYet
        case .noReading: .unavailable(SettingsError.words(for: error))
        default: .failed(SettingsError.words(for: error))
        }
    }
}

/// Where this phone's notifications go.
public enum PushRoute: Equatable, Sendable {
    /// Nothing registered yet.
    case off
    /// Apple's push service.
    case apns
    /// The ntfy app, subscribed to `topic` on `server`.
    case ntfy(topic: String, server: String)
}

/// Settings' state: the agent's name, notifications, plan usage, schedules and devices. Each section loads and fails
/// on its own, so a hub that has not built schedules yet still shows everything else.
@MainActor
@Observable
public final class SettingsModel {
    public private(set) var me: Me?
    /// People is the owner's only (B-37): a family member never sees it.
    public var showsPeople: Bool { me?.role == .owner }
    /// The agent Settings is about (the first one; a household has one agent per person).
    public var agent: Agent? { me?.agents.first }
    /// What is typed in the name field.
    public var name = ""
    public private(set) var push: PushRoute = .off
    public private(set) var devices: Loadable<[Device]> = .loading
    public private(set) var schedules: Loadable<[Schedule]> = .loading
    public private(set) var usage: Loadable<Usage> = .loading
    /// What went wrong last, in plain words, shown at the top. Tapping it clears it.
    public var problem: String?
    /// A plain-words note under the notifications section (how to set up ntfy, why APNs was skipped).
    public private(set) var pushNote: String?
    /// True while something is being sent, to stop a double tap.
    public private(set) var busy = false

    @ObservationIgnored let service: any SettingsService
    @ObservationIgnored let tokens: any PushTokenSource
    @ObservationIgnored let topics: NtfyTopicStore
    /// This phone's device id as pairing saved it, for when `/me` could not be read.
    @ObservationIgnored let thisDevice: String?
    /// Told the agent's name whenever the hub gives it (on load and after a rename), so the chat header can follow.
    @ObservationIgnored public var nameChanged: (String) -> Void = { _ in }

    public init(service: any SettingsService, tokens: any PushTokenSource, topics: NtfyTopicStore = NtfyTopicStore(),
                thisDevice: String? = nil) {
        self.service = service
        self.tokens = tokens
        self.topics = topics
        self.thisDevice = thisDevice
    }

    // MARK: Loading

    /// Reads every section. Each one that fails says so in its own place.
    public func load() async {
        do {
            let me = try await service.me()
            self.me = me
            if name.isEmpty || name == agent?.name { name = me.agents.first?.name ?? "" }
            if let agentName = me.agents.first?.name { nameChanged(agentName) }
            problem = nil
        } catch {
            problem = SettingsError.words(for: error)
        }
        await loadDevices()
        await loadSchedules()
        await loadUsage()
    }

    func loadDevices() async {
        do {
            let list = try await service.devices()
            devices = .loaded(list)
            push = route(for: list.first { $0.current == true }?.push)
        } catch {
            devices = .from(error)
        }
    }

    func loadSchedules() async {
        guard let agentId = agent?.id else {
            schedules = .failed("Couldn't read who Marcel is from the hub.")
            return
        }
        do {
            schedules = .loaded(try await service.schedules(agentId: agentId))
        } catch {
            schedules = .from(error)
        }
    }

    func loadUsage() async {
        do {
            usage = .loaded(try await service.usage())
        } catch {
            usage = .from(error)
        }
    }

    private func route(for kind: Device.PushPayload?) -> PushRoute {
        switch kind {
        case .apns: return .apns
        case .ntfy:
            guard let topic = topics.saved else { return .off }
            return .ntfy(topic: topic, server: ntfyServer)
        case nil, .some(.none): return .off
        }
    }

    var ntfyServer: String { me?.push.ntfyServer ?? "https://ntfy.sh" }

    // MARK: The agent's name

    /// The name as it would be sent, or nil when it cannot be (empty, too long, unchanged).
    public var nameToSave: String? {
        let trimmed = name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard (1...40).contains(trimmed.count), trimmed != agent?.name else { return nil }
        return trimmed
    }

    public func saveName() async {
        guard let agentId = agent?.id, let newName = nameToSave, !busy else { return }
        busy = true
        defer { busy = false }
        do {
            let updated = try await service.rename(agentId: agentId, to: newName)
            if var me {
                me.agents = me.agents.map { $0.id == updated.id ? updated : $0 }
                self.me = me
            }
            name = updated.name
            nameChanged(updated.name)
            problem = nil
        } catch {
            problem = (error as? SettingsError) == .notAvailableYet
                ? "Not available yet: this hub can't rename \(agent?.name ?? "the agent") yet."
                : SettingsError.words(for: error)
        }
    }

    // MARK: Notifications

    /// APNs when the hub can send through it and this build can receive it; ntfy otherwise (B-18).
    public func turnOnNotifications() async {
        guard let me, !busy else { return }
        busy = true
        defer { busy = false }
        if me.push.apns {
            do {
                let token = try await tokens.token()
                try await register(.init(kind: .apns, apnsToken: token))
                push = .apns
                pushNote = nil
                return
            } catch PushSetupError.notAllowed {
                problem = PushSetupError.notAllowed.errorDescription
                return
            } catch let error as SettingsError {
                problem = SettingsError.words(for: error)
                return
            } catch {
                // No entitlement in this build: fall through to ntfy, and say why.
                pushNote = "This build can't use Apple's push service, so notifications go through the ntfy app."
            }
        }
        await useNtfy()
    }

    /// Registers this phone's ntfy topic with the hub and says how to subscribe to it.
    public func useNtfy() async {
        guard let me else { return }
        guard me.push.ntfy else {
            problem = "The hub can't send notifications yet. Ask whoever runs it to set up APNs or ntfy."
            return
        }
        let topic = topics.topic()
        do {
            try await register(.init(kind: .ntfy, ntfyTopic: topic))
            push = .ntfy(topic: topic, server: ntfyServer)
        } catch {
            problem = SettingsError.words(for: error)
        }
    }

    private func register(_ target: Components.Schemas.PushTarget) async throws {
        guard let deviceId = me?.deviceId else { return }
        let device = try await service.setPush(deviceId: deviceId, target: target)
        if case .loaded(var list) = devices {
            list = list.map { $0.id == device.id ? device : $0 }
            devices = .loaded(list)
        }
    }

    /// What the ntfy app needs, step by step.
    public static func ntfySteps(topic: String, server: String) -> [String] {
        let host = URL(string: server)?.host() ?? server
        return [
            "Install the free ntfy app from the App Store.",
            "In ntfy, tap + and subscribe to the topic below. Server: \(host == "ntfy.sh" ? "the default" : server).",
            "Keep the topic to yourself: anyone who has it can read Marcel's notifications.",
        ]
    }

    // MARK: Schedules

    public func setPaused(_ schedule: Schedule, _ paused: Bool) async {
        guard !busy else { return }
        busy = true
        defer { busy = false }
        do {
            let updated = try await service.setPaused(scheduleId: schedule.id, paused)
            replace(schedule.id) { _ in updated }
            if pauseInstead?.id == schedule.id { pauseInstead = nil }
            problem = nil
        } catch {
            problem = SettingsError.words(for: error)
        }
    }

    public func delete(_ schedule: Schedule) async {
        guard !busy else { return }
        guard !schedule.builtin else {
            refuseDeleting(schedule)
            return
        }
        busy = true
        defer { busy = false }
        do {
            try await service.delete(scheduleId: schedule.id)
            replace(schedule.id) { _ in nil }
            problem = nil
        } catch SettingsError.builtIn {
            // The hub knows it as a built-in digest (409 `schedule_builtin`): say so, hide Delete, offer pause.
            refuseDeleting(schedule)
        } catch SettingsError.scheduleGone {
            replace(schedule.id) { _ in nil }
            problem = SettingsError.scheduleGone.errorDescription
        } catch {
            problem = SettingsError.words(for: error)
        }
    }

    /// A built-in digest that someone tried to delete: offered "Pause it instead" (unless it is paused already).
    public private(set) var pauseInstead: Schedule?

    private func refuseDeleting(_ schedule: Schedule) {
        problem = SettingsError.builtIn.errorDescription
        replace(schedule.id) { var builtin = $0; builtin.builtin = true; return builtin }
        pauseInstead = schedule.paused ? nil : schedule
    }

    /// "Pause it instead".
    public func pauseInsteadOfDeleting() async {
        guard let schedule = pauseInstead else { return }
        pauseInstead = nil
        await setPaused(schedule, true)
    }

    private func replace(_ id: String, _ change: (Schedule) -> Schedule?) {
        guard case .loaded(let list) = schedules else { return }
        schedules = .loaded(list.compactMap { $0.id == id ? change($0) : $0 })
    }

    // MARK: Devices

    /// "Sign out of this phone": forgets this phone's ntfy topic, then runs the app's sign-out (`AppFlow.signOut`:
    /// the hub forgets this phone, then the phone forgets the hub, the sign-in and the cache). Returns what to tell
    /// the person when the hub could not be told; the phone signs out either way.
    public func signOut(_ appSignOut: () async -> String?) async -> String? {
        topics.clear()
        return await appSignOut()
    }

    /// Revokes another device. This phone is unpaired instead (it forgets the hub too).
    public func revoke(_ device: Device) async {
        guard device.current != true else {
            problem = "To remove this phone, use Sign out of this phone."
            return
        }
        guard !busy else { return }
        busy = true
        defer { busy = false }
        do {
            try await service.revoke(deviceId: device.id)
            if case .loaded(let list) = devices { devices = .loaded(list.filter { $0.id != device.id }) }
            problem = nil
        } catch {
            problem = SettingsError.words(for: error)
        }
    }
}
