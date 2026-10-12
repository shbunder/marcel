# Push targets are secrets — keep them out of URLs

An ntfy topic lets anyone read the pushes; an APNs device token identifies the phone. httpx logs
every request at INFO as `HTTP Request: POST <full URL> …`, so a secret in the URL path lands in
the hub's log even when our own code never logs it.

- The ntfy backend (S-08.1) publishes JSON to the server's root, so the topic travels in the body.
- APNs puts the device token in the path (`/3/device/<token>`). S-08.2 must quiet the `httpx`
  logger for that client (or log through its own redacting event hook), and extend
  `tests/test_notify_ntfy.py::test_a_topic_is_never_logged` to the APNs token: it captures every
  logger at DEBUG and fails on the secret anywhere in a record.
