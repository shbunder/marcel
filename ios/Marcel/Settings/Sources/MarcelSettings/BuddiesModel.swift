import CoreImage
import CoreImage.CIFilterBuiltins
import Foundation
import MarcelKit
import MarcelLook
import Observation

/// Settings › Buddies (B-49, ADR-013; it replaces People): every live buddy with its avatar, to rename, give a new look
/// or archive; and "Pair another phone", a one-time code as a QR and a link, optionally starting on one buddy.
@MainActor
@Observable
public final class BuddiesModel {
    /// A code to show: the code, its `marcel://pair` link, when it stops working, and the buddy it starts on.
    public struct Code: Equatable, Sendable {
        public var code: String
        public var link: String
        public var expiresAt: Date
        /// The buddy's name the new phone starts on; nil for "the first live buddy".
        public var startsOn: String?
    }

    public private(set) var buddies: Loadable<[Agent]> = .loading
    /// The code on screen after "Make a code".
    public private(set) var code: Code?
    /// The buddy the next code starts on; nil for the first live buddy. Changing it takes the old code off the screen,
    /// so the card never names another buddy than the one picked.
    public var startOn: String? {
        didSet { if startOn != oldValue { code = nil } }
    }
    /// After archiving: "Pip is archived. Its chat and notebook stay readable."
    public private(set) var archived: String?
    public var problem: String?
    public private(set) var busy = false

    /// Each buddy drawn from its look (`GET /avatar-previews`), by look.
    public private(set) var avatars: [KnownLook: Data] = [:]

    @ObservationIgnored let service: any SettingsService
    @ObservationIgnored let looks: (any LookService)?
    @ObservationIgnored let buddy: CurrentBuddy
    @ObservationIgnored private var asked: Set<KnownLook> = []

    /// - Parameter looks: draws each buddy's avatar from its look and edits it; without it there is no look picker.
    public init(service: any SettingsService, looks: (any LookService)? = nil, buddy: CurrentBuddy = .shared) {
        self.service = service
        self.looks = looks
        self.buddy = buddy
    }

    /// The buddy this phone shows.
    public var shown: String? { buddy.agentId }

    /// `agent`'s avatar as a 32 × 32 PNG, once it has been drawn.
    public func avatar(for agent: Agent) -> Data? {
        agent.drawnLook.flatMap { avatars[$0] }
    }

    func loadAvatars(_ list: [Agent]) async {
        guard let looks else { return }
        for look in Set(list.compactMap(\.drawnLook)) where asked.insert(look).inserted {
            if let png = try? await looks.preview(look) {
                avatars[look] = png
            } else {
                asked.remove(look)
            }
        }
    }

    public func load() async {
        do {
            let list = try await service.me().agents.filter { $0.archivedAt == nil }
            buddies = .loaded(list)
            await loadAvatars(list)
        } catch {
            buddies = .from(error)
        }
    }

    /// Renames `agent` (`PATCH /agents/{id}`).
    @discardableResult
    public func rename(_ agent: Agent, to name: String) async -> Bool {
        let name = name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !name.isEmpty, name != agent.name, !busy else { return false }
        busy = true
        defer { busy = false }
        do {
            _ = try await service.rename(agentId: agent.id, to: String(name.prefix(40)))
            await load()
            problem = nil
            return true
        } catch {
            problem = SettingsError.words(for: error)
            return false
        }
    }

    /// What archiving `agent` does, said before and after.
    public static func archiveWords(_ agent: Agent) -> String {
        "\(agent.name) stops working and leaves your buddies. Its chat and notebook stay readable."
    }

    /// Archives `agent` (asked first on screen). A phone that showed it moves to another buddy (the hub says so with
    /// `device.updated`).
    public func archive(_ agent: Agent) async {
        guard !busy else { return }
        busy = true
        defer { busy = false }
        do {
            _ = try await service.archive(agentId: agent.id)
            archived = "\(agent.name) is archived. Its chat and notebook stay readable."
            if case .loaded(let list) = buddies { buddies = .loaded(list.filter { $0.id != agent.id }) }
            if let list = try? await service.me().agents.filter({ $0.archivedAt == nil }) { buddies = .loaded(list) }
            problem = nil
        } catch {
            problem = SettingsError.words(for: error)
        }
    }

    public func dismissArchived() { archived = nil }

    /// A fresh ten-minute code for another phone, starting on the buddy picked in `startOn` (if still live).
    public func newCode() async {
        guard !busy else { return }
        busy = true
        defer { busy = false }
        let agent = buddies.value?.first { $0.id == startOn }
        do {
            let issued = try await service.pairingCode(agentId: agent?.id)
            code = Code(code: issued.code, link: issued.link, expiresAt: issued.expiresAt, startsOn: agent?.name)
            problem = nil
        } catch {
            problem = SettingsError.words(for: error)
        }
    }

    public func closeCode() { code = nil }

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
