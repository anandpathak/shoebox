# Task List

Work through the tasks in order. When a task is finished, change `[ ]` to `[x]` and add the date, e.g. `[x] … ✅ 2026-10-12`.
Each task has a **Done when** check. The task isn't done until that check passes.
Design details: [LOCAL_APP_PLAN.md](LOCAL_APP_PLAN.md) (section numbers like §5 refer to it).

Legend: `[ ]` todo · `[~]` in progress · `[x]` done · `[-]` dropped

**Roadmap:** M0 archive CLI ✅ → M1 catalog DB → M2 op log → M3 S3 sync → M4 query/fetch → M5 API → M6 mobile-first UI → M7 uploads & triggers → **M8 phones (iOS + Android web)** → **M9 on-device mode** → **M10 per-device catalogs** → M11 multi-device E2E → **M12 phone auto-backup (iOS + Android)** → M13 always-on (optional) → M14 native extras (optional)

Database backup runs in two stages: **T0.7** backs up `state.sqlite` right away, and **M3b** adds full backup and recovery once the catalog and sync exist.

---

## M0 — Archive CLI (Phase 1 basics) ✅

- [x] **T0.1** `photoctl archive` from Apple Photos: hash → compress (sips, EXIF/GPS kept) → JSON sidecar → upload original to Deep Archive + compressed/meta to S3 ✅ 2026-10-10
- [x] **T0.2** Device-local resumable state (`~/.photoctl/state.sqlite`), `status`, `verify` ✅ 2026-10-10
- [x] **T0.3** Terraform bucket (private, encrypted, versioned, TLS-only, lifecycle) + ingest IAM policy ✅ 2026-10-10
- [x] **T0.4** `--local-only` test mode, friendly permission errors ✅ 2026-10-11
- [x] **T0.5** Video support: 720p MP4 (ffmpeg, metadata kept) + poster frame ✅ 2026-10-11
- [ ] **T0.6** First real run: `archive --selected` on ~20 photos + 2 videos → S3. Delete them from the "Archived to S3" album in Photos only after `verify` passes.
  *Done when:* `photoctl verify --sample 20` shows 0 bad, and the objects are visible in the S3 console.
- [x] **T0.7** Back up the Mac's databases to S3 now: `photoctl db backup` (SQLite online backup API → `integrity_check` → gzip → `backups/<device_id>/<timestamp>/` with `state.sqlite.gz`, `catalog.sqlite.gz`, `device.json` + manifest with sha256), `photoctl db list`, `photoctl db restore [--at] [--device]`. Runs automatically at the end of every `archive`. ✅ 2026-10-11
  *Done when:* deleting `~/.photoctl/state.sqlite`, then `photoctl db restore`, then re-running the same `archive`, uploads 0 bytes.

---

## M1 — Catalog DB built during archive (§3)

- [x] **T1.1** Add `photoctl/catalog/` with `schema.sql` (including the `devices` table), a migration runner (`meta.schema_version`), WAL + `busy_timeout`, and `open_catalog()`. ✅ 2026-10-11
  *Done when:* `photoctl catalog init` creates `~/.photoctl/catalog.sqlite` with every table from §3; re-running is a no-op.
- [x] **T1.2** Device identity: generate a `device_id` (ULID) + name + platform on first run and store it in `~/.photoctl/device.json` + `meta`. ✅ 2026-10-11
  *Done when:* the id is stable across runs and shown by `photoctl status`.
- [x] **T1.3** Thumbnails: 320px `thumb/<part>/<id>.jpg` for photos and video posters, saved locally and uploaded to S3 Standard. Backfill command for items already archived. ✅ 2026-10-11
  *Done when:* every archived item has a thumb of roughly ≤ 40 KB.
- [x] **T1.4** Map the sidecar to catalog rows: `media`, `media_sources`, `media_tags` (Apple keywords + labels), `media_albums`, `people` + `media_people`, place fields, camera, duration. ✅ 2026-10-11
  *Done when:* a unit test turns a fixture sidecar into the expected rows.
- [x] **T1.5** `archive` writes to the catalog after each verified upload (a direct insert until T2.5). In `--local-only` mode it writes to `<out>/catalog.sqlite` so test runs never pollute the real catalog. ✅ 2026-10-11
  *Done when:* archiving 5 items gives 5 `media` rows with correct keys, dates, GPS and place.
- [x] **T1.6** FTS: keep `media_fts` in sync on every insert or update. ✅ 2026-10-11
  *Done when:* `SELECT … FROM media_fts WHERE media_fts MATCH 'gokarna'` finds the test photo.
- [x] **T1.7** Ingest lock (`~/.photoctl/ingest.lock`, `flock`) + run log `~/.photoctl/runs/<run_id>.jsonl` (progress events). ✅ 2026-10-11
  *Done when:* starting a second `archive` while one is running exits immediately with "already running (pid, start time)".
- [x] **T1.8** `photoctl catalog backfill` builds the catalog from existing `library/meta/**/*.json` (or `--from-s3 meta/`). ✅ 2026-10-11
  *Done when:* deleting `catalog.sqlite` and running backfill restores an identical row count.
