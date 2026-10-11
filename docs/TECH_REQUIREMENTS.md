# Photo Archive & Natural-Language Search — Technical Requirements

**Status:** Draft v0.2 · **Companion to:** [PRD.md](PRD.md) · **Date:** 2026-10-11
**v0.2 change:** search no longer calls an LLM. Query understanding is a local rule-based parser over gazetteers built from our own index (§5.1).

Guiding constraints, in priority order: **(1) originals are never lost, (2) private by default, (3) minimum recurring cost, (4) simple enough for one person to operate.** Where these conflict, the earlier wins.

---

## 1. Architecture overview

```
┌──────────────────────── Mac (owner's machine) ─────────────────────────┐
│  photoctl CLI (Python)                                                   │
│   ingest ──► hash ─► derive (jpg/thumb) ─► upload ─► sidecar JSON        │
│   enrich ──► exif/time/place ─► vision (local or Claude) ─► faces ─►     │
│              embeddings ─► cluster ─► update index                       │
│   state: ~/.photoctl/state.sqlite  (manifest, faces, people, index)      │
└───────────────┬─────────────────────────────────────┬───────────────────┘
                │ boto3 (TLS, SigV4)                   │ sync index
                ▼                                     ▼
┌──────────────────────────── S3 (one private bucket) ────────────────────┐
│ raw/         originals          → Glacier Deep Archive (lifecycle)      │
│ compressed/  1600px JPEG        → S3 Standard-IA                        │
│ thumb/       320px JPEG         → S3 Standard                           │
│ meta/        per-photo JSON     → S3 Standard                           │
│ index/       photos.sqlite(+vec) snapshot, people.json                  │
└───────────────────────────────────────┬─────────────────────────────────┘
                                        │ read index + presign
                                        ▼
┌─────────────────────── Search service (phase 3) ────────────────────────┐
│ Option A: runs on the Mac, reachable over Tailscale           ($0)      │
│ Option B: AWS Lambda + Function URL, SQLite loaded from S3   (~$0–1/mo) │
│ FastAPI: /search, /photo/{id}, /people ; MCP server wrapper             │
│ Query → local parser (regex+fuzzy) → SQL + FTS + vector → presigned URLs│
└─────────────────────────────────────────────────────────────────────────┘
```

Key design decisions:

| Decision | Choice | Why |
|---|---|---|
| Where heavy compute runs | **Owner's Mac** (Apple Silicon) | Free, already has the photos, can run face/embedding models locally; no GPU rental. |
| System of record for index | **SQLite** (FTS5 + `sqlite-vec`) kept locally, snapshotted to S3 | Zero-cost, single file, rebuildable from `meta/` JSON; no RDS/OpenSearch bill. |
| Durable per-photo record | **JSON sidecar in S3 `meta/`** | Open format; index can be regenerated from these alone. |
| Originals tier | **S3 Glacier Deep Archive** | ~$1/TB/month. Originals are rarely read; 12-hour retrieval is acceptable (UI tells the user). |
| Derivatives tier | **Standard-IA** (compressed) / **Standard** (thumb, meta) | Cheap at rest, instantly readable for search/enrichment. |
| Face pipeline | **Local only** (InsightFace/ArcFace on ONNX, or Apple Vision) | Biometrics never leave the owner's machine; no per-call cost. |
| Image embeddings | **Local CLIP/SigLIP** (open_clip, MLX/CoreML) | Free; enables "sea or lakes" semantic search. |
| Captions/tags/OCR | **Configurable:** Claude vision via Batch API *or* local VLM | Cloud gives best quality; local gives $0 and full privacy. Owner chooses. |
| Query understanding | **Local rule-based parser** (regex + fuzzy match against gazetteers from the index) | $0, offline, no API key, query text never leaves the system, deterministic and unit-testable. Vocabulary is closed (our people, places, tags), so rules cover it. |
| Search hosting | **Mac + Tailscale first**, Lambda second | $0 and private; Lambda when always-on access from anywhere is needed. |

## 2. Repository layout

