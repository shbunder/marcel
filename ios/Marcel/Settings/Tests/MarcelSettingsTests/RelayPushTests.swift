import CryptoKit
import Foundation
import MarcelKit
import XCTest

@testable import MarcelSettings

/// S-22.16: turning notifications on registers with Marcel's push relay and gives the hub the target; ntfy when the
/// relay cannot be reached; signing out forgets the handle and the key. Through the real generated hub client (the fake
/// hub) and fakes for Apple and the relay; nothing reaches the network.
@MainActor
final class RelayPushTests: XCTestCase {
    static let token = APNsToken.hex(Data((0..<32).map { UInt8($0 * 5 & 0xFF) }))
    static let handle = "hY3kQ9vT2mW8pL5xR1nB7c"
    static let secret = "Zk1QbXJ4V2c5dEw3cU5hYjJ5Rk1uVDhvSzZlUjRzVmQ"

    /// A relay that answers as told and remembers what it was asked.
    final class FakeRelay: PushRelay, @unchecked Sendable {
        var failure: RelayError?
        var forgetFails = false
        private(set) var registered: [(token: String, sandbox: Bool)] = []
        private(set) var forgotten: [String] = []
        private var made = 0

        func register(apnsToken: String, sandbox: Bool) async throws -> RelayRegistration {
            registered.append((apnsToken, sandbox))
            if let failure { throw failure }
            made += 1
            let handle = made == 1 ? RelayPushTests.handle : String(RelayPushTests.handle.dropLast()) + "\(made)"
            return RelayRegistration(relayURL: "https://relay.test", handle: handle,
                                     sendSecret: RelayPushTests.secret, apnsToken: apnsToken)
        }

        func forget(_ registration: RelayRegistration) async throws {
            forgotten.append(registration.handle)
            if forgetFails { throw RelayError.unreachable }
        }
    }

    final class FakeKeys: PushKeyStore, @unchecked Sendable {
        var key: Curve25519.KeyAgreement.PrivateKey?
        var failing = false
        func publicKey() throws -> String {
            if failing { throw URLError(.cannotCreateFile) }
            if key == nil { key = .init() }
            return Base64URL.encode(key!.publicKey.rawRepresentation)
        }
        func privateKey() throws -> Curve25519.KeyAgreement.PrivateKey? { key }
        func delete() throws { key = nil }
    }

    @MainActor
    struct Rig {
        let hub = FakeHub()
        let tokens = FakeTokens()
        let relay = FakeRelay()
        let keys = FakeKeys()
        let saved = RelayRegistrationStore(store: PairedStores.Token(nil))
        var setup: RelaySetup { RelaySetup(relay: relay, sandbox: false, keys: keys, saved: saved) }

        init(relayOnHub: Bool? = true, apns: Bool = false) {
            hub["GET /me"] = (200, Fx.json(Fx.me(apns: apns, relay: relayOnHub)))
            hub["GET /devices"] = (200, Fx.json(Fx.devices(phonePush: "none")))
            hub["PUT /devices/dev_phone/push"] = (200, Fx.json(Fx.devices(phonePush: "relay")[0]))
            tokens.result = .success(RelayPushTests.token)
        }

        func model(relay: Bool = true) async -> SettingsModel {
            let model = SettingsModel(service: hub.service, tokens: tokens, topics: NtfyTopicStore(defaults: .scratch()),
                                      relay: relay ? setup : nil, thisDevice: "dev_phone")
            await model.load()
            return model
        }
    }

    // MARK: Turning notifications on

    func testTurningOnRegistersWithTheRelayAndGivesTheHubTheTarget() async throws {
        let rig = Rig()
        let model = await rig.model()
        await model.turnOnNotifications()
        XCTAssertEqual(rig.relay.registered.map { $0.token }, [Self.token])
        XCTAssertEqual(rig.relay.registered.map { $0.sandbox }, [false], "a TestFlight build")
        let deviceKey = try XCTUnwrap(rig.keys.key.map { Base64URL.encode($0.publicKey.rawRepresentation) })
        XCTAssertEqual(rig.hub.lastBody, ["kind": "relay", "relay_url": "https://relay.test", "handle": Self.handle,
                                          "send_secret": Self.secret, "device_key": deviceKey])
        XCTAssertFalse(rig.hub.sentBodies.joined().contains(Base64URL.encode(rig.keys.key!.rawRepresentation)),
                       "the private key never leaves the phone")
        XCTAssertEqual(model.push, .apns, "the notices come by Apple's push service")
        XCTAssertNil(model.pushNote)
        XCTAssertNil(model.problem)
        XCTAssertEqual(model.devices.value?.first?.push, .relay)
        XCTAssertEqual(rig.saved.load()?.handle, Self.handle, "kept for sign out")
        XCTAssertEqual(rig.saved.load()?.apnsToken, Self.token)
    }

