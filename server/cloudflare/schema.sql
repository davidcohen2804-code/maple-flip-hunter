-- Maple Flip Hunter licence database (Cloudflare D1). Same model as server/license_server.py.
CREATE TABLE IF NOT EXISTS machines (
  machine        TEXT PRIMARY KEY,
  first_seen     REAL NOT NULL,
  last_seen      REAL NOT NULL,
  version        TEXT,
  licensed_until REAL            -- NULL = trial rules, 0 = lifetime, -1 = revoked, >0 = unix expiry
);
CREATE TABLE IF NOT EXISTS codes (
  code        TEXT PRIMARY KEY,
  days        INTEGER NOT NULL,  -- 0 = lifetime
  created     REAL NOT NULL,
  redeemed_by TEXT,
  redeemed_at REAL
);
CREATE TABLE IF NOT EXISTS meta (          -- public_key, update_manifest
  k TEXT PRIMARY KEY,
  v TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS usage (         -- approximate request counter per UTC day (for the free-quota check)
  day TEXT PRIMARY KEY,
  n   INTEGER NOT NULL DEFAULT 0
);
