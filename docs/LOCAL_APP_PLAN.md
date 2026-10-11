# Local-First Catalog, Multi-Device Sync & Library App — Design

**Status:** Draft v0.2 · **Date:** 2026-10-11 · **Companion to:** [PRD.md](PRD.md), [TECH_REQUIREMENTS.md](TECH_REQUIREMENTS.md) · **Task list:** [TASKS.md](TASKS.md)

This doc covers seven things:

1. Every `photoctl archive` run also writes a **queryable SQLite catalog**.
2. A **local app** (browser UI served from the Mac) lets you search, view thumbnails, download compressed copies, request originals, and start an upload.
3. **The same website works on iPhone (Safari) and Android (Chrome).** You open it on the phone over a private Tailscale link, search, view, and **trigger archive runs on the Mac** or **upload photos from the phone**. (§9)
4. **On-device mode:** once a phone (or any device) has downloaded the SQLite file, the app runs search **locally in the browser** (SQLite compiled to WebAssembly), even when the Mac is off or there's no network. (§10)
5. **Per-device catalogs are downloadable:** every device (Mac, iPhone, Android) publishes its SQLite catalog, and any device can download one or many of them to **see and search iOS and Android data together on one device**. (§11)
6. Two or more devices can archive and edit at the same time. Their catalogs **converge without losing data**, using locks where they're needed and deterministic conflict resolution everywhere else.
7. **Nothing is lost if a device is.** The Mac's SQLite databases are backed up to S3 and can be restored on a new Mac (§12). **iPhone and Android automatically back up photos that aren't archived yet** whenever they sync (§13).

> **Supersedes** TECH_REQUIREMENTS §4.6 "index push" for multi-device use. Uploading one whole SQLite file is last-writer-wins at the *file* level, so two devices would overwrite each other. This design syncs *changes*, not files. Snapshots are still used, but only to bootstrap a new device quickly.

---

## 1. Principles

| # | Principle | Consequence |
|---|---|---|
| P1 | **S3 is the source of truth; every device's SQLite is a cache.** | Any device's DB can be deleted and rebuilt from S3 (`meta/` + `sync/`). |
| P2 | **Facts are content-addressed and immutable.** | The same photo archived from two devices gets the same `photo_id`, so it merges naturally with no conflict. |
| P3 | **Mutable things (titles, tags, people names, favourites, hide/delete) change only through an append-only operation log.** | Devices exchange operations, not database files, so concurrent edits never clobber each other. |
| P4 | **Conflict resolution is deterministic.** | Every device applies the same ops in the same order and ends up in the same state, with no "which copy wins?" prompt. |
| P5 | **Lock only where exclusive access is truly needed.** | S3 conditional writes (`If-None-Match` / `If-Match`) serve as a distributed lock for snapshot compaction and upload claims. Locally, SQLite WAL plus a file lock handles it. |
| P6 | **The app talks to an HTTP API, or to a local SQLite copy through the same query module.** It never talks to S3 directly. | One UI codebase works online (server) and offline (on-device SQLite), on desktop and phones. |
| P7 | **Mobile-first, web-first.** Every screen is designed for a phone screen first and tested on iOS Safari and Android Chrome. | No separate mobile app is needed for search, view, trigger or upload. Native wrappers are optional. |
| P8 | **Phones never hold AWS credentials.** | Phones reach S3 only through the server: presigned URLs, op proxy, upload proxy. |

---

## 2. Big picture

```
 Device A (Mac, has Apple Photos)              Device B (2nd Mac / later: phone)
 ┌──────────────────────────────┐              ┌──────────────────────────────┐
 │ photoctl CLI / App "Upload"  │              │ App (search, edit, upload)   │
 │   ingest → derive → upload   │              │                              │
 │   emit ops ─┐                │              │   emit ops ─┐                │
 │             ▼                │              │             ▼                │
 │  catalog.sqlite (WAL)        │              │  catalog.sqlite (WAL)        │
 │  ├ media, tags, people, fts  │              │  (same schema)               │
 │  ├ ops (applied log)         │              │                              │
 │  └ outbox (unpushed ops)     │              │                              │
 │  local API :8765 ◄── UI      │              │  local API ◄── UI            │
 └──────────┬──────▲────────────┘              └──────────┬───────────────────┘
            │      │ HTTPS over Tailscale (tailnet only)   │
            │      │                                      │
            │  ┌───┴──────────────────────────────────┐   │
            │  │ iPhone (Safari) / Android (Chrome)   │   │
            │  │ installable PWA, same UI             │   │
            │  │  online : search/view/trigger/upload │   │
            │  │  offline: catalog.sqlite in OPFS     │   │
            │  │           (sqlite-wasm), cached      │   │
            │  │           thumbs, outbox of edits    │   │
            │  └──────────────────────────────────────┘   │
            │ push outbox / pull others' ops  (sync loop)  │
            ▼                                              ▼
 ┌──────────────────────────── S3 bucket ──────────────────────────────────────┐
 │ raw/ compressed/ thumb/ meta/          (immutable, content-addressed)       │
 │ sync/ops/<device_id>/<hlc>.jsonl.gz    (append-only op batches, per device) │
 │ sync/snapshots/<hlc>.sqlite.gz         (compacted catalog for bootstrap)    │
 │ sync/HEAD.json                         (points at latest snapshot; CAS)     │
 │ sync/locks/<name>.json                 (leases via conditional writes)      │
 │ sync/devices/<device_id>.json          (device registry, last seen)         │
 │ sync/catalogs/<device_id>/catalog.sqlite.gz + manifest.json                 │
 │ backups/<device_id>/<timestamp>/   catalog + state DBs, device.json (§12)   │
 │                                        (each device's downloadable catalog) │
 └─────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Data model (`~/.photoctl/catalog.sqlite`)

The catalog is separate from `state.sqlite`. **`state.sqlite` stays device-local**: it maps "Apple Photos UUID on *this* Mac → upload status" and is never synced. **`catalog.sqlite` is the synced, queryable library.**

```sql
PRAGMA journal_mode = WAL;          -- readers never block the writer
PRAGMA busy_timeout = 5000;         -- CLI and app on the same device wait instead of failing

