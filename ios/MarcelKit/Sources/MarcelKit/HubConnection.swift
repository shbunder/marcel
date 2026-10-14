import Foundation
import Observation
import OpenAPIRuntime
import OpenAPIURLSession

/// The app's one link to its hub: pairing, the live event stream and the cache, wired together.
@MainActor
@Observable
public final class HubConnection {
    public enum Phase: Equatable {
        case unpaired
        case paired(PairedHub)
    }

    public private(set) var phase: Phase
    public private(set) var stream: EventStream?

    @ObservationIgnored public let tokenStore: any TokenStore
    @ObservationIgnored public let hubStore: any PairedHubStore
    @ObservationIgnored public let cache: MarcelCache
    @ObservationIgnored private let transport: any ClientTransport
    @ObservationIgnored private let connectorFactory: @MainActor (PairedHub) -> any EventSocketConnector
    @ObservationIgnored private var streaming: Task<Void, Never>?
    /// What screens keep of their own from this hub (the notebook's drafts and rollover log), forgotten on unpair.
    @ObservationIgnored private var forgetters: [UUID: @MainActor () -> Void] = [:]

    public init(
        tokenStore: any TokenStore,
        hubStore: any PairedHubStore,
        cache: MarcelCache,
        transport: any ClientTransport,
        connectorFactory: @escaping @MainActor (PairedHub) -> any EventSocketConnector
    ) {
        self.tokenStore = tokenStore
        self.hubStore = hubStore
        self.cache = cache
        self.transport = transport
        self.connectorFactory = connectorFactory
        let hub = hubStore.load()
        let hasToken = (try? tokenStore.token()) != nil
        phase = hub.flatMap { hasToken ? .paired($0) : nil } ?? .unpaired
    }

    /// Production wiring: the token in the Keychain, the hub's address in UserDefaults.
    public static func live(
        cache: MarcelCache,
        transport: any ClientTransport = HubClient.transport,
        defaults: UserDefaults = .standard
    ) -> HubConnection {
        let tokenStore = KeychainTokenStore()
        return HubConnection(
            tokenStore: tokenStore,
            hubStore: UserDefaultsPairedHubStore(defaults: defaults),
            cache: cache,
            transport: transport,
            connectorFactory: { URLSessionEventSocketConnector(serverURL: $0.serverURL, tokenStore: tokenStore) }
        )
    }

    private var pairing: PairingService {
        PairingService(tokenStore: tokenStore, hubStore: hubStore, transport: transport)
    }

    /// Pairs with the hub in the scanned QR code, then starts the live stream.
    public func pair(scanned text: String, deviceName: String) async throws(PairingError) {
        let hub = try await pairing.pair(scanned: text, deviceName: deviceName)
        phase = .paired(hub)
        start()
    }

    /// Opens the event stream if paired. Safe to call again; a running stream is left alone.
    public func start() {
        guard case .paired(let hub) = phase, streaming == nil else { return }
        cache.refresher = RESTCacheRefresher(
            client: HubClient.make(serverURL: hub.serverURL, tokenStore: tokenStore, transport: transport))
        let stream = EventStream(
            connector: connectorFactory(hub), sink: cache,
            onUnpaired: { [weak self] in self?.unpair() })
        self.stream = stream
        streaming = Task { await stream.run() }
    }

    public func stop() {
        streaming?.cancel()
        streaming = nil
    }

    /// Forgets the hub and everything cached from it, including what was registered with `onUnpair`. Runs when the
    /// person unpairs and when the hub refuses this phone.
    public func unpair() {
        stop()
        pairing.unpair()
        try? cache.clear()
        for forget in forgetters.values { forget() }
        stream = nil
        phase = .unpaired
    }

    /// Runs `forget` every time this phone is unpaired: for data a screen keeps on the phone outside the cache.
    @discardableResult
    public func onUnpair(_ forget: @escaping @MainActor () -> Void) -> UUID {
        let token = UUID()
        forgetters[token] = forget
        return token
    }

    public func removeOnUnpair(_ token: UUID) {
        forgetters[token] = nil
    }
}
