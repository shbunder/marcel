import Foundation
import MarcelKit
import Observation

/// One section's data: still loading, there, not built on the hub yet, or a problem in plain words.
public enum Loadable<Value: Equatable>: Equatable {
    case loading
    case loaded(Value)
    /// The hub answered 404 / 405 / 501: the contract has the route, this hub has not built it yet.
    case notAvailableYet
    case failed(String)

    public var value: Value? {
        if case .loaded(let value) = self { return value }
        return nil
    }

    static func from(_ error: any Error) -> Loadable {
        (error as? SettingsError) == .notAvailableYet ? .notAvailableYet : .failed(SettingsError.words(for: error))
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

    public init(service: any SettingsService, tokens: any PushTokenSource, topics: NtfyTopicStore = NtfyTopicStore()) {
        self.service = service
        self.tokens = tokens
        self.topics = topics
    }

    // MARK: Loading

    /// Reads every section. Each one that fails says so in its own place.
    public func load() async {
        do {
            let me = try await service.me()
            self.me = me
            if name.isEmpty || name == agent?.name { name = me.agents.first?.name ?? "" }
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
            problem = nil
        } catch {
            problem = (error as? SettingsError) == .notAvailableYet
                ? "Not available yet: this hub can't rename Marcel yet." : SettingsError.words(for: error)
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
            problem = nil
        } catch {
            problem = SettingsError.words(for: error)
        }
    }

    public func delete(_ schedule: Schedule) async {
        guard !busy else { return }
        guard !schedule.builtin else {
            problem = SettingsError.builtIn.errorDescription
            return
        }
        busy = true
        defer { busy = false }
        do {
            try await service.delete(scheduleId: schedule.id)
            replace(schedule.id) { _ in nil }
            problem = nil
        } catch {
            problem = SettingsError.words(for: error)
        }
    }

    private func replace(_ id: String, _ change: (Schedule) -> Schedule?) {
        guard case .loaded(let list) = schedules else { return }
        schedules = .loaded(list.compactMap { $0.id == id ? change($0) : $0 })
    }

    // MARK: Devices

    /// Revokes another device. This phone is unpaired instead (it forgets the hub too).
    public func revoke(_ device: Device) async {
        guard device.current != true else {
            problem = "To remove this phone, use Unpair this phone."
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