-- One row per unique piece of content (photo or video).
CREATE TABLE media (
  photo_id      TEXT PRIMARY KEY,           -- base32(sha256)[:26]
  sha256        TEXT NOT NULL UNIQUE,
  media_type    TEXT NOT NULL,              -- photo | video
  raw_key       TEXT NOT NULL,
  compressed_key TEXT, poster_key TEXT, thumb_key TEXT, meta_key TEXT NOT NULL,
  orig_name     TEXT, orig_ext TEXT, mime TEXT,
  bytes_raw     INTEGER, bytes_compressed INTEGER,
  width INTEGER, height INTEGER, duration_s REAL,
  taken_at      TEXT, taken_at_source TEXT, tz TEXT,       -- ISO 8601
  lat REAL, lon REAL,
  place_country TEXT, place_region TEXT, place_city TEXT, place_locality TEXT, place_name TEXT,
  camera_make TEXT, camera_model TEXT,
  -- user-editable fields (changed only via ops, LWW per field; see §5)
  title TEXT, description TEXT, favorite INTEGER DEFAULT 0, hidden INTEGER DEFAULT 0,
  deleted INTEGER DEFAULT 0,                -- tombstone; originals are never deleted by the app
  field_hlc     TEXT NOT NULL DEFAULT '{}', -- JSON {field: hlc} — last-writer metadata per field
  created_hlc   TEXT NOT NULL,
  updated_hlc   TEXT NOT NULL
);
CREATE INDEX media_taken ON media(taken_at);
CREATE INDEX media_place ON media(place_country, place_city);

-- Where each item came from (the same content can come from many devices or libraries).
CREATE TABLE media_sources (
  photo_id TEXT, device_id TEXT, source_ref TEXT,   -- 'applephotos:<uuid>' | 'folder:/path' | 'upload:<name>'
  seen_hlc TEXT, PRIMARY KEY (photo_id, device_id, source_ref));

-- Known devices (synced via `device.register` ops) so the UI can filter by device or platform.
CREATE TABLE devices (
  device_id TEXT PRIMARY KEY, name TEXT,            -- 'My MacBook', 'My iPhone', 'Pixel 8'
  platform TEXT,                                     -- macos | ios | android | web
  first_seen_hlc TEXT, last_seen_hlc TEXT, field_hlc TEXT DEFAULT '{}');

-- Tags, albums and people are sets → observed-remove sets (add-wins); see §5.
CREATE TABLE media_tags   (photo_id TEXT, tag TEXT, source TEXT, add_op TEXT, PRIMARY KEY (photo_id, tag, add_op));
CREATE TABLE media_albums (photo_id TEXT, album TEXT, add_op TEXT, PRIMARY KEY (photo_id, album, add_op));
CREATE TABLE people       (person_id TEXT PRIMARY KEY, name TEXT, merged_into TEXT, field_hlc TEXT DEFAULT '{}');
CREATE TABLE media_people (photo_id TEXT, person_id TEXT, source TEXT, add_op TEXT,
                           PRIMARY KEY (photo_id, person_id, add_op));
CREATE TABLE tombstones   (add_op TEXT PRIMARY KEY, removed_by TEXT);  -- which adds were removed

-- Full-text search across everything a person might type.
CREATE VIRTUAL TABLE media_fts USING fts5(
  photo_id UNINDEXED, title, description, tags, people, place, orig_name, albums,
  tokenize = 'unicode61 remove_diacritics 2');

-- Sync bookkeeping.
CREATE TABLE ops (                         -- every op ever applied (idempotency + audit)
  op_id TEXT PRIMARY KEY,                  -- '<hlc>:<device_id>:<seq>' — globally unique and sortable
  hlc TEXT NOT NULL, device_id TEXT NOT NULL, kind TEXT NOT NULL, entity TEXT NOT NULL,
  body TEXT NOT NULL, applied_at TEXT NOT NULL);