```
photoframing/
  photoctl/                # Python package (Python 3.12, uv/poetry)
    cli.py                 # Typer CLI: ingest, enrich, faces, index, verify, serve
    sources/               # folder.py, apple_photos.py (osxphotos)
    derive.py              # Pillow + pillow-heif, rawpy for RAW
    exif.py                # PyExifTool / exifread
    storage/s3.py          # boto3 wrapper, multipart, checksum, presign
    state/                 # SQLite schema + migrations (sqlite-utils / alembic-lite)
    enrich/
      place.py             # offline reverse geocoder (+ optional online)
      vision_claude.py     # Anthropic SDK, Batch API, structured outputs
      vision_local.py      # MLX/ollama VLM adapter
      embed.py             # open_clip / SigLIP image + text embeddings
      faces.py             # detect + embed + cluster
    search/
      api.py               # FastAPI: /search /photo /people /feedback
      parser.py            # NL → structured query (regex + fuzzy, no LLM)
      gazetteer.py         # people/place/tag vocab loaded from the index
      dates.py             # date/range grammar
      retrieve.py          # SQL filters + FTS5 + sqlite-vec hybrid
      mcp_server.py        # MCP tool wrapper around /search
  infra/                   # Terraform or CDK: bucket, lifecycle, IAM, (Lambda)
  docs/                    # PRD.md, TECH_REQUIREMENTS.md, runbooks
  tests/
```

## 3. Phase 1 — Ingest, compress, archive

### 3.1 Identity and naming
- `photo_id` = first 26 chars of base32(SHA-256(original bytes)) — content-addressed, collision-safe, filename-safe. Keep full SHA-256 in metadata.
- Key layout (identical path under each prefix; only prefix and extension differ):
  ```
  raw/2024/2024-12/01J8X0K3Q7V4R9N2M5P6T8W1Y3.heic
  compressed/2024/2024-12/01J8X0K3Q7V4R9N2M5P6T8W1Y3.jpg
  thumb/2024/2024-12/01J8X0K3Q7V4R9N2M5P6T8W1Y3.jpg
  meta/2024/2024-12/01J8X0K3Q7V4R9N2M5P6T8W1Y3.json
  ```
  The `YYYY/YYYY-MM/` partition comes from `taken_at` (fallback: file mtime; unknown → `0000/0000-00/`). Partitioning keeps listings cheap and lifecycle rules simple; it is informational — the `photo_id` is the real key and the index stores the full key.
- Object metadata on every put: `x-amz-meta-sha256`, `x-amz-meta-orig-name`, `x-amz-meta-source` (`folder:/path` or `applephotos:UUID`), `x-amz-meta-taken-at`. Use `ChecksumAlgorithm=SHA256` on upload so S3 verifies integrity end-to-end.

