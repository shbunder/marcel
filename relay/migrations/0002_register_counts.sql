-- Registrations per client address per hour, and for the whole relay per day (push-relay 0.1.1).
-- `key` is 'ip:' + SHA-256 of the hour and the address (never the address itself), or 'all'.
CREATE TABLE register_counts (
  key    TEXT PRIMARY KEY,
  period TEXT NOT NULL,           -- the UTC hour (YYYY-MM-DDTHH) or day (YYYY-MM-DD) `count` is for
  count  INTEGER NOT NULL
);