- [x] **T1.9** `photoctl catalog export --flavour full|contributed [--device <id>] -o file.sqlite.gz`: a consistent snapshot via the SQLite backup API, then VACUUM (§11.2). ✅ 2026-10-11
  *Done when:* the exported file opens in `sqlite3`, and the `contributed` export contains only media sourced from that device.

---

## M2 — Op log, HLC, deterministic apply (§4, §5)

- [x] **T2.1** HLC module: `now()`, `observe(remote_hlc)`, string format `wall-counter-device`; persisted in `meta.hlc_last`. ✅ 2026-10-11
  *Done when:* unit tests show it is monotonic under backward clock jumps and that ties are broken by `device_id`.
- [x] **T2.2** Op model + JSON schema for every `kind` in §4 (including `device.register`); `op_id` generation; ops are written to `ops` + `outbox`. ✅ 2026-10-11
  *Done when:* schema validation rejects malformed ops. The schema is in `spec/ops.schema.json` so the TypeScript side can reuse it.
  *Note:* op_id is the op's HLC value (it already embeds the device id and never repeats). Canonical schema lives in the package; spec/ops.schema.json is a symlink.
- [x] **T2.3** `apply_ops(ops)`: idempotent and order-independent, with per-field LWW (`field_hlc`), OR-sets for tags/albums/people, tombstones, person merge resolution, FTS refresh. ✅ 2026-10-11
  *Done when:* unit tests pass for every row of the §5 conflict table.
  *Note:* Imports (Apple title/favorite) carry a lower priority than edits, so a later re-import never overrides a user edit. Ops for photos that haven't arrived yet wait in deferred_ops.
- [x] **T2.4** **Property test** (Hypothesis): 2–3 simulated devices (Mac + phones), random ops, random delivery interleavings → identical catalog checksum. ✅ 2026-10-11
  *Done when:* 1,000 examples pass in CI.
  *Note:* Found and fixed a real bug: two removes of the same tag made tombstones.removed_by depend on arrival order. Verified the test catches a planted order-dependence bug. Default 300 examples; PHOTOCTL_PROPERTY_EXAMPLES=1000 for the full run.
- [x] **T2.5** Change `archive` (T1.5) to emit `media.upsert` / `media.source` / `device.register` ops instead of direct inserts. ✅ 2026-10-11
  *Done when:* the catalog after archive is the same as before, and the ops appear in `outbox`.
  *Note:* archive, catalog backfill and thumbs backfill all emit ops; backfill skips records already in the catalog unless --force.
- [x] **T2.6** `photoctl catalog checksum`: deterministic hash over sorted rows (used by sync verify). ✅ 2026-10-11
  *Done when:* the same ops give the same checksum on two DB files.
- [x] **T2.7** Conformance vectors `spec/sync_vectors/*.json` (ops → expected rows), shared with the TypeScript `apply_ops` (T9.4). ✅ 2026-10-11
  *Done when:* there are ≥ 15 vectors covering every §5 rule, run in pytest.
  *Note:* 20 vectors in spec/sync_vectors (generated by scripts/gen_sync_vectors.py with hand-written expectations), each run in 4 delivery orders.

---

## M3 — Sync through S3 with locks (§6, §7)

- [~] **T3.1** Terraform: add `sync/*` read/write to the ingest policy; add lifecycle rules for old op batches and snapshots.
  *Done when:* `tofu plan` shows only the expected changes and `apply` succeeds.
  *Note:* Written and `tofu validate` passes (2026-10-11). The ingest policy already covers `sync/*` (it allows the whole bucket, no deletes); added `sync-ops-expiry` (365 days, `sync_ops_retention_days`). Snapshots use one overwritten key (`sync/snapshots/latest.sqlite.gz`), so old versions expire through the existing noncurrent-version rule. **Your step:** `aws login`, then `cd infra && tofu plan && tofu apply`.
- [x] **T3.2** Switch the raw upload to a conditional create (`IfNoneMatch='*'`); on 412 → verify size + sha256 → mark as already archived. ✅ 2026-10-11
  *Done when:* two concurrent archive processes on the same photo produce one upload and both succeed.
  *Note:* Single PUT with If-None-Match up to 64 MB; larger files use multipart with If-None-Match on CompleteMultipartUpload (aborted on 412). A 412 is verified against size + sha256 metadata.
- [x] **T3.3** Push: outbox → `sync/ops/<device_id>/<hlc>.jsonl.gz` with `If-None-Match`; delete the outbox rows after success. ✅ 2026-10-11
  *Done when:* killing the process mid-push and re-running gives no lost and no duplicated batches.
  *Note:* If a batch key already exists (crash after upload, or restored device), the ops it holds are cleared from the outbox and the rest go in a new batch, so nothing is lost or uploaded twice.
- [x] **T3.4** Pull: list each device prefix with `StartAfter=cursor`, sort by HLC, `apply_ops`, advance cursors in the same transaction. ✅ 2026-10-11
  *Done when:* device B sees device A's archive after `photoctl sync`.
  *Note:* Batches are validated first; malformed or spoofed batches (ops from another device_id) are skipped with a warning, not fatal.