CREATE TABLE outbox (op_id TEXT PRIMARY KEY, batch TEXT);   -- local ops not yet pushed to S3
CREATE TABLE sync_cursor (device_id TEXT PRIMARY KEY, last_key TEXT);  -- last S3 batch pulled per remote device
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);       -- schema_version, device_id, snapshot_hlc, hlc_last
```

**Thumbnails:** a new `thumb/<part>/<photo_id>.jpg` (320px, about 20–40 KB) is produced at ingest, for both photos and video posters. Thumbs are stored locally in `library/thumb/` and in S3 Standard.

---

## 4. Operations (the only way the catalog changes)

Each op is one JSON line. Ops are batched into `sync/ops/<device_id>/<first_hlc>.jsonl.gz`.

| kind | body | produced by |
|---|---|---|
| `media.upsert` | full immutable facts + initial Apple metadata (the sidecar record) | `archive` after a verified upload |
| `media.source` | `{photo_id, device_id, source_ref}` | `archive` (the same content seen on another device or library) |
| `media.set` | `{photo_id, field, value}` with field ∈ title, description, favorite, hidden | app edits |
| `media.delete` / `media.restore` | `{photo_id}` | app ("remove from library"; the S3 original is kept) |
| `tag.add` / `tag.remove` | add: `{photo_id, tag}`; remove: `{photo_id, tag, observed_add_ops: [...]}` | app, enrichment |
| `album.add` / `album.remove` | same shape as tags | app, Apple import |
| `person.create` / `person.set` / `person.merge` | `{person_id, name}` / `{person_id, field, value}` / `{from, into}` | app, faces (phase 2) |
| `media.person.add` / `.remove` | `{photo_id, person_id}` (+ `observed_add_ops`) | Apple import, faces, app |
| `device.register` / `device.set` | `{device_id, name, platform}` / `{device_id, field, value}` | first run on any device (Mac CLI, phone browser) |

**HLC (hybrid logical clock):** `"<wall_ms:013d>-<counter:04d>-<device_id>"`. Each new op gets `max(now_ms, last_hlc.wall)`, and the counter increments on ties. When a device pulls ops it advances its clock past every HLC it has seen. Plain string sort then gives a total order that is consistent with causality, even if device clocks drift by minutes. `device_id` is the final tie-breaker, so no two ops ever compare equal.

---

## 5. Conflict resolution rules (deterministic, no user prompts)

| Situation | Rule | Result |
|---|---|---|
| Same photo archived on two devices at once | `photo_id` = content hash, so both `media.upsert` ops are for the same row. Immutable facts are identical. For Apple-derived editable fields, the higher HLC wins. | One row with two `media_sources` rows. |
| Both devices upload the same original to S3 | Claim with `PutObject(..., IfNoneMatch='*')` on `raw/…`. The loser gets `412 PreconditionFailed`, treats it as "already archived", verifies size and sha256, and continues. | Uploaded once; no wasted Deep Archive charges. |
| Both edit the same field (e.g. title) | **Last-writer-wins per field** by HLC. `field_hlc[field]` stores the winning HLC, and an incoming op is applied only if its HLC is greater. | Converges; the edit made later in HLC order wins. |
| A edits title, B edits favourite | Different fields, so both survive (LWW is per field, not per row). | No loss. |
| A adds tag "beach", B removes "beach" concurrently | **Add-wins observed-remove set**: a remove only cancels the specific add ops it had seen. | Tag stays (B never saw A's add). |
| A renames person P to "Yahvi", B to "Yahvi S" | LWW on `people.name`. | One name, the same on every device. |
| A merges P1 into P2 while B tags a photo with P1 | `person.merge` sets `merged_into`; reads resolve P1 → P2. Ops arriving later that reference P1 are rewritten on apply. | The photo ends up tagged with P2. |
| A deletes a photo, B edits it | The edit is applied but the row stays `deleted=1` until an explicit `media.restore` with a higher HLC. | The delete holds; nothing is lost (it can be restored). |
| The same op is pulled twice | `ops.op_id` primary key, so apply is idempotent. | No duplicates. |
| Ops arrive out of order | Apply is commutative because of the per-field HLC checks and OR-sets. A full replay sorted by HLC gives the same state. | Same final state on every device. |

**Fallback:** `photoctl catalog rebuild` replays `snapshot + all ops` from S3 into a fresh DB. If two devices ever disagree, run `photoctl sync verify`, which compares a deterministic checksum over sorted rows.

---

## 6. Locking

### 6.1 On one device (CLI and app on the same Mac)
- **SQLite WAL** with `busy_timeout=5000`: many readers and one writer. All writes go through `catalog.apply_ops()` in short transactions.
- **Ingest lock:** `~/.photoctl/ingest.lock` held with `fcntl.flock(LOCK_EX | LOCK_NB)` for the length of an archive run. If you start a second run from the CLI or the app's Upload button, it fails fast with "an upload is already running (pid N, started HH:MM)". The app shows live progress by tailing `~/.photoctl/runs/<run_id>.jsonl`.
- **Sync lock:** one sync loop per device, using the same `flock` pattern on `sync.lock`.

### 6.2 Across devices (S3 conditional writes; no DynamoDB needed)
S3 supports `If-None-Match: *` (create-if-absent) and `If-Match: <etag>` (compare-and-swap) on `PutObject`.

| What | Mechanism |
|---|---|
| Push op batch | Key is unique per device (`sync/ops/<device_id>/<hlc>.jsonl.gz`), so devices never contend. Written with `If-None-Match` so a retry can't overwrite. |
| Claim an original upload | `If-None-Match: *` on the `raw/` key (§5). |
| **Compaction lease** (only one device builds a snapshot) | `sync/locks/compact.json` = `{device_id, expires_at}`. Acquire with `If-None-Match: *`. If the lease has expired, take it over with `If-Match: <etag>`. Renew every 60 s; the lease lasts 5 min. |
| Publish snapshot | Upload `sync/snapshots/<hlc>.sqlite.gz`, then update `sync/HEAD.json` with `If-Match: <old etag>`. On 412, re-read and retry. |
| Device registry | `sync/devices/<device_id>.json` is written only by its own device; there is no contention. |
| Per-device catalog publish (§11) | `sync/catalogs/<device_id>/` is written only by (or on behalf of) its own device. The manifest is replaced with `If-Match` so a slow older publish can't overwrite a newer one. |
| Phone ops via the server | The server writes `sync/ops/<phone_device_id>/…` on the phone's behalf, keeping the phone's `device_id` and HLCs; the batch key comes from the phone's `op_id`s, so a retried request is idempotent. |

**Bucket config changes (Terraform):** versioning is already on, which conditional writes need for safe CAS. Add a lifecycle rule that deletes `sync/ops/` batches older than the newest snapshot + 90 days, and keeps the last 5 snapshots. Add `sync/*` read/write to the ingest IAM policy. Deletes on `sync/` only go to an admin/compactor role.

---

## 7. Sync loop

```
every 60 s, on app start, after each archive run, or on "Sync now":
  1. PUSH:  batch the outbox → gzip JSONL → PutObject(If-None-Match) → delete from outbox
  2. PULL:  for each device prefix under sync/ops/: ListObjectsV2(StartAfter=cursor) → GET new batches
            → sort all new ops by HLC → apply_ops() in one transaction → advance cursors
  3. BOOTSTRAP (new or empty device): GET sync/HEAD.json → download the snapshot → then PULL from its cursors
  4. COMPACT (optional, holder of the compact lease only, when > 5k ops since the last snapshot)
```

Cost at family scale: a few hundred LIST/GET per day, well under $0.01/month.

**Apply pipeline (`apply_ops`):** sort by HLC → skip ops whose `op_id` is already in `ops` → dispatch on `kind` → update the row with per-field HLC checks → refresh the `media_fts` row → insert into `ops`. It is idempotent and commutative, which makes it the most test-critical function in the system.

---

## 8. App (served by the Mac)

**Shape:** `photoctl serve` runs a FastAPI server bound to `127.0.0.1:8765` and serves a single-page UI (Vite + React + TypeScript, built into `photoctl/web/dist`). The UI talks only to the JSON API when online, and to an on-device SQLite copy when offline (P6). Phones reach the server through Tailscale (§9).

### API (v1)
| Method | Path | Purpose |
|---|---|---|
| GET | `/api/search?q=&from=&to=&person=&place=&type=&fav=&device=&cursor=` | Filters + FTS, newest first, keyset pagination; `device` = items contributed by that device/platform |
| GET | `/api/media/{id}` | Full record (catalog row + sidecar) |
| GET | `/api/media/{id}/thumb` | Local file, or redirect to a presigned S3 `thumb/` URL |
| GET | `/api/media/{id}/compressed` | Download the compressed JPEG/MP4 (local or presigned, ≤ 15 min) |
| POST | `/api/media/{id}/original` | Start a Deep Archive restore; `GET` shows status, then a presigned link when ready |
| PATCH | `/api/media/{id}` | title / description / favorite / hidden → `media.set` ops |
| POST/DELETE | `/api/media/{id}/tags` | tag ops |
| GET/PATCH | `/api/people`, `/api/people/{id}` | names, merges |
| POST | `/api/uploads/apple-photos` | **Trigger** an archive run on the Mac (selection / album / date range / all; dry-run). Holds the ingest lock. Callable from a phone. |
| POST/PATCH/HEAD | `/api/uploads/files` | **Resumable chunked upload** from any browser, including phones (tus protocol) → inbox → ingest |
| GET | `/api/runs`, `/api/runs/{id}`, `/api/runs/{id}/events` | Run history; live progress (SSE) |
| POST/DELETE | `/api/runs/{id}/cancel` | Cancel a running archive cleanly (finishes the current item) |
| POST | `/api/devices/register` | A phone/browser registers `{device_id, name, platform}` → `device.register` op |
| POST | `/api/sync` · GET `/api/sync/status` | Sync now; last push/pull, pending outbox, devices |
| POST | `/api/sync/ops` | Clients without AWS creds (phones) push their op batches; the server writes them to `sync/ops/<their device_id>/` |
| GET | `/api/sync/ops?since=<cursor>` | Clients pull new ops incrementally (for on-device catalogs) |
| GET | `/api/catalogs` | List every device's published catalog + manifest (§11) |
| GET | `/api/catalogs/library` | Download the current merged catalog (`.sqlite.gz`) |
| GET | `/api/catalogs/{device_id}?flavour=full\|contributed` | Download one device's catalog |
| GET | `/api/catalogs/all.zip` | Download every device's catalog in one go |
| PUT | `/api/catalogs/{device_id}` | A phone publishes its on-device catalog snapshot (server writes it to S3) |
| GET | `/api/offline/thumbs?year=&album=` | Zip of thumbnails for offline use (§10) |
| POST | `/api/backup/check` | Phones send up to 500 SHA-256 hashes; the server answers which are not archived yet (§13.4) |
| GET | `/api/backup/status?device=` | Per-phone backup progress: backed up, waiting, failed |
| POST | `/api/db/backup` · GET `/api/db/backups` | Back up the Mac's databases now; list backups with age and size (§12) |

### Screens (mobile-first; every screen works one-handed on a phone)
1. **Library grid**: thumbnails by month, infinite scroll, a search box and filter chips (date, person, place, type, favourite, **source device**).
2. **Viewer**: compressed image or video player; swipe left/right; metadata panel (date, map pin, place, camera, people, tags, albums, source devices); edit title/tags/favourite; "Download compressed"; "Request original" (shows "ready in about 12 h" for Deep Archive).
3. **Uploads**: "Archive from Apple Photos **on <Mac name>**" (album / date range / all / what's selected on the Mac, + dry-run) → live progress, cancel; "Upload from this device" (photo picker, resumable); run history with failures and "retry failed".
4. **People**: list, rename, merge.
5. **Catalogs & offline**: the list of devices with platform icons; each device's catalog (size, item count, age), download/open toggles; "Use on this device" (offline mode); "Download thumbnails for offline"; storage used; "Remove downloaded data".
6. **Sync & devices**: status, last sync, devices, "Sync now", converged indicator via checksum.
7. **Status bar** (all screens): Online via <Mac> / **Offline – on-device catalog from <time>** / pending edits count.

### Security
- The server binds to localhost. Remote access goes **only through Tailscale** (`tailscale serve`), and no port is open to the internet.
- `tailscale serve` adds `Tailscale-User-Login` headers. The server accepts only requests proxied from 127.0.0.1 that carry a login on the allowlist in `~/.photoctl/config.toml`. A direct localhost browser uses the per-install token cookie.
- The server holds AWS credentials. Browsers and phones only ever see presigned URLs (≤ 15 min) (P8).
- Catalogs contain GPS and people names. They are downloadable only by authenticated users, and the UI says so before download. On phones they live in the browser's origin-private storage (OPFS), protected by device encryption, and "Remove downloaded data" wipes them.

---

## 9. Phones (iOS + Android) — open the website and trigger runs

**Goal:** on an iPhone or Android phone, open the website, search and view, **start an archive run on the Mac**, or **upload photos from the phone**, using the same UI as the desktop.

### 9.1 Access
- Install **Tailscale** on the Mac and on each phone, signed in to the same tailnet (free for personal use; iOS + Android apps).
- On the Mac: `tailscale serve --bg 8765` publishes `https://<mac-name>.<tailnet>.ts.net` with a valid HTTPS certificate. HTTPS is required because service workers, OPFS and "Add to Home Screen" only work in a secure context.
- `photoctl serve --tailscale` checks this setup and prints the phone URL plus a QR code.
- **Install as an app (PWA):**
  - iOS: Safari → Share → Add to Home Screen.
  - Android: Chrome → Install app.
  - Installed PWAs get their own icon, full-screen display, persistent storage (important on iOS, §10.3) and web push (iOS 16.4+).

### 9.2 Trigger an archive run on the Mac from the phone
- Uploads screen → "Archive from Apple Photos on <Mac>" → choose album / date range / all / "what's currently selected on the Mac" → optional dry-run → Start.
- `POST /api/uploads/apple-photos` starts a background run on the Mac, which takes the same ingest lock as the CLI (§6.1). If a run is already going, the phone shows its live progress instead of starting a second one.
- Progress streams over SSE. The phone can lock, come back and reattach. An optional web push says "Archive finished: 312 done, 2 failed".
- Cancel stops cleanly after the current item; a re-run resumes.

### 9.3 Upload photos from the phone
- `<input type="file" accept="image/*,video/*" multiple>` opens the native photo picker on both platforms.
- **Resumable chunked uploads** (tus protocol: `tus-js-client` in the browser, a tus endpoint in FastAPI) survive network drops and app switches. Large videos are sent in 8 MB chunks.
- The server stages files in `~/.photoctl/inbox/<phone_device_id>/` and runs the same pipeline (hash → derive → S3 → catalog). `source_ref = upload:<phone_device_id>:<original name>`, and `media_sources.device_id` = the phone, so "photos from my iPhone" works.
- **Metadata caveats in browsers** (shown in the UI after each upload, recorded as `metadata_fidelity` in the record):
  - **iOS Safari:** the photo picker may convert HEIC → JPEG and **remove location** unless "Options → Location" is turned on in the picker. Picking from the **Files** app keeps the originals.
  - **Android Chrome:** the system photo picker can **redact GPS** from shared files.
  - When GPS or capture date is missing, the item is flagged "metadata incomplete". The fix is either archiving that photo from the Mac's Photos library (if it syncs via iCloud) or the phone's automatic backup in the native app (§13).
- When the Mac is off, uploads queue in the PWA (IndexedDB) and resume when the Mac is reachable again.

### 9.4 Device identity for phones
On first open, the PWA generates a `device_id` (ULID) and keeps it in OPFS/IndexedDB. It asks for a name ("My iPhone"), detects the platform (ios/android), and calls `POST /api/devices/register`. Ops created on the phone (edits, uploads) carry that `device_id` and their own HLC, exactly like a Mac.

---

## 10. On-device mode — search without the server

**Goal:** once a device has the SQLite file, it can search and browse **on the device itself**, offline or with the Mac switched off, on iOS and Android, without a native app.

### 10.1 Engine
- **`@sqlite.org/sqlite-wasm`** (the official SQLite WebAssembly build) running in a Web Worker.
- Storage: the **`opfs-sahpool` VFS** in the Origin Private File System. It works on iOS Safari 16.4+ and Android Chrome and, unlike the plain OPFS VFS, does **not** need COOP/COEP headers or SharedArrayBuffer.
- FTS5 is included in the official build, so the same `media_fts` queries work unchanged.

### 10.2 Getting the catalog onto the device
- "Use on this device" downloads `/api/catalogs/library` (`.sqlite.gz`, about 30–80 MB for 50k items without embeddings), decompresses it in the browser with `DecompressionStream('gzip')`, and imports it into OPFS. The download is resumable via Range requests.
- Updates use incremental `GET /api/sync/ops?since=<cursor>` → the TypeScript `apply_ops` (the same rules as Python, proven by the shared conformance vectors). If the device is too far behind (more than N ops), it re-downloads the snapshot.
- **Edits offline** go to the on-device `outbox` and are pushed via `POST /api/sync/ops` when the server is reachable. Conflicts resolve by §5, so nothing extra is needed on the phone.

### 10.3 Storage limits and eviction
- The app calls `navigator.storage.persist()`. On **iOS, data written by a website can be evicted after 7 days without use unless the site is installed to the Home Screen**, so the UI asks iOS users to install before enabling on-device mode.
- The Catalogs screen shows quota and usage (`navigator.storage.estimate()`).

### 10.4 Images while offline
- Thumbnails are cached by the service worker as you scroll. **Offline packs** pre-download them by year or album (`/api/offline/thumbs`, about 30 KB each: 1k photos ≈ 30 MB).
- Compressed copies are available offline only when marked "Save offline" (per item or album).
- Originals always need the server (Deep Archive restore).

### 10.5 One query layer, two backends
- All search SQL lives in **`queries/*.sql`** files with named parameters, used by both the Python server and the TypeScript client, so online and offline results are identical.
- The UI calls a `DataSource` interface with two implementations: `ServerDataSource` (HTTP) and `LocalDataSource` (sqlite-wasm).
- It picks automatically: server if reachable, otherwise local, and the status bar shows which one is in use.

---

## 11. Per-device catalogs — download any device's SQLite, search them together

**Goal:** every device's SQLite is downloadable, so you can **see and search iOS and Android (and Mac) data together on one device**.

### 11.1 What gets published
Each device publishes to `sync/catalogs/<device_id>/` after each sync (if changed, at most every 10 min):
- `catalog.sqlite.gz`: a consistent snapshot taken with the SQLite backup API, then VACUUM'd.
- `manifest.json`: `{device_id, name, platform, schema_version, generated_hlc, checksum, item_count, contributed_count, bytes}`.
- Macs upload directly. Phones upload through `PUT /api/catalogs/{device_id}`, which the server writes to S3.

### 11.2 Two flavours per device
| Flavour | Contents | Use |
|---|---|---|
| `full` | That device's whole local catalog (after sync, the same as the library; it differs only when the device is behind) | Inspect or debug a device, check convergence, offline copy |
| `contributed` | Only media whose `media_sources` include that device ("photos that came from my iPhone"), with the same schema | Search one device's photos on its own, or share "just the Android photos" |

The **library** catalog (`/api/catalogs/library`) is the merged view of everything and is the normal choice.

### 11.3 Searching several catalogs on one device
- On-device: sqlite-wasm opens the selected files and `ATTACH`es them (up to 10). Search runs the same `queries/*.sql` per attached DB with `UNION ALL`, then **dedups by `photo_id`**. A photo present in both the iPhone and Android catalogs shows once, with both device badges.
- On the server and CLI: `photoctl catalog download --device <id|all> --flavour contributed -o dir/` and `photoctl search "beach" --catalog iphone.sqlite --catalog pixel.sqlite`.
- UI: the Catalogs screen has a toggle per device catalog ("Pixel 8 · Android · 4,210 items · 2 h old") plus "Download all" (`/api/catalogs/all.zip`). The grid's "Source" chip filters by device or platform.

### 11.4 Freshness
Downloaded catalogs are point-in-time snapshots. Every result card and catalog row shows its age. "Refresh" pulls ops since the snapshot (§10.2) or re-downloads the snapshot.

---

## 12. Database backup & recovery — lose the Mac, lose nothing

**Goal:** the SQLite databases are backed up to S3 automatically. If the Mac dies, gets wiped, or you move to a new one, you can recover and carry on archiving from where you left off.

### 12.1 What gets backed up

| File | Why it matters | Can it be rebuilt without a backup? |
|---|---|---|
| `~/.photoctl/catalog.sqlite` | The searchable library: edits, tags, people names, op log | Yes, from `sync/snapshots` + `sync/ops` (§7) or from `meta/` JSON. A backup is just faster. |
| `~/.photoctl/state.sqlite` | Which Apple Photos items this Mac already archived, plus failures to retry | Yes, from `meta/` JSON (each sidecar has the Apple UUID and sha256), but slowly |
| `~/.photoctl/device.json` | This Mac's `device_id` and name. Restoring it lets the new Mac **carry on as the same device** instead of appearing as a stranger | No. **Back it up.** |
| `~/.photoctl/config.toml` | Settings, Tailscale login allowlist (no secrets) | No. **Back it up.** |

AWS credentials and the API token are **never** backed up; you log in again on a new Mac.

### 12.2 How backups are made
- `photoctl db backup` takes a consistent copy of each database while it's in use, via SQLite's online backup API (`sqlite3.Connection.backup`). It then runs `PRAGMA integrity_check` on the copy, gzips it, and uploads it.
- **Layout:**
  ```
  backups/<device_id>/<YYYY-MM-DDTHH-MM>/catalog.sqlite.gz
  backups/<device_id>/<YYYY-MM-DDTHH-MM>/state.sqlite.gz
  backups/<device_id>/<YYYY-MM-DDTHH-MM>/device.json, config.toml
  backups/<device_id>/<YYYY-MM-DDTHH-MM>/manifest.json   {sha256 per file, schema_version, last_pushed_op, photoctl version}
  backups/<device_id>/LATEST.json                          (pointer, replaced with If-Match)
  ```
- **When:** automatically at the end of every archive run, plus daily via a macOS LaunchAgent (`photoctl db schedule install`). Also available as a button in the app (Sync & devices → "Back up databases now").
- **Retention (S3 lifecycle):** daily backups kept 30 days; the first backup of each month is copied to `backups/<device_id>/monthly/` and kept 12 months. At 50k photos, a gzipped catalog is roughly 20–30 MB, so this costs well under $0.05/month.
- **Safety:** same private, encrypted, versioned bucket. Backups contain GPS and people names, so the ingest policy may write `backups/*` but only the admin role may delete there.
- **Monitoring:** `photoctl status` and the app's Sync screen show "Last backup: 3 h ago ✓". It turns amber after 48 h without a backup.

### 12.3 Recovery ladder (fastest first)

| Situation | Command | What happens | Time |
|---|---|---|---|
| Database corrupted or deleted, Mac fine | `photoctl db restore` | Download LATEST → verify sha256 + integrity → put files back → `photoctl sync` pulls every op made since the backup | Minutes |
| New Mac / Mac wiped | `photoctl recover` | Pick your old device from a list (read from `sync/devices/`) → restore `device.json`, config and both DBs → re-adopt the same `device_id` → sync → re-scan Apple Photos (already-archived items are skipped) | Minutes |
| Restore an older point in time | `photoctl db restore --at 2026-10-01` | Same as above, from a dated backup; sync then re-applies later ops | Minutes |
| No usable backup | `photoctl catalog rebuild --from-s3` + `photoctl state rebuild --from-s3` | Rebuild from snapshot + ops and from `meta/` JSON | ~10–30 min at 50k |

**Making "same device_id" safe:** before a restored device writes anything, it pulls **its own** published ops from `sync/ops/<device_id>/` and advances its HLC past the highest one. That means it never reuses an op id. Local edits that were made after the last push and before the crash are the only thing that can be lost. Pushes happen every 60 s, so that window is small.

**Phones:** a phone's catalog needs no backup. It re-downloads (§10.2). Its backup ledger (§13.4) is rebuilt by re-hashing the camera roll and asking the server what it already has.

---

## 13. Automatic backup from iPhone and Android

**Goal:** photos and videos taken on a phone that aren't archived yet are **found and backed up automatically when the phone syncs**. No picking files by hand, and nothing uploaded twice.

### 13.1 Why this needs the native app
A website can't read the camera roll by itself or run in the background. It only sees files you pick. So:
- **Native app (Capacitor wrapper, iOS + Android):** real automatic backup. This becomes a **required** milestone (M12) instead of optional.
- **Web app fallback (PWA):** an assisted "Back up recent photos" button. You select photos, and the app hashes them on the phone and uploads only the ones the server doesn't have (§13.5).

### 13.2 When backup runs
Backup is part of sync. Each sync does pull ops → push ops → **back up new camera-roll items** → publish catalog. It is triggered by:
- opening the app, and pulling to refresh
- **iOS:** `BGProcessingTask` (when charging and on Wi-Fi, scheduled by the system, usually overnight) + `BGAppRefreshTask` for a quick check. Uploads use a **background `URLSession`**, which keeps transferring after the app is suspended.
- **Android:** `WorkManager` periodic work (every 6 h, with constraints), plus a content-URI trigger (`JobInfo.TriggerContentUri` on MediaStore) so a new photo starts a backup within minutes when the constraints are met.

Settings (per phone): Wi-Fi only (default on), only while charging (default on for videos), include videos, include screenshots (default off), albums/folders to include, back up from date (default: everything).

### 13.3 Finding what's new
| | iOS | Android |
|---|---|---|
| Library API | **PhotoKit** (`PHAsset`) | **MediaStore** (`MediaStore.Images` / `.Video`) |
| "What changed since last time" | `PHPhotoLibrary.fetchPersistentChanges(since: token)` (iOS 16+), so it doesn't rescan the whole library | `MediaStore.getGeneration()` + query `GENERATION_ADDED > last` (Android 11+); `DATE_ADDED` fallback |
| Original bytes + metadata | `PHAssetResourceManager` → original HEIC/JPEG/RAW/MOV with EXIF + GPS; Live Photo paired video | `ACCESS_MEDIA_LOCATION` + `MediaStore.setRequireOriginal(uri)` so GPS is **not** redacted |
| Permissions | Full photo library access. With "Limited" access, only the allowed photos are backed up, and the app says so. | `READ_MEDIA_IMAGES/VIDEO` (13+). Android 14 "selected photos only" is shown as a warning, like iOS Limited. |
| iCloud-only / cloud items | `isNetworkAccessAllowed = true` downloads the original first (Wi-Fi only by default) | Local files only; Google Photos cloud-only items are out of scope |

### 13.4 Never uploading the same photo twice
1. For each new asset, the phone computes **SHA-256 on the device** while streaming the original. That gives the same `photo_id` the Mac would compute (§3).
2. `POST /api/backup/check` with a batch of hashes (up to 500) → the server answers which are **missing**. Already archived means skipped, including photos that reached S3 through the Mac's Apple Photos (iCloud Photos sync).
3. Missing items upload as originals via **resumable upload** (tus to the Mac), or via presigned multipart PUT straight to S3 once M13 (always-on API) exists.
4. After a verified upload, the server emits `media.upsert` + `media.source` with the phone's `device_id` and `source_ref = ios:<localIdentifier>` or `android:<MediaStore id>`. Same-content uploads from two phones merge as usual (§5). The raw upload uses `If-None-Match` (§6.2), so concurrent backups can't double-upload.
5. Compressed copy / poster / thumb: the Mac makes them as soon as it receives the original (the same pipeline as Apple Photos). With the M13 always-on API, the phone uploads directly with `needs_derivatives=true`, and the next Mac run fills them in.

**On-phone backup ledger** (SQLite in the app, the phone's version of `state.sqlite`): `assets(local_id PK, sha256, photo_id, size, modified_at, status: pending|hashed|uploading|done|skipped_dup|failed, error, attempts)` + `cursor(change_token | media_generation)`. Edited photos (new modification date) are re-hashed. If the bytes changed, the edit is backed up as a new item linked to the same `local_id`.

### 13.5 Web app (PWA) assisted backup
- "Back up recent photos" → photo picker (multi-select) → the browser hashes each file with WebCrypto → `POST /api/backup/check` → uploads only the missing ones.
- The browser metadata limits from §9.3 apply (GPS may be stripped), so these items are flagged "metadata incomplete", and the native app is recommended in the UI.

### 13.6 What the user sees
- Sync screen: "Backed up 1,284 of 1,290 · 6 waiting for Wi-Fi", the last backup time, and failures with retry.
- Each photo in the grid can show a small "on this phone only" badge until it's backed up.
- **Free up space** (optional, off by default): after a backup is verified (S3 checksum + catalog row), the app can offer to delete those items from the phone. It uses the OS delete confirmation (iOS `PHAssetChangeRequest.deleteAssets`, Android `MediaStore.createDeleteRequest`), so the system always asks you first.

### 13.7 When the Mac is off
Without an always-on API (M13), phones queue backups and upload when the Mac is reachable on the tailnet. With M13, the phone gets presigned URLs from the hosted API, uploads straight to S3, and the Mac fills in the compressed copies later.

---

## 14. Native Android & iOS apps — beyond auto-backup

The native app (Capacitor wrapper around the same UI) first exists for automatic backup (§13, milestone M12). Once it's there, it also brings:

| Need | Why native |
|---|---|
| Full-fidelity uploads of anything you share to the app | Share sheet / intent target that receives originals with GPS intact |
| Bigger, never-evicted offline storage | Native SQLite (`@capacitor-community/sqlite`) behind the same `LocalDataSource` interface |
| Notifications without installing the PWA | Native push for "run finished", "original ready", "backup needs attention" |
| Store distribution | TestFlight / Play internal testing for family members |

Phones still never hold AWS keys (P8).

**Always-on access when the Mac is off** (search online, backups straight to S3) means moving the API to Lambda (TECH §5.4, milestone M13). Until then, phones fall back to on-device mode (§10) and queue backups and uploads.

---

## 15. Testing strategy

- **Property tests (Hypothesis) for `apply_ops`:** random op sequences from 2–3 simulated devices (including phones), applied in random interleavings, must give identical catalog checksums.
- **Conformance vectors:** `spec/sync_vectors/*.json` (ops in → expected rows out), run by **both** the Python and the TypeScript `apply_ops` in CI.
- **Query parity:** the same `queries/*.sql` run through Python sqlite3 and sqlite-wasm (Node) on a fixture catalog must return identical ids.
- **Concurrency tests:** two processes archiving overlapping photo sets against a test bucket. Exactly one raw upload per sha, and both catalogs converge after sync.
- **Lock tests:** a second `archive` (from the CLI or a phone trigger) gets "already running"; an expired compaction lease gets taken over.
- **API tests:** FastAPI TestClient for search, edit → op emitted, presign expiry, tus resume, catalog download flavours, Tailscale header auth.
- **UI:** Playwright with iPhone and Pixel device emulation (search → open → download; trigger run; offline mode with network disabled).
- **Backup & recovery:** back up while an archive run is writing (the copy must pass `integrity_check`); restore onto a clean user account and onto a "new Mac" VM, then confirm the catalog checksum matches the other devices and a re-run of `archive` uploads zero bytes; restore an older backup and confirm sync re-applies later ops; confirm a restored device never reuses an op id.
- **Phone auto-backup:** a fixture camera roll (iOS simulator / Android emulator) with new, edited, duplicate, iCloud-only, Live Photo and large video items → only missing items upload; killing the app mid-upload resumes; Limited/selected-photos permission is reported; GPS survives on both platforms.
- **Real devices** (manual checklist per release): iPhone Safari + installed PWA, Android Chrome + installed PWA. Covers install, trigger, upload with location on/off, offline search, multi-catalog search.

---

## 16. Decisions to confirm

1. **UI stack:** React + Vite + TypeScript (recommended: best PWA, sqlite-wasm and Capacitor path). Alternative: server-rendered HTMX, which can't do on-device mode, so it's not recommended now.
2. **Remote access:** Tailscale (recommended: free, private, real HTTPS, works on iOS and Android) vs Cloudflare Tunnel + Access.
3. **Phone upload fidelity:** accept browser limits for now (recommended; flag incomplete metadata), or rely on the native app's automatic backup (§13) for full metadata.
4. **Offline images:** thumbnails only by default + opt-in "Save offline" for compressed copies (recommended).
5. **Op batch flush policy:** at the end of each archive run and every 60 s for edits (recommended).
6. **Deletes:** "remove from library" only hides it (tombstone, recommended). A true purge of S3 originals stays a separate admin-only CLI command.
7. **Database backup schedule:** after every archive run + daily, keeping 30 daily + 12 monthly (recommended).
8. **Phone backup defaults:** Wi-Fi only, videos only while charging, screenshots excluded (recommended).
9. **"Free up space" on phones after backup:** off by default; the OS always asks before deleting (recommended).