    func testARelayThatCannotBeReachedMeansNtfy() async {
        for failure in [RelayError.unreachable, .busy, .refused(400, "bad_request"), .badAnswer] {
            let rig = Rig()
            rig.relay.failure = failure
            rig.hub["PUT /devices/dev_phone/push"] = (200, Fx.json(Fx.devices()[0]))
            let model = await rig.model()
            await model.turnOnNotifications()
            guard case .ntfy(let topic, _) = model.push else { return XCTFail("\(failure): \(model.push)") }
            XCTAssertEqual(rig.hub.lastBody, ["kind": "ntfy", "ntfy_topic": topic], "\(failure)")
            XCTAssertEqual(model.pushNote,
                           "Marcel's push service didn't answer. Notifications through ntfy: install the ntfy app.")
            XCTAssertNil(rig.saved.load(), "nothing kept")
        }
    }

    /// The owner's own build, with the hub's APNs key: the relay down still leaves Apple's push service directly.
    func testARelayThatIsDownFallsBackToAPNsWhenTheHubHasIt() async {
        let rig = Rig(apns: true)
        rig.relay.failure = .unreachable
        rig.hub["PUT /devices/dev_phone/push"] = (200, Fx.json(Fx.devices(phonePush: "apns")[0]))
        let model = await rig.model()
        await model.turnOnNotifications()
        XCTAssertEqual(rig.hub.lastBody, ["kind": "apns", "apns_token": Self.token])
        XCTAssertEqual(model.push, .apns)
    }

    func testAKeychainThatRefusesTheKeySpendsNoRegistration() async {
        let rig = Rig()
        rig.keys.failing = true
        rig.hub["PUT /devices/dev_phone/push"] = (200, Fx.json(Fx.devices()[0]))
        let model = await rig.model()
        await model.turnOnNotifications()
        XCTAssertTrue(rig.relay.registered.isEmpty, "the key comes first")
        guard case .ntfy = model.push else { return XCTFail("\(model.push)") }
    }

    func testAHubThatRefusesTheTargetForgetsTheNewHandle() async {
        let rig = Rig()
        rig.hub["PUT /devices/dev_phone/push"] = (400, Fx.error("bad_request", "device_key is not a valid key."))
        let model = await rig.model()
        await model.turnOnNotifications()
        XCTAssertEqual(rig.relay.forgotten, [Self.handle], "no handle left at the relay that no hub can use")
        XCTAssertNil(rig.saved.load())
        XCTAssertEqual(model.problem, "device_key is not a valid key.")
        XCTAssertEqual(model.push, .off)
    }

    /// A hub from before app API 0.8.0 (no `push.relay`), and a build with no relay address: the relay is not used.
    func testNoRelayWithoutBothTheHubAndTheBuild() async {
        let older = Rig(relayOnHub: nil)
        older.hub["PUT /devices/dev_phone/push"] = (200, Fx.json(Fx.devices()[0]))
        let olderModel = await older.model()
        await olderModel.turnOnNotifications()
        XCTAssertTrue(older.relay.registered.isEmpty)
        XCTAssertEqual(older.tokens.asked, 0, "nothing of Apple's is needed for ntfy")
        guard case .ntfy = olderModel.push else { return XCTFail("\(olderModel.push)") }

        let noAddress = Rig()
        noAddress.hub["PUT /devices/dev_phone/push"] = (200, Fx.json(Fx.devices()[0]))
        let noAddressModel = await noAddress.model(relay: false)
        await noAddressModel.turnOnNotifications()
        XCTAssertEqual(noAddress.tokens.asked, 0)
        guard case .ntfy = noAddressModel.push else { return XCTFail("\(noAddressModel.push)") }
        XCTAssertNil(noAddressModel.pushNote)
    }