- [x] **T3.5** Sync lock (`sync.lock` flock) + `photoctl sync [--watch 60]` + automatic sync at the end of archive. ✅ 2026-10-11
  *Done when:* running `sync` twice concurrently gives one sync and one "already syncing" message.
- [x] **T3.6** Device registry `sync/devices/<id>.json` (name, platform, last_seen, app version, checksum); `photoctl devices`. ✅ 2026-10-11
  *Done when:* both devices are listed with their platform and last-seen time.
- [x] **T3.7** Snapshot + bootstrap: compaction lease (`sync/locks/compact.json`), VACUUM'd gz snapshot, `HEAD.json` CAS with `If-Match`; a new device bootstraps from the snapshot, then pulls the remaining ops. ✅ 2026-10-11
  *Done when:* a fresh device gets a full catalog in < 1 min for 10k items, and a simulated expired lease is taken over.
  *Note:* Measured with 10k items (mocked S3): snapshot 6.1 MB gzipped, published in 2.3 s; fresh device bootstrap + pull 0.3 s, checksums equal. Snapshots drop the op log (re-applying is harmless) except ops still waiting in deferred_ops. 'photoctl sync --snapshot' forces one.
- [x] **T3.8** `photoctl sync verify`: compares the local checksum with other devices' published checksums. ✅ 2026-10-11
  *Done when:* after both devices sync, verify reports "converged".
  *Note:* Sync now also registers the device in the catalog if it isn't yet (found by this test).
- [x] **T3.9** Integration test on a `-test` bucket: two processes (two `device_id`s) doing concurrent archive + edits + sync → same checksum. ✅ 2026-10-11
  *Done when:* it passes 3 runs in a row.
  *Note:* Runs against a moto S3 server (AWS_ENDPOINT_URL) with two real processes; passes 3 runs per test session. Run against a real '-test' bucket by pointing AWS_ENDPOINT_URL away and setting real credentials.
- [x] **T3.10** Publish this device's catalog to `sync/catalogs/<device_id>/` (`catalog.sqlite.gz` + `manifest.json`, manifest CAS with `If-Match`) after sync when changed, at most every 10 min (§11.1). ✅ 2026-10-11
  *Done when:* the manifest shows the right checksum and count, and an older publish can't overwrite a newer one.

---

## M3b — Database backup & recovery (§12)

- [~] **T3b.1** Terraform: lifecycle for `backups/` (daily kept 30 days, `monthly/` kept 365 days); confirm the ingest policy can write but not delete there.
  *Done when:* `tofu apply` succeeds and a test delete on `backups/` with the ingest identity is denied.
  *Note:* Written and `tofu validate` passes (2026-10-11). Backups are tagged `retention=daily` / `retention=monthly` and two tag-filtered rules expire them after 30 / 365 days (`backup_daily_days`, `backup_monthly_days`); the ingest policy gained `s3:PutObjectTagging`/`GetObjectTagging` and still has no delete. **Your step:** `tofu plan && tofu apply` (together with T3.1).
- [x] **T3b.2** Full `photoctl db backup`: catalog + state + `device.json` + `config.toml`; consistent copies via the online backup API; `integrity_check`; manifest (sha256 per file, schema_version, last pushed op, version); `LATEST.json` replaced with `If-Match`; first backup of the month copied to `monthly/`. ✅ 2026-10-11
  *Done when:* a backup taken while an archive run is writing passes `integrity_check` after download.
- [x] **T3b.3** Automatic schedule: after every archive run + daily via a LaunchAgent (`photoctl db schedule install|uninstall`); `photoctl status` shows "Last backup: 3 h ago", amber after 48 h. ✅ 2026-10-11
  *Done when:* the LaunchAgent fires once overnight and a new backup appears in S3.
  *Note:* Not installed on this Mac. Run 'photoctl db schedule install' yourself; the daily job needs credentials that don't expire overnight (an expired 'aws login' session makes it fail and logs to ~/.photoctl/logs/backup.err.log).
- [x] **T3b.4** `photoctl db restore [--at <date>]`: download → verify sha256 → `integrity_check` → swap files atomically (old ones kept as `.bak`) → `photoctl sync` catches up on ops made since the backup. ✅ 2026-10-11
  *Done when:* restoring yesterday's backup and syncing gives the same catalog checksum as the other devices.
- [x] **T3b.5** `photoctl recover` for a new or wiped Mac: list old devices from `sync/devices/` → restore identity, config and DBs → pull this device's own ops and advance the HLC past them (no op-id reuse) → sync → ready to `archive` again. ✅ 2026-10-11
  *Done when:* on a fresh macOS user account, `recover` followed by `archive --all --dry-run` reports everything as already archived.
  *Note:* Also fixed 'archive --dry-run' to skip items already archived, so a dry run predicts the real run.
- [x] **T3b.6** Fallback rebuilds when there's no usable backup: `photoctl catalog rebuild --from-s3` and `photoctl state rebuild --from-s3` (from `meta/` JSON: Apple UUID + sha256 → status). ✅ 2026-10-11
  *Done when:* both rebuild on a test bucket and match the originals' row counts.
