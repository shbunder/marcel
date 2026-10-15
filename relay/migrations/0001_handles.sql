-- One row per registered phone (contracts/push-relay.yaml). Nothing else is ever stored.
CREATE TABLE handles (
  handle      TEXT PRIMARY KEY,           -- 22 base64url characters
  apns_token  TEXT NOT NULL,              -- the phone's APNs device token, hex
  secret_hash TEXT NOT NULL,              -- hex SHA-256 of the handle's send_secret
  sandbox     INTEGER NOT NULL,           -- 1: APNs sandbox (a development build)
  day         TEXT NOT NULL DEFAULT '',   -- the UTC date `count` is for, YYYY-MM-DD
  count       INTEGER NOT NULL DEFAULT 0  -- pushes on `day`
);