    func testNoPushEntitlementMeansNtfyWithoutAskingTheRelay() async {
        let rig = Rig()
        rig.tokens.result = .failure(PushSetupError.apnsUnavailable)
        rig.hub["PUT /devices/dev_phone/push"] = (200, Fx.json(Fx.devices()[0]))
        let model = await rig.model()
        await model.turnOnNotifications()
        XCTAssertTrue(rig.relay.registered.isEmpty)
        XCTAssertEqual(model.pushNote,
                       "This build can't use Apple's push service, so notifications go through the ntfy app.")
    }

    // MARK: A new APNs token

    func testANewTokenRegistersAgainAndForgetsTheOldHandle() async {
        let rig = Rig()
        let model = await rig.model()
        await model.turnOnNotifications()
        let unchanged = await rig.setup.tokenChanged(Self.token, deviceId: "dev_phone", service: rig.hub.service)
        XCTAssertFalse(unchanged, "the same token at launch spends no registration")
        XCTAssertEqual(rig.relay.registered.count, 1)

        let newToken = String(repeating: "ab", count: 32)
        let changed = await rig.setup.tokenChanged(newToken, deviceId: "dev_phone", service: rig.hub.service)
        XCTAssertTrue(changed)
        XCTAssertEqual(rig.relay.registered.last?.token, newToken)
        XCTAssertEqual(rig.hub.lastBody?["handle"] as? String, rig.saved.load()?.handle)
        XCTAssertNotEqual(rig.saved.load()?.handle, Self.handle)
        XCTAssertEqual(rig.relay.forgotten, [Self.handle], "the old handle is forgotten")
    }

    func testATokenForAPhoneNotOnTheRelayDoesNothing() async {
        let rig = Rig()
        let changed = await rig.setup.tokenChanged(Self.token, deviceId: "dev_phone", service: rig.hub.service)
        XCTAssertFalse(changed)
        XCTAssertTrue(rig.relay.registered.isEmpty)
        XCTAssertFalse(rig.setup.inUse)
    }

    // MARK: Sign out

    func testSignOutForgetsTheHandleAndTheKey() async {
        let rig = Rig()
        let model = await rig.model()
        await model.turnOnNotifications()
        XCTAssertNotNil(rig.keys.key)
        var ran = 0
        _ = await model.signOut { ran += 1; return nil }
        XCTAssertEqual(rig.relay.forgotten, [Self.handle])
        XCTAssertNil(rig.keys.key, "the private key is gone")
        XCTAssertNil(rig.saved.load())
        XCTAssertEqual(ran, 1)
    }

    func testSignOutWhileTheRelayIsDownStillForgetsEverythingOnThePhone() async {
        let rig = Rig()
        let model = await rig.model()
        await model.turnOnNotifications()
        rig.relay.forgetFails = true
        var ran = 0
        let note = await model.signOut { ran += 1; return "Signed out, but the hub wasn't told." }
        XCTAssertEqual(ran, 1, "a relay that is down never stops a sign out")
        XCTAssertEqual(note, "Signed out, but the hub wasn't told.")
        XCTAssertNil(rig.keys.key)
        XCTAssertNil(rig.saved.load())
    }

    // MARK: The live relay client

    final class Wire: @unchecked Sendable {
        var requests: [URLRequest] = []
        var answer: (Int, String) = (201, #"{"handle":"hY3kQ9vT2mW8pL5xR1nB7c","send_secret":"Zk1QbXJ4V2c5dEw3cU5hYjJ5Rk1uVDhvSzZlUjRzVmQ"}"#)
        var offline = false

        func relay() -> LiveRelay {
            LiveRelay(url: URL(string: "https://relay.test/base")!) { [self] request in
                requests.append(request)
                if offline { throw URLError(.timedOut) }
                let response = HTTPURLResponse(url: request.url!, statusCode: answer.0, httpVersion: nil,
                                               headerFields: ["content-type": "application/json"])!
                return (Data(answer.1.utf8), response)
            }
        }
    }

