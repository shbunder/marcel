# Push targets are secrets — keep them out of URLs

An ntfy topic lets anyone read the pushes; an APNs device token identifies the phone. httpx logs
every request at INFO as `HTTP Request: POST <full URL> …`, so a secret in the URL path lands in
the hub's log even when our own code never logs it.

- The ntfy backend (S-08.1) publishes JSON to the server's root, so the topic travels in the body.
- APNs puts the device token in the path (`/3/device/<token>`). `notify/apns.py` adds a filter to
  the `httpx` logger that rewrites the path to `/3/device/<token>`, and keeps the `hpack` logger
  (which logs every HTTP/2 header it encodes, the path and the provider token included) at WARNING.
  `tests/test_apns.py::test_no_secret_reaches_a_log` captures every logger at DEBUG and fails on
  the device token, the key, its id, the team id or the provider token anywhere in a record.