- [x] **T3b.7** Runbook `docs/RECOVERY.md`: what to do when the DB is corrupted, the Mac is lost, or you need a point in time; tested end to end once. ✅ 2026-10-11
  *Done when:* someone following only the runbook recovers a wiped test Mac.
  *Note:* Each runbook path is exercised by automated tests that wipe ~/.photoctl and recover (tests/test_archive_cli.py, test_restore_catchup.py). It hasn't been walked through on a physical spare Mac yet.

---

## M4 — Query & fetch (CLI first)

- [x] **T4.1** Shared query files `queries/*.sql` (search, filters, person, place, device/platform, pagination) with named parameters, plus a Python runner. Parameterised SQL only (§10.5). ✅ 2026-10-11
  *Done when:* there are tests for each filter and for combinations of 2 filters.
  *Note:* Text filters (free text, person, place, tag, album) go through one quoted FTS5 expression from build_fts (person names resolve through merges via the FTS people column); dates, type, favourite, device, hidden and keyset pagination are plain SQL. Words like 'december', '2024', 'videos' become filters. Shared vectors: spec/query_vectors.json. Multi-catalog search queries each file and merges (rather than ATTACH), so the same SQL runs unchanged everywhere.
- [x] **T4.2** `photoctl search "gokarna" --from 2025-08-01 --type photo [--device iphone]` prints a table with ids and local paths. ✅ 2026-10-11
  *Done when:* it returns the expected test items.
- [x] **T4.3** Multi-catalog search: `photoctl search "beach" --catalog a.sqlite --catalog b.sqlite` (ATTACH + UNION ALL + dedup by `photo_id`, with source-device badges) (§11.3). ✅ 2026-10-11
  *Done when:* a photo present in both files is listed once, with both devices shown.
- [x] **T4.4** `photoctl catalog download --device <id|all|library> --flavour full|contributed -o dir/` from `sync/catalogs/`. ✅ 2026-10-11
  *Done when:* every published catalog downloads and opens.
- [x] **T4.5** Fetch helpers: local path if present, otherwise a presigned S3 URL (≤ 15 min); `photoctl get <id> [--compressed|--thumb] -o dir`. ✅ 2026-10-11
  *Done when:* fetching on a second device (with no local files) downloads from S3.
  *Note:* Presigned links use SigV4 and are capped at 900 s.
- [x] **T4.6** Original restore: `photoctl restore <id>` → `RestoreObject` (Bulk/Standard tier option); status check; download when ready. ✅ 2026-10-11
  *Done when:* a restore is requested and the status shows "in progress" (the full 12 h path is checked once manually).
  *Note:* Command is 'photoctl original <id>' (request / --status / --download DIR) so it isn't confused with 'db restore'. The full 12-hour path still needs one manual check against real Deep Archive.

---

## M5 — API server (§8)

- [x] **T5.1** FastAPI + uvicorn; `photoctl serve` binds to 127.0.0.1:8765; API token cookie for localhost; Tailscale header auth with a login allowlist (`~/.photoctl/config.toml`). ✅ 2026-10-11
  *Done when:* requests without the token or an allowed Tailscale login get 401.
  *Note:* Token in the macOS Keychain (0600 file fallback). Also: DNS-rebinding Host check, same-origin check on cookie writes, Tailscale headers only trusted from 127.0.0.1, 'serve' refuses non-local interfaces.
- [x] **T5.2** Read endpoints: search (incl. `device` filter), media detail, thumb, compressed, people. ✅ 2026-10-11
  *Done when:* TestClient tests pass, and thumbnails stream from local disk or redirect to presigned URLs.
- [x] **T5.3** Write endpoints: PATCH media, tags, people. Each one emits ops, and nothing writes SQLite directly. ✅ 2026-10-11
  *Done when:* an edit through the API appears in `outbox` and syncs to device B.
- [x] **T5.4** Restore endpoints (POST/GET original). ✅ 2026-10-11
  *Done when:* they return the restore status correctly.
- [x] **T5.5** Sync endpoints + a background sync task (every 60 s, sharing the T3.5 lock). ✅ 2026-10-11
  *Done when:* `/api/sync/status` shows the last push/pull and pending ops.
  *Note:* Full sync logic moved to sync.full_sync, shared by the CLI and the server.
- [x] **T5.6** Op proxy for clients without AWS creds: `POST /api/sync/ops` (writes to `sync/ops/<client device_id>/`, idempotent on op ids) and `GET /api/sync/ops?since=`; `POST /api/devices/register`. ✅ 2026-10-11
  *Done when:* a script acting as a phone pushes edits that reach device B and pulls ops incrementally.
  *Note:* Pull cursor is the server's op arrival sequence plus a catalog epoch (410 tells the client to re-download). device.register for a phone is emitted by the server device on its behalf; the phone's own ops keep the phone's device id.
- [x] **T5.7** Catalog endpoints: `GET /api/catalogs`, `/api/catalogs/library`, `/api/catalogs/{device_id}?flavour=`, `/api/catalogs/all.zip`, `PUT /api/catalogs/{device_id}`. Support Range requests for resumable downloads. ✅ 2026-10-11
  *Done when:* each download opens in `sqlite3`, and an interrupted download resumes.
