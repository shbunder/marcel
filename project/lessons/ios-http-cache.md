# iOS: the hub client keeps no HTTP cache, and sends ETags as given

Found by S-19.4's mock-hub test (a new look never reached the chat header).

- **The system URL cache keys by URL alone.** `GET /agents/{id}/avatar` serves two bodies at one URL, picked by
  `Accept` (the sheet's JSON, its PNG), with one ETag. `URLSession.shared` cached the JSON, then sent the PNG request
  with the JSON's fresh ETag as `If-None-Match` on its own; the hub answered 304 and the app kept the old sheet. The
  cache also survives between test runs in the simulator, so a "fresh" run still sent stale tags. Every hub request
  now goes through `HubClient.session` (no URL cache, `reloadIgnoringLocalCacheData`): the app keeps its own cache in
  SwiftData. A hub that serves bodies by `Accept` should also send `Vary: Accept`, for clients that do cache.
- **The generated client percent-encodes header values.** `If-None-Match: "lk-fox"` went out as `%22lk-fox%22`,
  never matched, and the hub never answered 304. `ETagMiddleware` in `HubClient.swift` sends it as the hub gave it.
- **How it was found:** log the request headers in the mock hub (`dict(request.headers)`), then call the same
  request from a one-off test on the simulator and from a `swift` script on the Mac. The Mac script showed the shared
  session adding `If-None-Match` and a cache-less session not.
- Tests: `HubClientTests` in MarcelKit (no cache; the ETag sent unencoded) and
  `ChatMockHubTests.testPickingALookOnTheMockHubShowsInTheHeader`.
