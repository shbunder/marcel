import CoreImage
import CoreImage.CIFilterBuiltins
import Foundation
import MarcelKit
import MarcelLook
import Observation

/// People (B-37, owner only): everyone with their helper, phones and quests this week; adding a person (a pairing
/// code as a QR and a link, with the time left); removing one after saying what happens.
@MainActor
@Observable
public final class PeopleModel {
    /// A code to show: who it is for, the code, its `marcel://pair` link, and when it stops working.
    public struct Code: Equatable, Sendable {
        public var person: String
        public var code: String
        public var link: String
        public var expiresAt: Date
    }

    public private(set) var people: Loadable<[User]> = .loading
    /// The code on screen after adding someone or asking for a new one.
    public private(set) var code: Code?
    /// After removing someone: "Emma's phones are signed out and Emma's helper, Pip, stops."
    public private(set) var removed: String?
    public var problem: String?
    public private(set) var busy = false

    /// Each helper drawn from its look (`GET /avatar-previews`), by look: the owner cannot fetch another person's
    /// sheet (`GET /agents/{id}/avatar` is 404 for agents that are not yours), so People draws the look instead.
    public private(set) var avatars: [KnownLook: Data] = [:]

    @ObservationIgnored let service: any SettingsService
    @ObservationIgnored let looks: (any LookService)?
    @ObservationIgnored private var asked: Set<KnownLook> = []

    /// - Parameter looks: draws each helper's avatar from its look; without it the cards show none.
    public init(service: any SettingsService, looks: (any LookService)? = nil) {
        self.service = service
        self.looks = looks
    }

    /// `user`'s helper as a 32 × 32 PNG, once it has been drawn.
    public func avatar(for user: User) -> Data? {
        user.agent?.look?.known.flatMap { avatars[$0] }
    }

    /// Draws every listed helper's look once; one that fails is asked again on the next load.
    func loadAvatars(_ list: [User]) async {
        guard let looks else { return }
        for look in Set(list.compactMap { $0.agent?.look?.known }) where asked.insert(look).inserted {
            if let png = try? await looks.preview(look) {
                avatars[look] = png
            } else {
                asked.remove(look)
            }
        }
    }

    public func load() async {
        do {
            let list = try await service.users()
            people = .loaded(list)
            await loadAvatars(list)
        } catch {
            people = .from(error)
        }
    }

    /// What removing `user` does, said before and after: their phones and their helper, by name.
    public static func removalWords(_ user: User) -> String {
        let helper = user.agent.map { "\(user.name)'s helper, \($0.name)," } ?? "\(user.name)'s helper"
        return "\(user.name)'s phones are signed out and \(helper) stops. Their chats and notes stay on the hub."
    }

    /// Adds a person and their helper, then makes the code for their first phone.
    @discardableResult
    public func add(name: String, helperName: String) async -> Bool {
        let name = name.trimmingCharacters(in: .whitespacesAndNewlines)
        let helper = helperName.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !name.isEmpty, !helper.isEmpty else {
            problem = "Type their name and their helper's name."
            return false
        }
        guard !busy else { return false }
        busy = true
        defer { busy = false }
        do {
            let user = try await service.addUser(name: String(name.prefix(40)), helperName: String(helper.prefix(40)))
            code = nil
            await load()
            await issueCode(for: user)
            if code == nil {
                // Added, but no code: say so, so nobody adds them twice.
                problem = "\(user.name) was added, but the hub didn't make a code. Tap New code next to their name."
            }
            return true
        } catch {
            problem = SettingsError.words(for: error)
            return false
        }
    }

    /// A fresh ten-minute code for `user`'s phone.
    public func newCode(for user: User) async {
        guard !busy else { return }
        busy = true
        defer { busy = false }
        await issueCode(for: user)
    }

    private func issueCode(for user: User) async {
        do {
            let issued = try await service.pairingCode(userId: user.id)
            code = Code(person: user.name, code: issued.code, link: issued.link, expiresAt: issued.expiresAt)
            problem = nil
        } catch {
            problem = SettingsError.words(for: error)
        }
    }

    public func closeCode() { code = nil }

    /// Removes `user` (asked first on screen); says what happened and drops them from the list.
    public func remove(_ user: User) async {
        guard !busy else { return }
        busy = true
        defer { busy = false }
        do {
            try await service.removeUser(userId: user.id)
            removed = Self.removalWords(user)
            if case .loaded(let list) = people { people = .loaded(list.filter { $0.id != user.id }) }
            // Refresh, but keep the trimmed list if the hub can't be asked right now.
            if let list = try? await service.users() { people = .loaded(list) }
            problem = nil
        } catch {
            problem = SettingsError.words(for: error)
        }
    }

    public func dismissRemoved() { removed = nil }

    /// "Expires in 9 min", "Expires in under a minute", "Expired: make a new code".
    public static func timeLeft(_ code: Code, now: Date) -> String {
        let seconds = code.expiresAt.timeIntervalSince(now)
        if seconds <= 0 { return "Expired: make a new code." }
        if seconds < 60 { return "Expires in under a minute." }
        return "Expires in \(Int((seconds / 60).rounded(.up))) min."
    }

    /// The code's link as a QR image, sharp at any size.
    public static func qr(_ link: String) -> CGImage? {
        let filter = CIFilter.qrCodeGenerator()
        filter.message = Data(link.utf8)
        filter.correctionLevel = "M"
        guard let image = filter.outputImage else { return nil }
        return CIContext().createCGImage(image, from: image.extent)
    }
}