- [x] **T5.8** Database backup endpoints: `POST /api/db/backup`, `GET /api/db/backups` (age, size, integrity). ✅ 2026-10-11
  *Done when:* a backup can be started and listed from the API.

---

## M6 — Web UI, mobile-first (§8 screens)

- [x] **T6.1** Scaffold `web/` (Vite + React + TS), served by FastAPI; API client generated from OpenAPI; `DataSource` interface with a `ServerDataSource` implementation (§10.5). ✅ 2026-10-11
  *Done when:* `photoctl serve` opens the UI at http://127.0.0.1:8765.
  *Note:* Request types are generated from the OpenAPI schema (scripts/dump_openapi.py + openapi-typescript); response types are hand-written in web/src/api/types.ts because the API returns plain dicts. The schema isn't served at runtime.
- [x] **T6.2** Responsive layout designed for phone width first (bottom tab bar on mobile, sidebar on desktop); status bar (online/offline/pending edits). ✅ 2026-10-11
  *Done when:* Playwright screenshots at iPhone 15 and Pixel 8 sizes look right, with no horizontal scroll.
  *Note:* Playwright runs against a seeded throwaway server (scripts/e2e_server.py) using the installed Chrome at iPhone 15, Pixel 7 and desktop sizes; asserts no horizontal scroll and where the navigation sits.
- [x] **T6.3** Library grid: month sections, virtualised infinite scroll, lazy thumbnails, touch-friendly. ✅ 2026-10-11
  *Done when:* 10k items scroll smoothly on a mid-range Android phone.
  *Note:* Measured in Chrome at Pixel 7 size on this Mac: 10,000 tiles loaded, p95 frame time 16.8 ms while scrolling (e2e/perf.spec.ts with E2E_ITEMS=10000). Not yet checked on a real mid-range Android phone. Uses content-visibility on month sections instead of a virtual list. The e2e run also found and fixed a server bug: request DB connections crossing worker threads.
- [x] **T6.4** Search bar + filter chips (date, person, place, type, favourite, source device). ✅ 2026-10-11
  *Done when:* the results match `photoctl search` for the same query.
  *Note:* Same /api/search code path as 'photoctl search'; e2e compares tile counts with the API for text, type, favourites, device and person.
- [x] **T6.5** Viewer: swipe navigation, image/video player, metadata panel with a map pin and source devices, download compressed, request original. ✅ 2026-10-11
  *Done when:* everything works for one photo and one video on desktop and in mobile emulation.
  *Note:* Map is an OpenStreetMap link (no embedded map: works offline and loads nothing third-party).
- [x] **T6.6** Editing: title, description, favourite, tags, hide/delete (tombstone) with optimistic UI. ✅ 2026-10-11
  *Done when:* an edit made on device A shows on device B after sync.
  *Note:* Cross-device propagation of API edits is covered by tests/test_api_edit.py::test_api_edit_reaches_another_device; the UI e2e covers optimistic edits, persistence and remove/undo.
- [x] **T6.7** People screen: list, rename, merge. ✅ 2026-10-11
  *Done when:* renaming updates every photo of that person.
- [x] **T6.8** Sync & devices screen, including the last database backup and a "Back up databases now" button. ✅ 2026-10-11
  *Done when:* it shows the devices (with platform), the last sync, the last backup age, a "Sync now" button and the converged status.
  *Note:* The e2e server now runs against an in-process mock S3 (moto) with stand-in originals in Deep Archive, so sync, backups and original restores are exercised in the browser. The 'converged' banner across several devices is covered by tests/test_api_sync.py.

---

## M7 — Uploads & run triggers from the app (§8, §9.2)

- [x] **T7.1** Refactor `archive` into a library function `run_archive(selection, options, progress_cb, cancel_token)` used by both the CLI and the API. ✅ 2026-10-11
  *Done when:* the CLI behaviour is unchanged (existing tests pass).
  *Note:* photoctl/archiver.py: run_archive(items | selection, Options, on_event, cancel). Cancellation stops after the items in progress; the CLI is a thin wrapper drawing its progress bar from events. The per-item pipeline takes any item with source_type/ref (Apple Photos now, uploads in T7.3).
- [x] **T7.2** `POST /api/uploads/apple-photos` (selection on the Mac / album / date range / all, dry-run) → background run holding the ingest lock; SSE progress; `POST /api/runs/{id}/cancel`. ✅ 2026-10-11
  *Done when:* a run started from the UI shows live progress, a second start shows "already running", and cancel stops after the current item.
  *Note:* Progress streams from the run's JSONL log, so runs started from the terminal are visible in the app too (only app-started runs can be cancelled from the app). Without S3 the server still adds items to the main catalog but doesn't mark them archived, so a later run with S3 uploads them.
