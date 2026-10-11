-- Requests to bring originals back from Deep Archive. Local bookkeeping only (not synced, not in the checksum).
CREATE TABLE IF NOT EXISTS restore_requests (
  photo_id     TEXT PRIMARY KEY,
  raw_key      TEXT NOT NULL,
  tier         TEXT NOT NULL,
  days         INTEGER NOT NULL,
  requested_at TEXT NOT NULL,
  status       TEXT NOT NULL,          -- in_progress | ready | expired | available
  ready_until  TEXT,
  checked_at   TEXT
);