### 3.2 Sources
- **Folders:** `os.walk`, follow an allow-list of extensions, skip `.DS_Store`, hidden dirs, and any path matching `--exclude` globs.
- **Apple Photos:** `osxphotos` (reads the Photos SQLite directly; exports originals, reports `uuid`, `date`, `latitude/longitude`, `favorite`, `hidden`, `intrash`, `persons` (Apple's face names), `albums`, `keywords`, `title`, `description`, `burst`, `live_photo`). Use `export` with `--download-missing` for iCloud-optimised originals (requires Photos signed in); record photos whose original couldn't be fetched as `status=missing_original` and retry later.
- Both sources produce a `SourceItem{path, source_ref, mtime, size, apple_meta?}` stream consumed by one pipeline.

### 3.3 Derivative generation
- Decode: Pillow + `pillow-heif` (HEIC), `rawpy` for RAW (embedded JPEG preview preferred for speed, full demosaic as fallback), `ImageOps.exif_transpose` to bake orientation.
- `compressed/`: long edge 1600 px, Lanczos, JPEG q=82, progressive, 4:2:0, sRGB (convert from Display-P3 with ICC), **keep EXIF + GPS** (copy via `piexif`/exiftool), strip thumbnails/maker notes. Target median ≤ 400 KB. Why 1600: Claude downsizes to ~1568 px long edge anyway, face detectors work well ≥ 1024, and it's a good full-screen preview.
- `thumb/`: long edge 320 px, q=75, EXIF stripped (UI use only).
- Live Photos: still frame only in v1; the `.mov` goes to `raw/` alongside (same id, `.mov` ext) if `--include-live-video` is set.
- Run derivative generation in a `ProcessPoolExecutor` (CPU-bound); uploads in a thread pool (IO-bound). Target ≥ 5 photos/s on an M-series Mac.

### 3.4 Metadata captured at ingest (written to sidecar + state DB)
EXIF: DateTimeOriginal, OffsetTimeOriginal, GPSLatitude/Longitude/Altitude, Make, Model, LensModel, FocalLength, FNumber, ExposureTime, ISO, Orientation, PixelXDimension/Y, ColorSpace. File: size, mime, sha256, perceptual hash (`imagehash.phash`). Apple: everything listed in §3.2. This is the cheapest moment to read it; never rely on reading `raw/` again.

### 3.5 State database (local, `~/.photoctl/state.sqlite`)
```sql
CREATE TABLE source_files (
  source_ref TEXT PRIMARY KEY,     -- 'folder:/abs/path' or 'applephotos:UUID'
  sha256 TEXT, size INTEGER, mtime REAL,
  photo_id TEXT, status TEXT,       -- pending|hashed|derived|uploaded|failed|skipped_dup
  error TEXT, updated_at TEXT);
CREATE TABLE photos (
  photo_id TEXT PRIMARY KEY, sha256 TEXT UNIQUE, raw_key TEXT, compressed_key TEXT,
  thumb_key TEXT, meta_key TEXT, orig_ext TEXT, orig_name TEXT, bytes_raw INTEGER,
  bytes_compressed INTEGER, taken_at TEXT, taken_at_source TEXT, tz TEXT,
  lat REAL, lon REAL, width INTEGER, height INTEGER, phash TEXT,
  exif_json TEXT, apple_json TEXT, ingested_at TEXT);
```
Every step is a state transition; `ingest` is safe to Ctrl-C and re-run. Uploads are verified by comparing the returned checksum; a `verify` command HEADs a random sample and compares `x-amz-meta-sha256`.

### 3.6 S3 bucket configuration (Terraform/CDK in `infra/`)
- One bucket, e.g. `my-photo-archive-<random>`; **Block Public Access: all four on**; **Object Ownership: bucket owner enforced** (no ACLs).
- Default encryption SSE-S3 (AES-256). SSE-KMS is optional; it adds ~$1/mo + per-request cost and only matters if you want key-level revocation.
- Versioning **on** (protects against accidental overwrite/delete); lifecycle: noncurrent versions expire after 30 days; abort incomplete multipart after 7 days.
- Lifecycle by prefix: `raw/` → transition to **Glacier Deep Archive at day 0** (upload directly with `StorageClass=DEEP_ARCHIVE` to skip the transition fee); `compressed/` → Standard-IA at day 30 (IA has a 128 KB min billable size — thumbs stay Standard).
- Optional: MFA Delete or Object Lock (governance mode) on `raw/` if you want ransomware-grade protection. Recommended once the archive is complete.
- Bucket policy: deny non-TLS (`aws:SecureTransport=false`), deny unencrypted puts.
- Access logging to a second small bucket or CloudTrail data events for `raw/` only (cheap audit trail of retrievals).

### 3.7 Credentials for the CLI
- Prefer **IAM Identity Center (SSO)** with `aws sso login` → short-lived creds in the AWS CLI cache; boto3 picks them up. No long-lived keys on disk.
- If using an IAM user instead: access key stored in macOS Keychain (`aws-vault` or `keyring`), never in repo or `.env`; policy scoped to that one bucket with `s3:PutObject, GetObject, ListBucket, AbortMultipartUpload, RestoreObject` and **no** `s3:DeleteObject` for the ingest principal (separate admin role for deletes).

### 3.8 Phase 1 acceptance tests
- Unit: hashing, key derivation (`compressed_key = raw_key.replace("raw/","compressed/").rsplit(".",1)[0]+".jpg"`), EXIF extraction on HEIC/JPEG/RAW fixtures, orientation handling.
- Integration: ingest a 200-photo fixture set into a LocalStack or a `-test` bucket; re-run → 0 uploads; corrupt one local file → reported failed, not uploaded.
- Performance: 1,000 mixed photos in < 5 min on M1/M2 with 100 Mbps uplink (upload-bound).

## 4. Phase 2 — Enrichment

### 4.1 Canonical photo record (`meta/<path>/<photo_id>.json`)
```jsonc
{
  "schema_version": 1,
  "photo_id": "01J8X0K3Q7V4R9N2M5P6T8W1Y3",
  "keys": {"raw": "raw/2024/2024-12/….heic", "compressed": "compressed/…jpg", "thumb": "thumb/…jpg"},
  "file": {"sha256": "…", "orig_name": "IMG_4821.HEIC", "orig_ext": "heic", "bytes_raw": 3812044,
           "width": 4032, "height": 3024, "mime": "image/heic", "phash": "c3a1…"},
  "source": [{"type": "applephotos", "ref": "5A2B…", "seen_at": "2026-08-21T10:02:11Z"}],
  "time": {"taken_at": "2024-12-14T16:42:10+05:30", "source": "exif", "tz_source": "gps"},
  "camera": {"make": "Apple", "model": "iPhone 15 Pro", "lens": "…", "iso": 64, "f": 1.78, "exposure": "1/120"},
  "place": {"lat": 15.5461, "lon": 73.7519, "country": "IN", "region": "Goa", "city": "Candolim",
            "locality": "Candolim Beach", "poi": null, "source": "offline-rg@1.2", "at": "…"},
  "content": {"caption": "A young girl building a sandcastle on a beach at sunset",
              "tags": ["beach","child","sandcastle","sunset","sea","outdoor"],
              "ocr_text": "", "is_screenshot": false, "is_document": false,
              "dominant_colors": ["#f2a65a","#3b6ea5"],
              "model": "claude-opus-5", "prompt_version": 3, "at": "…"},
  "embedding": {"model": "ViT-B-32/laion2b", "dim": 512, "ref": "index"},   // vector lives in sqlite-vec, not JSON
  "faces": [{"face_id": "f_01J8…_0", "bbox": [0.41,0.22,0.12,0.16], "det_score": 0.98,
             "person_id": "p_0007", "cluster_conf": 0.71, "model": "buffalo_l@0.7"}],
  "apple": {"favorite": true, "persons": ["Yahvi"], "albums": ["Goa 2024"], "keywords": [], "title": null},
  "enrichment": {"place": "2026-08-22T…", "content": "…", "faces": "…", "embedding": "…"}   // stamps → selective re-runs
}
```

### 4.2 Time and place
- Time: EXIF `DateTimeOriginal` + `OffsetTimeOriginal`; if no offset but GPS exists, infer tz with `timezonefinder` (offline). Else Apple Photos date (already tz-aware). Else file mtime, flagged `source=mtime`.
- Place: **offline** `reverse_geocoder` (GeoNames cities-1000, ~city level, free) as baseline; optional **online** enrichment to locality/POI via Nominatim (self-rate-limited to 1 req/s, results cached in SQLite by rounded lat/lon 3 dp) or AWS Location Service (≈$0.50 per 1k, only for photos where offline result is low confidence). Never re-geocode a cached cell.

### 4.3 Content: captions, tags, OCR
Two adapters behind one interface `describe(image_bytes) -> ContentRecord`; chosen by `config.vision.backend`.

**(a) Claude (cloud) — best quality.**
- SDK: `anthropic` Python. Send the `compressed/` JPEG downsized to 1024 px long edge (≈ 1,000–1,100 input tokens/image; 1600 px gives no accuracy benefit for tags and costs ~1.6×).
- Use **structured outputs** (`output_config.format` with a JSON schema: caption, tags[], ocr_text, is_screenshot, is_document, people_count, setting) so parsing never fails.
- Use the **Message Batches API** for the bulk pass: 50% price, no rate-limit babysitting, results keyed by `custom_id=photo_id`. Chunk 1,000 photos per batch; poll; write results; mark `enrichment.content`.
- Default model per repo convention: `claude-opus-5`. Cheaper tiers are a valid owner choice; costs below.
- Prompt is versioned (`prompt_version`) so re-captioning can be selective. System prompt is frozen and placed first so it is prompt-cached across the batch.
- Privacy: photos of family go to Anthropic's API (not used for training by default; 30-day retention on some models). If that is unacceptable, use (b).

**(b) Local VLM — $0, private.**
- Apple Silicon via `mlx-vlm` (e.g. Qwen2.5-VL-7B or Florence-2-large for tags/caption/OCR) or `ollama`. ~1–3 s/photo on M2/M3 → 50k photos ≈ 1–2 days of background runtime. Quality is lower on fine-grained tags; acceptable for v1 with embedding search covering the gap.

**Cost estimate for 50,000 photos (one-time, ~1,300 tokens in / 250 out per image, Batch API 50% off):**

| Backend | $/photo (batch) | 50k photos |
|---|---|---|
| Claude Opus 5 ($5 / $25 per MTok) | ≈ $0.006 | ≈ **$300** |
| Claude Sonnet 5 (intro $2 / $10 through 2026-08-31) | ≈ $0.0026 | ≈ **$130** |
| Claude Haiku 4.5 ($1 / $5 per MTok) | ≈ $0.0013 | ≈ **$65** |
| Local VLM (MLX) | $0 | **$0** (electricity + time) |

Recommendation for "minimum cost": run **local embeddings for everyone** (free, drives semantic search), run **local VLM** for captions/tags first, and optionally re-caption the subset you search most (favourites, people-tagged) with Claude later. The adapter design makes this a config change.

### 4.4 Image embeddings (semantic search)
- `open_clip` ViT-B-32 (laion2b) or SigLIP-base; run on Apple Silicon via MPS/CoreML, ~20–50 img/s. 512-d float32 → store as float16 in `sqlite-vec` (`vec0` virtual table). 50k × 512 × 2 B ≈ 50 MB.
- Also embed the text query at search time with the same model's text tower → cosine similarity. This is what makes "sea or lakes", "snow", "birthday cake" work without tags.

### 4.5 Faces
- Detector + embedder: **InsightFace `buffalo_l`** (SCRFD detector + ArcFace 512-d) on `onnxruntime` (CoreML EP on Mac). Alternative with zero Python deps: Apple Vision `VNDetectFaceRectanglesRequest` + `VNGenerateFaceprint` via `pyobjc` — fast, but faceprints are opaque/non-portable; prefer InsightFace for portability of the index.
- Run on `compressed/` (1600 px). Keep faces with det_score ≥ 0.6 and bbox ≥ 24 px; store bbox normalised.
- Clustering: L2-normalise embeddings; **HDBSCAN** (min_cluster_size=3, metric=cosine) for the initial pass; then **incremental assignment**: new face → nearest cluster centroid if cosine sim ≥ 0.45 (ArcFace typical threshold; tune on your data), else `unassigned` pool; re-cluster the pool weekly/on demand.
- Tables:
  ```sql
  CREATE TABLE faces (face_id TEXT PRIMARY KEY, photo_id TEXT, bbox TEXT, det_score REAL,
                      person_id TEXT, cluster_conf REAL, embedding BLOB, model TEXT);
  CREATE TABLE people (person_id TEXT PRIMARY KEY, name TEXT, aliases TEXT, centroid BLOB,
                       face_count INTEGER, sample_face_ids TEXT, hidden INTEGER DEFAULT 0);
  CREATE TABLE people_history (at TEXT, action TEXT, from_ids TEXT, to_id TEXT);   -- merge/split/rename
  ```
- Naming UI: `photoctl faces review` prints/opens a local HTML page (served on localhost) with cluster grids; owner types names, merges, splits. Also bootstrap from Apple's `persons` labels: if ≥ 70% of a cluster's faces come from photos Apple labelled "Yahvi", propose that name.
- `people.json` (id → name, no embeddings) is synced to S3 `index/`; **embeddings are never uploaded to S3 unencrypted** — either keep them local-only (default; rebuildable from `compressed/`) or upload encrypted with a local key (age/sops) as an opt-in backup.

### 4.6 Index build and sync
- `photoctl index build` materialises the search tables from `photos`, `faces`, `people`, and `meta` JSON:
  ```sql
  CREATE VIRTUAL TABLE photo_fts USING fts5(photo_id UNINDEXED, caption, tags, ocr_text, place_text, people_text);
  CREATE VIRTUAL TABLE photo_vec USING vec0(photo_id TEXT PRIMARY KEY, embedding float[512]);
  CREATE INDEX idx_photos_taken ON photos(taken_at);  -- plus lat/lon, city, person via join table
  CREATE TABLE photo_people (photo_id TEXT, person_id TEXT, PRIMARY KEY(photo_id, person_id));
  -- vocab for the query parser (§5.1); small, hand-editable, rebuilt from photos/people + vocab/*.yaml
  CREATE TABLE vocab_place (alias TEXT PRIMARY KEY, field TEXT, value TEXT);   -- 'bombay' → city 'Mumbai'
  CREATE TABLE vocab_tag   (alias TEXT PRIMARY KEY, tags TEXT);                -- 'sea' → ["sea","ocean","beach","coast"]
  CREATE TABLE vocab_event (alias TEXT PRIMARY KEY, date_from TEXT, date_to TEXT); -- 'diwali 2022' → range
  ```
- Place vocab is generated from distinct `country/region/city/locality/poi` values in the index plus `vocab/places.yaml` (aliases). Tag vocab is generated from distinct tags plus `vocab/synonyms.yaml`. Events come only from `vocab/events.yaml` (owner-maintained).
- `photoctl index push` uploads `index/photos.sqlite` (VACUUM'd, ~100–200 MB at 50k photos with vectors; faces.embedding excluded) and `index/people.json`. Versioned key (`index/photos.sqlite` + `index/photos.<ts>.sqlite` kept 5 deep).
- Rebuild path: `photoctl index rebuild --from-s3` streams `meta/` JSON (50k objects ≈ $0.02 of GETs) → SQLite. Embeddings/faces are recomputed from `compressed/` if the local DB is lost.

## 5. Phase 3 — Search

### 5.1 Query understanding (NL → structured), local and rule-based
No LLM and no network call. The vocabulary is closed (our people, our places, our tags), so a deterministic parser over **gazetteers loaded from the index** handles it. Target: < 5 ms per query, pure Python, fully unit-tested against the 50-query test set.

**Output** (same shape whether it comes from the parser or from an MCP caller's structured args):
```json
{ "people": ["p_0007"], "people_mode": "all",            // "all" for and/with, "any" for or
  "date_from": "2024-12-01", "date_to": "2024-12-31",
  "place": {"field": "city", "value": "Candolim"},
  "content_terms": ["beach"], "content_expanded": ["beach","sea","coast","sand"],
  "flags": {"screenshots": false, "documents": false, "favorites": false},
  "limit": 24, "sort": "newest",
  "understood": [{"span": "yahvi", "as": "person", "value": "Yahvi"},
                 {"span": "last december", "as": "date", "value": "2025-12-01..2025-12-31"}],
  "clarification": null }
```

**Pipeline** (each step consumes the tokens it matches; leftovers go to the next step):
1. **Normalise:** lowercase, strip punctuation except `-` and `/`, drop possessive `'s`, collapse whitespace. Keep the original spans for `understood[]`.
2. **Refinement detection:** a leading `only|just|also|but|and|without|except|not` with an active `session_id` means "merge with the previous structured query": add filters, or negate them for `without/except/not`. Otherwise start fresh.
3. **Modifiers (regex):** limit (`top 10`, `10 photos`), sort (`oldest first`, `latest`), flags (`screenshots`, `documents|receipts`, `favou?rites`).
4. **Dates (`dates.py`, regex grammar, "today" injected for tests):**
   | Pattern | Example → range |
   |---|---|
   | year | `2023` → 2023-01-01..2023-12-31 |
   | month [year] | `december 2024`; bare `december` → most recent past December |
   | relative | `today`, `yesterday`, `last/this week/month/year`, `last december`, `N days/months/years ago` |
   | season [year] | `summer 2023` (month ranges from config, default northern hemisphere; editable for monsoon etc.) |
   | range | `2019-2021`, `between march and june 2022`, `from 2018 to 2020` |
   | explicit date | ISO `2024-12-14`, `14/12/2024` (day-first by config), `14 dec 2024` (via `dateutil.parser` on the matched span only) |
   | event | longest match in `vocab_event` (`diwali 2022`, `goa trip`) |

   An unknown event-like phrase (`christmas 2021` with no entry) is left as a content term. It is not guessed.
5. **People:** match 1–3-token n-grams against people names and aliases with `rapidfuzz` (`token_sort_ratio ≥ 88` accepts; 70–88 raises a clarification "I don't know anyone called *Yahavi*, did you mean *Yahvi*?"). Joined by `and/with/&` → `people_mode=all`; joined by `or` → `any`. A name after `of|with` that matches no one also raises a clarification.
6. **Places:** longest n-gram match in `vocab_place` (exact first, then fuzzy ≥ 90), optionally after `in|at|near|from`. Most specific field wins (poi > locality > city > region > country).
7. **Content terms:** drop stopwords (`show me photos pictures pics of the a with from in at ...`); the remaining tokens are content terms. Expand each through `vocab_tag` (`sea` → sea/ocean/beach/coast). `or` between terms means any-of.
8. **Empty result of parsing** (no filters, no terms): return recent photos and note "showing latest".

**Optional local NLU fallback (off by default):** spaCy `en_core_web_sm` (~12 MB, CPU) tags PERSON/GPE/DATE spans that the rules left unmatched. They are used only to word clarifications ("*Ravi* looks like a name but isn't in your people list"), never to invent filters. Enable with `search.nlu=spacy`. Rules plus our own gazetteers beat generic NER on this data, so this is a nicety.

**Why this is enough:** every filter must ultimately match a value already in the index. A parser that only recognises indexed values cannot produce filters that return nothing because of hallucinated names or places. `understood[]` is shown in the UI as removable chips, so a misparse is visible and fixable in one click.

### 5.2 Retrieval and ranking
1. **Hard filters** (SQL): people (via `photo_people`, resolved through aliases), date range, city/country, `is_screenshot=0` unless asked.
2. **Candidate generation** within the filtered set: FTS5 BM25 over caption/tags/ocr/place using `content_expanded` (OR-joined, quoted, so user text is never FTS syntax), and `sqlite-vec` KNN on the CLIP **text-tower** embedding of the raw content terms (top 200). The CLIP text encoder is local (same model as §4.4), so it is not an LLM call. It is what finds the untagged lake. `search.vector=false` turns it off for a labels-only search.
3. **Fusion:** reciprocal-rank fusion of FTS and vector lists; small boosts for `apple.favorite`, for all requested people present, and for place text match. If the query has no content terms, rank by date only.
4. Return `photo_id, thumb_url (presigned 15 min), taken_at, place.city, people names, match_reasons[]`.
5. "Get original": `RestoreObject` on `raw/` (Deep Archive Standard retrieval ≈ $0.02/GB + per-request; Bulk ≈ $0.0025/GB, 48 h); record a pending restore; poll `HEAD` for `x-amz-restore: ongoing-request="false"`; then presign. UI/Claude tool reports "ready in ~12 h, I'll flag it" — optional SNS/email when done.

### 5.3 API and MCP surface
- FastAPI, single process, loads `photos.sqlite` read-only (on Lambda: download from S3 to `/tmp` on cold start; ETag-checked).
  - `POST /search {query?, filters?, session_id?}` → results + `understood[]`, or a clarification. `filters` uses the §5.1 shape and skips the parser.
  - `GET /photo/{id}` → full record + presigned compressed URL
  - `POST /photo/{id}/restore` → starts Glacier restore
  - `GET /people` (names only) ; `POST /feedback`
- **MCP server** (`photoctl serve-mcp`, stdio or streamable-HTTP) exposing `search_photos(query?, people?, date_from?, date_to?, place?, terms?)` (structured args let the calling assistant do its own parsing; otherwise the local parser runs), `list_people()`, `get_photo(photo_id)`, `request_original(photo_id)`. Register it in Claude Desktop/Claude Code → "share me a photo of Yahvi" works in chat, and the model receives the thumbnail as an image block. This is the cheapest phase-3 UI: no web frontend needed for the owner.
- Web UI (3b): minimal React/HTMX page served by the same FastAPI app — search box, grid, lightbox, "get original" with status.

### 5.4 Hosting options (pick A first, move to B when needed)

| | **A. Mac + Tailscale** | **B. Lambda + Function URL** | C. Always-on VM (Lightsail/Hetzner) |
|---|---|---|---|
| Cost | $0 | ~$0–1/mo at family volume (free tier covers it) | $4–6/mo |
| Availability | Only when Mac is on | Always | Always |
| Auth | Tailscale identity (MagicDNS, ACLs) — nothing exposed to internet | Cognito Hosted UI or **Cloudflare Access** (free ≤ 50 users) in front | same as B |
| Cold start | none | 1–3 s (SQLite download ~150 MB → use Lambda ephemeral storage 512 MB+, or mount EFS ~$0.30/GB/mo) | none |
| Secrets | none | none (IAM role only) | none |
Search needs only S3 (presign) plus the local SQLite file and CLIP text model. There are no third-party API keys and no database server. On Lambda, bundle the CLIP text tower (~250 MB for ViT-B-32 text weights in a container image), or set `search.vector=false` for a lighter labels-only deploy.

### 5.5 Security controls (all phases)
- **Identity/authz:** SSO/short-lived creds for CLI; Lambda role with `s3:GetObject` on `compressed/*, thumb/*, meta/*, index/*`, `s3:RestoreObject` + `GetObject` on `raw/*`, nothing else. No principal other than the admin role can delete.
- **Data:** all buckets private + encrypted; TLS-only policy; versioning; optional Object Lock on `raw/`. Presigned URLs ≤ 15 min, generated per request, never logged. EXIF GPS stays in `compressed/` for the owner; if a "share externally" feature is ever added, serve a stripped copy.
- **Biometrics:** face embeddings local-only by default; people names in S3 are just names ↔ opaque ids. Provide `photoctl faces purge` to delete all embeddings. (If anyone outside the household is ever indexed, BIPA/GDPR-style consent considerations apply — out of scope for a personal archive, but the switch exists.)
- **LLM boundary:** the search path makes no LLM or third-party calls; query text stays on owner-controlled infrastructure. The only possible cloud-model use is enrichment captioning, and only when `vision.backend=claude`: derivative images only, never originals or face crops. Treat that model output as untrusted data: structured outputs + schema validation.
- **Query safety:** parser output values come from the vocab tables. Free-text content terms go into FTS5 as quoted phrases and SQL uses parameterised queries only. User text is never interpolated into SQL or FTS syntax.
- **Secrets:** `ANTHROPIC_API_KEY` (only if `vision.backend=claude`) from Keychain; never in git; `.env` git-ignored; pre-commit secret scan.
- **Web surface:** auth in front of every route (Tailscale or Cloudflare Access/Cognito), CSRF not needed for bearer-token JSON API, rate-limit `/search` (e.g. 60/min/user), CORS restricted to the UI origin, security headers. Dependencies pinned; `pip-audit` in CI.
- **Audit:** CloudTrail management events (free tier) + S3 data events on `raw/` only; CLI writes structured JSONL logs locally.
- **Backups of the index:** `index/` in S3 with versioning; local `state.sqlite` in Time Machine; both rebuildable anyway.

## 6. Cost model (steady state, 50k photos ≈ 250 GB raw, 15 GB compressed, 1.5 GB thumbs)

| Item | Monthly |
|---|---|
| raw/ Deep Archive 250 GB × $0.00099/GB | $0.25 (+ ~$0.04 for the 40 KB/object overhead on 50k objects) |
| compressed/ Standard-IA 15 GB × $0.0125 | $0.19 |
| thumb/ + meta/ + index/ Standard ~2 GB × $0.023 | $0.05 |
| Requests (enrich/search reads, ~100k GET/mo) | ~$0.04 |
| Lambda + Function URL at family volume | $0 (free tier) — or $0 on Mac |
| Query understanding (local parser) | $0 |
| CloudTrail/SSM | $0–0.40 |
| **Total at rest** | **≈ $1/month** |

One-time: uploads 50k×4 PUTs ≈ $1; Deep Archive has a 180-day minimum (deleting early still bills 180 days); enrichment: $0 local, or $65–300 via Claude per §4.3 table. Glacier restores are the only "surprise" cost — the UI must always say so before starting one.

## 7. Tech stack summary

| Concern | Choice |
|---|---|
| Language / runtime | Python 3.12, `uv` for env, Typer CLI, pydantic models |
| Imaging | Pillow, pillow-heif, rawpy, piexif / PyExifTool, imagehash |
| Apple Photos | osxphotos |
| AWS | boto3; Terraform (or CDK) for bucket/lifecycle/IAM/Lambda |
| State + index | SQLite (WAL), FTS5, sqlite-vec |
| Vision | Anthropic Python SDK (`claude-opus-5`, Batch API, structured outputs) **or** mlx-vlm / ollama |
| Embeddings | open_clip (ViT-B-32) on MPS / CoreML |
| Faces | insightface buffalo_l on onnxruntime (CoreML EP); hdbscan / scikit-learn |
| Geocoding | reverse_geocoder + timezonefinder (offline); Nominatim / AWS Location (optional) |
| Query parsing | stdlib `re` + `python-dateutil` + `rapidfuzz`; optional spaCy `en_core_web_sm` |
| Search service | FastAPI + uvicorn; Mangum on Lambda; MCP Python SDK for the tool server |
| Auth | Tailscale (A) / Cloudflare Access or Cognito (B) |
| Testing | pytest, LocalStack (or a `-test` bucket), fixture set of ~200 photos incl. HEIC/RAW/no-EXIF/rotated |

## 8. Phase 1 implementation plan (first sprint)

1. `infra/`: bucket + lifecycle + IAM + TLS/encryption policies; `terraform apply`.
2. `photoctl ingest --source folder:/path --dry-run` → manifest only; then real run on 1k photos.
3. Add Apple Photos source; handle iCloud-missing originals.
4. `photoctl verify --sample 200`.
5. Full library run overnight; review summary; turn on Object Lock/MFA-delete for `raw/`.

Phase 2 starts with the **free** parts (EXIF/time/place, CLIP embeddings, faces) so search is useful before any LLM spend; captions/tags follow as a config choice.

## 9. Risks and mitigations

| Risk | Mitigation |
|---|---|
| iCloud-optimised library → originals not on disk | `osxphotos --download-missing`; report `missing_original`; retry pass |
| HEIC/RAW decode edge cases | fixture set in CI; fall back to embedded preview; never block upload of the raw |
| Face clustering over/under-merges | conservative threshold + review UI; merge/split history; Apple labels as bootstrap |
| Deep Archive retrieval surprises | UI warns; default Bulk retrieval; `restore` requires explicit confirmation |
| Mac-hosted search unavailable | Tailscale first; move to Lambda (B) when annoying — same code via Mangum |
| Cloud vision privacy concern | backend switch; default to local if undecided |
| Rule parser misses a phrasing | `understood[]` chips make misparses visible; failed/thumbs-down queries are logged and become new test cases and rules; vocab YAML is owner-editable |
| Index divergence between Mac and S3 | `index push` is the only writer; service reads ETag, reloads on change |