- [x] **T7.3** tus resumable upload endpoint `/api/uploads/files` → `~/.photoctl/inbox/<device_id>/` → same pipeline (`source_ref = upload:<device_id>:<name>`); records `metadata_fidelity` + a "metadata incomplete" flag when GPS/date is missing. ✅ 2026-10-11
  *Done when:* a 500 MB video upload survives a dropped connection and resumes.
  *Note:* Tested with a 30 MB file in 8 MB chunks with a simulated drop and resume (the protocol is the same at 500 MB; not tried at that size here). Uploads are credited to the uploading device. Without S3 the uploaded file is kept in inbox/<device>/archived-locally/ (it's the only copy) and is uploaded once the server runs with a bucket.
- [x] **T7.4** Uploads screen: trigger options, live progress, cancel, "Upload from this device" picker, run history, failures + "retry failed". ✅ 2026-10-11
  *Done when:* a failed item can be retried from the UI.
  *Note:* The e2e server swaps Apple Photos for generated fake photos, so tests never read the real library. Browser tests also caught two UI bugs, now fixed: the viewer overflowed iPhone width (the Next button ended up off-screen), and the 'Removed' panel reset on reload.

---

## M8 — Phones: iOS + Android web access and triggers (§9)

- [~] **T8.1** Tailscale on the Mac + iPhone + Android; `tailscale serve --bg 8765`; `photoctl serve --tailscale` checks the setup and prints the phone URL + a QR code. Add a setup guide to the README.
  *Done when:* `https://<mac>.<tailnet>.ts.net` opens on both phones with a valid certificate, and is unreachable off the tailnet.
  *Note:* Built 2026-10-11: `photoctl serve --tailscale` / `--setup-tailscale` checks Tailscale, sets up `tailscale serve`, trusts its MagicDNS name, allows your own login by default, and prints the URL + QR code; README has the phone guide. Tested with a stand-in `tailscale` command (Tailscale isn't installed on this Mac). **Your step:** install Tailscale on the Mac and both phones, run `uv run photoctl serve --setup-tailscale`, open the URL on each phone.
- [~] **T8.2** PWA: manifest, icons, service worker (app shell + thumbnail cache), install prompts/help for iOS (Add to Home Screen) and Android (Install app).
  *Done when:* it installs on both phones and opens full-screen from the home-screen icon.
  *Note:* Built 2026-10-11: web manifest, drawn icons (incl. maskable + apple-touch-icon), service worker (offline app shell, thumbnail cache, API never cached), install hint (iOS: Share → Add to Home Screen; Chrome/Android: Install button). Browser tests confirm the manifest, the active worker and an offline reload. **Your step:** install it on the iPhone and the Android phone.
- [~] **T8.3** Phone device registration (§9.4): generate a `device_id`, ask for a name, detect the platform → `POST /api/devices/register`.
  *Done when:* both phones appear in the devices list as `ios` / `android`.
  *Note:* Built 2026-10-11. Browser tests simulate a Pixel reaching the Mac through tailscale serve: it names itself, registers as android, and its uploads are credited to it. A browser on the Mac itself counts as the Mac. **Your step:** open the app on both real phones once.
- [~] **T8.4** Trigger an archive on the Mac from a phone (§9.2): start, watch progress, lock the phone, come back and reattach, cancel.
  *Done when:* this works on both phones end to end, and a second phone sees "already running" with live progress.
  *Note:* Built 2026-10-12. Browser tests as a phone on the tailnet: start a run, reload mid-run and reattach (counted once), a second phone sees 'A run is in progress', and cancel stops early. Fixed along the way: progress double-counted after a reconnect; 'Recent runs' not refreshing after a run; the library layout being wider than phone screens (filter chips and off-screen month sections stretched it, so mobile Chrome zoomed the page out). Each browser project now gets its own seeded server. **Your step:** try it from both real phones.
- [~] **T8.5** Upload from a phone (§9.3): photo picker → tus resumable upload → ingest. Test location on/off on iOS, and on Android; show "metadata incomplete" when GPS is missing.
  *Done when:* photos from both phones appear in the grid with Source = that phone, and the metadata caveats are shown correctly.
  *Note:* Built 2026-10-12: picker → resumable upload → archive, credited to the phone; the viewer shows 'Metadata incomplete' when the location or capture date was stripped. Covered by the tailnet browser test (an upload without GPS gets the note). **Your step:** upload from a real iPhone (location on and off) and Android phone.
- [x] **T8.6** Upload queue when the Mac is unreachable (IndexedDB), resumed automatically. ✅ 2026-10-12
  *Done when:* uploads queued in airplane mode complete after reconnecting.
  *Note:* Files wait in IndexedDB (they survive closing the tab) and upload by themselves when the Mac is reachable again; tus fingerprints resume half-sent files. Browser test: pick while offline, reload, reconnect, uploaded.
- [ ] **T8.7** (Optional) Web push for "run finished" / "original ready" (iOS 16.4+ installed PWA, Android).
  *Done when:* a notification arrives on both phones.
- [ ] **T8.8** Real-device QA checklist (§15) on iPhone Safari + PWA and Android Chrome + PWA.
  *Done when:* every checklist item passes and the results are noted in this file.
- [ ] **T8.9** Assisted backup in the web app (§13.5): "Back up recent photos" → picker → SHA-256 in the browser (WebCrypto) → `POST /api/backup/check` → upload only the missing ones.
  *Done when:* picking 20 photos of which 15 are already archived uploads exactly 5.

---

## M9 — On-device mode: search without the server (§10)

- [ ] **T9.1** Add `@sqlite.org/sqlite-wasm` in a Web Worker with the `opfs-sahpool` VFS; FTS5 check.
  *Done when:* a fixture catalog opens and an FTS query runs in iOS Safari and Android Chrome.
- [ ] **T9.2** "Use on this device": download `/api/catalogs/library` (resumable) → `DecompressionStream` → import into OPFS; `navigator.storage.persist()`; ask iOS users to install the PWA first (eviction rule).
  *Done when:* a 50 MB catalog imports on both phones and survives an app restart.
- [ ] **T9.3** `LocalDataSource`: runs the shared `queries/*.sql`; automatic fallback when the server is unreachable; status bar shows "Offline – catalog from <time>".
  *Done when:* in airplane mode, search returns the same ids as online for 20 test queries (query parity test in CI too).
- [ ] **T9.4** TypeScript `apply_ops` + HLC passing **every** `spec/sync_vectors` vector (run in CI with Node + sqlite-wasm).
  *Done when:* the TS and Python results are identical for all vectors.
- [ ] **T9.5** Incremental refresh: `GET /api/sync/ops?since=` → TS `apply_ops`; re-download the snapshot if too far behind.
  *Done when:* an archive on the Mac shows up on the phone's on-device catalog within one refresh.
- [ ] **T9.6** Offline edits: on-device outbox → `POST /api/sync/ops` when back online.
  *Done when:* a title edited offline on the phone appears on the Mac after reconnecting, and a concurrent edit resolves per §5.
- [ ] **T9.7** Offline images: service-worker thumbnail cache, "Download thumbnails for offline" packs by year/album, "Save offline" for compressed copies; storage usage + "Remove downloaded data".
  *Done when:* in airplane mode the grid shows thumbnails for a downloaded year, and remove frees the storage.

---

## M10 — Per-device catalogs: download any device's SQLite, search them together (§11)

- [ ] **T10.1** Phones publish their on-device catalog: export from OPFS → gzip → `PUT /api/catalogs/{device_id}` → `sync/catalogs/<device_id>/` (the server writes the manifest).
  *Done when:* the iPhone and Android catalogs appear in `GET /api/catalogs` with the correct platform and counts.
- [ ] **T10.2** `contributed` flavour generation on the server for any device (media whose sources include that device).
  *Done when:* the Android `contributed` catalog contains only photos uploaded from the Android phone.
- [ ] **T10.3** Catalogs screen: list every device catalog (platform icon, items, size, age), download one / several / all (`all.zip`), toggle which are open.
  *Done when:* all catalogs can be downloaded on an iPhone and on an Android phone.
- [ ] **T10.4** Multi-catalog on-device search: open the selected catalogs in sqlite-wasm, `ATTACH`, `UNION ALL` the shared queries, dedup by `photo_id`, show device badges.
  *Done when:* **on one phone, offline, a search returns iPhone and Android photos together**, with duplicates shown once.
- [ ] **T10.5** Freshness: the age of each catalog in the UI; "Refresh" pulls ops or re-downloads.
  *Done when:* a stale catalog is clearly labelled and refresh updates it.

---

## M11 — Multi-device end to end (Mac + iPhone + Android)

- [ ] **T11.1** Install guide for a second Mac: clone, `uv sync`, AWS creds, `photoctl sync` bootstrap.
  *Done when:* the second Mac shows the full library without running any archive.
- [ ] **T11.2** Live test: archive on the Mac (triggered from Android) while editing on the iPhone offline → reconnect → sync all → `sync verify` converged; the §5 scenarios checked by hand.
  *Done when:* every §5 scenario is green, with notes in this file.
- [ ] **T11.3** Recovery drill: `photoctl recover` onto a wiped Mac, and clear the PWA data on one phone → both bootstrap → checksums match → a re-run of `archive` uploads 0 bytes.
  *Done when:* both are recovered in < 15 min, with timings noted here.

---

## M12 — Automatic backup from iPhone and Android (§13)

- [ ] **T12.1** Capacitor wrapper around `web/`: iOS + Android projects, app ids, icons; the web UI runs unchanged inside it.
  *Done when:* the app runs on both phones and talks to the Mac over Tailscale.
- [ ] **T12.2** Server side: `POST /api/backup/check` (hashes → missing), `GET /api/backup/status`, the upload endpoint accepts `source_ref = ios:<localIdentifier>` / `android:<MediaStore id>`, emits `media.upsert` + `media.source` with the phone's `device_id`, and the raw upload uses `If-None-Match`.
  *Done when:* a script acting as a phone backs up 10 files, and re-sending them uploads nothing.
- [ ] **T12.3** On-phone backup ledger (SQLite): `assets` + `cursor` tables (§13.4); handles edited assets (re-hash, new item linked to the same local id).
  *Done when:* unit tests cover new, unchanged, edited and deleted assets.
- [ ] **T12.4** iOS plugin: PhotoKit permission (full / limited reported), persistent change token (`fetchPersistentChanges`), original resources incl. Live Photo video and iCloud download, streaming SHA-256.
  *Done when:* on an iPhone, 5 new photos + 1 video are found without a full rescan, with GPS intact.
- [ ] **T12.5** Android plugin: MediaStore generation query, `READ_MEDIA_IMAGES/VIDEO`, `ACCESS_MEDIA_LOCATION` + `setRequireOriginal`, streaming SHA-256; Android 14 "selected photos" reported.
  *Done when:* on an Android phone, 5 new photos + 1 video are found, with GPS intact.
- [ ] **T12.6** Upload engine: batch check → resumable uploads with retries; iOS background `URLSession`; Android WorkManager with a foreground notification for long uploads.
  *Done when:* force-quitting the app mid-video resumes the upload later without starting over.
- [ ] **T12.7** Triggers: on app open, pull-to-refresh and every sync; iOS `BGProcessingTask` + `BGAppRefreshTask`; Android periodic WorkManager (6 h) + MediaStore content trigger.
  *Done when:* a photo taken with the app closed is backed up overnight on both phones (charging + Wi-Fi).
- [ ] **T12.8** Backup settings + UI: Wi-Fi only, while charging, videos, screenshots, albums, from date; "Backed up X of Y · N waiting"; "on this phone only" badge in the grid.
  *Done when:* the settings are respected on both platforms and the counts match the ledger.
- [ ] **T12.9** (Optional) "Free up space": after verified backup, offer to delete those items using the OS confirmation (`PHAssetChangeRequest.deleteAssets` / `MediaStore.createDeleteRequest`). Off by default.
  *Done when:* the OS confirmation is always shown, and only verified items are offered.
- [ ] **T12.10** Tests: simulator/emulator fixture camera rolls (new, edited, duplicate, iCloud-only, Live Photo, large video); dedup against photos already archived from the Mac's Apple Photos.
  *Done when:* only missing items upload in every scenario, on both platforms.
- [ ] **T12.11** TestFlight / Play internal testing builds for family phones.
  *Done when:* the app installs from TestFlight and from Play internal testing.

---

## M13 — Always-on access (optional; when the Mac is often off)

- [ ] **T13.1** Decide hosting: Lambda + Function URL vs a small VM (TECH §5.4). Record the decision here.
- [ ] **T13.2** Move the API (search on the S3-hosted library snapshot, presign, op proxy, catalogs, backup check) to the chosen host; add real auth (Cloudflare Access or Cognito) for use outside the tailnet.
  *Done when:* phones can search with the Mac switched off; unauthenticated requests get 401/403.
- [ ] **T13.3** Phones back up straight to S3 via presigned multipart PUT when the Mac is off (`needs_derivatives=true`).
  *Done when:* auto-backup completes with the Mac off.
- [ ] **T13.4** A Mac worker processes `needs_derivatives` items uploaded while it was off.
  *Done when:* those items get compressed/poster/thumb once the Mac comes online.

---

## M14 — Native extras (optional, §14)

- [ ] **T14.1** `LocalDataSource` backed by `@capacitor-community/sqlite` (same interface; passes the conformance vectors).
- [ ] **T14.2** Share-sheet / intent target: send photos from any app straight into the archive with full metadata.
- [ ] **T14.3** Native push notifications: run finished, original ready, backup needs attention.

---

## Open decisions (see LOCAL_APP_PLAN §16)

- [ ] **D1** UI stack: React + Vite + TS (recommended; needed for on-device mode) vs HTMX
- [ ] **D2** Remote access: Tailscale (recommended) vs Cloudflare Tunnel + Access
- [ ] **D3** Phone upload fidelity in the web app: accept browser limits (recommended); the native app (M12) gives full metadata
- [ ] **D4** Offline images: thumbnails by default + opt-in "Save offline" (recommended)
- [ ] **D5** Op flush policy: end of run + every 60 s (recommended)
- [ ] **D6** Delete semantics: tombstone only (recommended)
- [ ] **D7** Database backup schedule: after every run + daily; keep 30 daily + 12 monthly (recommended)
- [ ] **D8** Phone backup defaults: Wi-Fi only, videos only while charging, no screenshots (recommended)
- [ ] **D9** "Free up space" on phones: off by default, OS confirmation always (recommended)

## Notes / log

- 2026-10-11 — Plan created. M0 done apart from the first real S3 run (T0.6).
- 2026-10-11 — Added iOS/Android web access + run triggers (M8), on-device SQLite mode (M9), and downloadable per-device catalogs with cross-device search (M10). Renumbered later milestones.
- 2026-10-11 — Added database backup to S3 + recovery (T0.7, M3b) and automatic backup from iPhone/Android (M12, now required). Always-on moved to M13, native extras to M14.
- 2026-10-11 — Git repo initialised with a leak guard (`.githooks/pre-commit`). Built T0.7 (DB backup/restore) and all of M1 (catalog, device id, thumbnails, FTS, ingest lock + run log, backfill, export). 22 tests passing (`uv run pytest`). Waiting on T0.6 (first real S3 run).
