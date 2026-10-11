-- Catalog schema v1 (LOCAL_APP_PLAN.md §3). Applied by catalog.migrate(); never edit a released
-- migration — add a new numbered file instead.

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

-- One row per unique piece of content (photo or video).
CREATE TABLE IF NOT EXISTS media (
  photo_id         TEXT PRIMARY KEY,
  sha256           TEXT NOT NULL UNIQUE,
  media_type       TEXT NOT NULL CHECK (media_type IN ('photo', 'video')),
  raw_key          TEXT NOT NULL,
  compressed_key   TEXT, poster_key TEXT, thumb_key TEXT, meta_key TEXT NOT NULL,
  orig_name        TEXT, orig_ext TEXT, mime TEXT,
  bytes_raw        INTEGER, bytes_compressed INTEGER,
  width INTEGER, height INTEGER, duration_s REAL,
  taken_at         TEXT, taken_at_source TEXT, tz TEXT,
  lat REAL, lon REAL,
  place_country TEXT, place_region TEXT, place_city TEXT, place_locality TEXT, place_name TEXT,
  camera_make TEXT, camera_model TEXT,
  -- user-editable fields: changed only through ops once M2 lands (LWW per field)
  title TEXT, description TEXT, favorite INTEGER NOT NULL DEFAULT 0, hidden INTEGER NOT NULL DEFAULT 0,
  deleted INTEGER NOT NULL DEFAULT 0,
  field_hlc        TEXT NOT NULL DEFAULT '{}',
  created_hlc      TEXT NOT NULL,
  updated_hlc      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS media_taken ON media(taken_at);
CREATE INDEX IF NOT EXISTS media_place ON media(place_country, place_city);

CREATE TABLE IF NOT EXISTS media_sources (
  photo_id TEXT NOT NULL, device_id TEXT NOT NULL, source_ref TEXT NOT NULL, seen_hlc TEXT,
  PRIMARY KEY (photo_id, device_id, source_ref));
CREATE INDEX IF NOT EXISTS media_sources_device ON media_sources(device_id);

CREATE TABLE IF NOT EXISTS devices (
  device_id TEXT PRIMARY KEY, name TEXT, platform TEXT,
  first_seen_hlc TEXT, last_seen_hlc TEXT, field_hlc TEXT NOT NULL DEFAULT '{}');

CREATE TABLE IF NOT EXISTS media_tags (
  photo_id TEXT NOT NULL, tag TEXT NOT NULL, source TEXT, add_op TEXT NOT NULL,
  PRIMARY KEY (photo_id, tag, add_op));
CREATE INDEX IF NOT EXISTS media_tags_tag ON media_tags(tag);

CREATE TABLE IF NOT EXISTS media_albums (
  photo_id TEXT NOT NULL, album TEXT NOT NULL, add_op TEXT NOT NULL,
  PRIMARY KEY (photo_id, album, add_op));

CREATE TABLE IF NOT EXISTS people (
  person_id TEXT PRIMARY KEY, name TEXT, merged_into TEXT, field_hlc TEXT NOT NULL DEFAULT '{}');

CREATE TABLE IF NOT EXISTS media_people (
  photo_id TEXT NOT NULL, person_id TEXT NOT NULL, source TEXT, add_op TEXT NOT NULL,
  PRIMARY KEY (photo_id, person_id, add_op));
CREATE INDEX IF NOT EXISTS media_people_person ON media_people(person_id);

CREATE TABLE IF NOT EXISTS tombstones (add_op TEXT PRIMARY KEY, removed_by TEXT);

CREATE VIRTUAL TABLE IF NOT EXISTS media_fts USING fts5(
  photo_id UNINDEXED, title, description, tags, people, place, orig_name, albums,
  tokenize = 'unicode61 remove_diacritics 2');

-- Sync bookkeeping (filled from M2 on).
CREATE TABLE IF NOT EXISTS ops (
  op_id TEXT PRIMARY KEY, hlc TEXT NOT NULL, device_id TEXT NOT NULL, kind TEXT NOT NULL,
  entity TEXT NOT NULL, body TEXT NOT NULL, applied_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS outbox (op_id TEXT PRIMARY KEY, batch TEXT);
CREATE TABLE IF NOT EXISTS sync_cursor (device_id TEXT PRIMARY KEY, last_key TEXT);