    func testTheLiveRelayRegistersAsTheContractSays() async throws {
        let wire = Wire()
        let registration = try await wire.relay().register(apnsToken: Self.token, sandbox: true)
        XCTAssertEqual(registration, RelayRegistration(relayURL: "https://relay.test/base", handle: Self.handle,
                                                       sendSecret: Self.secret, apnsToken: Self.token))
        let request = try XCTUnwrap(wire.requests.first)
        XCTAssertEqual(request.httpMethod, "POST")
        XCTAssertEqual(request.url?.absoluteString, "https://relay.test/base/v1/register")
        XCTAssertEqual(request.value(forHTTPHeaderField: "Content-Type"), "application/json")
        let body = try XCTUnwrap(JSONSerialization.jsonObject(with: request.httpBody ?? Data()) as? NSDictionary)
        XCTAssertEqual(body, ["apns_token": Self.token, "sandbox": true])
    }

    func testTheLiveRelaySaysWhyItCouldNotRegister() async {
        let cases: [((Int, String), Bool, RelayError)] = [
            ((429, #"{"code":"too_many_registrations","message":"Try later."}"#), false, .busy),
            ((503, ""), false, .unreachable),
            ((400, #"{"code":"bad_request","message":"No."}"#), false, .refused(400, "bad_request")),
            ((201, #"{"handle":"short","send_secret":"x"}"#), false, .badAnswer),
            ((201, "not json"), false, .badAnswer),
            ((201, ""), true, .unreachable),
        ]
        for (answer, offline, expected) in cases {
            let wire = Wire()
            wire.answer = answer
            wire.offline = offline
            do {
                _ = try await wire.relay().register(apnsToken: Self.token, sandbox: false)
                XCTFail("\(answer): expected \(expected)")
            } catch {
                XCTAssertEqual(error as? RelayError, expected, "\(answer)")
            }
        }
    }

    func testTheLiveRelayForgetsAHandleWithItsSecret() async throws {
        let wire = Wire()
        wire.answer = (204, "")
        let registration = RelayRegistration(relayURL: "https://relay.test/base", handle: Self.handle,
                                             sendSecret: Self.secret, apnsToken: Self.token)
        try await wire.relay().forget(registration)
        let request = try XCTUnwrap(wire.requests.first)
        XCTAssertEqual(request.httpMethod, "DELETE")
        XCTAssertEqual(request.url?.absoluteString, "https://relay.test/base/v1/handles/\(Self.handle)")
        XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer \(Self.secret)")

        wire.answer = (401, #"{"code":"unauthorized","message":"No."}"#)
        do {
            try await wire.relay().forget(registration)
            XCTFail("a refused delete throws")
        } catch {
            XCTAssertEqual(error as? RelayError, .refused(401, "unauthorized"))
        }
    }

    func testTheBuildsRelayAddressMustBeAPlainHTTPSAddress() {
        XCTAssertEqual(LiveRelay.address("https://marcel-relay.shaun.workers.dev/")?.absoluteString,
                       "https://marcel-relay.shaun.workers.dev")
        XCTAssertEqual(LiveRelay.address("https://relay.example/marcel")?.absoluteString, "https://relay.example/marcel")
        for bad in [nil, "", "  ", "http://relay.example", "https://user@relay.example", "https://relay.example?x=1",
                    "https://", "$(MARCEL_RELAY_URL)", "https:/$()/relay.example", "relay.example"] as [String?] {
            XCTAssertNil(LiveRelay.address(bad), String(describing: bad))
        }
        XCTAssertNil(RelaySetup.live(sandbox: false, bundle: Bundle(for: Self.self)), "the test bundle has no address")
    }

    /// The send secret is a password: in the Keychain, never in UserDefaults.
    func testTheRegistrationIsKeptInTheKeychain() throws {
        let keychain = KeychainTokenStore(service: "test.push-relay.\(UUID())")
        defer { try? keychain.delete() }
        let store = RelayRegistrationStore(store: keychain)
        XCTAssertNil(store.load())
        let registration = RelayRegistration(relayURL: "https://relay.test", handle: Self.handle,
                                             sendSecret: Self.secret, apnsToken: Self.token)
        try store.save(registration)
        XCTAssertEqual(RelayRegistrationStore(store: keychain).load(), registration)
        let defaults = UserDefaults.standard.dictionaryRepresentation().values.map { "\($0)" }.joined()
        XCTAssertFalse(defaults.contains(Self.secret))
        store.delete()
        XCTAssertNil(store.load())
    }
}
