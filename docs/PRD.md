# Photo Archive & Natural-Language Search — Product Requirements

**Status:** Draft v0.2 · **Date:** 2026-10-11 (v0.2: search runs fully locally, no LLM)

---

## 1. Summary

A personal/family photo archive that ingests photos from local disks and Apple Photos, stores the originals durably in S3 at the lowest possible cost, builds a searchable index (dates, places, people, scene content), and lets the owner find photos by asking in plain language — "show me photos of Yahvi", "photos from December", "photos with sea or lakes".

The product is built in three phases that are each independently useful:

| Phase | Outcome | Value on its own |
|---|---|---|
| **1 — Archive** | Every original is in S3 (cold tier) with a same-named compressed copy in a hot tier. | Offsite backup; photos are safe even if the Mac dies. |
| **2 — Enrich** | Every compressed photo has a JSON record: EXIF, place, caption/tags, detected faces with cluster IDs; user can name face clusters. | Browsable, filterable library (by date, place, person) without any LLM. |
| **3 — Search** | A secure search interface that accepts natural-language queries, parses them locally (no LLM), and returns matching photos from the SQLite index. | The "ask for a photo" experience, at $0 per query. |

## 2. Goals and non-goals

### Goals
- **G1.** Preserve every original photo bit-for-bit in S3 with a stable identifier and the ability to trace any compressed image back to its original.
- **G2.** Produce a compressed derivative per photo that is small enough for cheap LLM/ML processing and fast display, but good enough for face and scene recognition.
- **G3.** Build a per-photo metadata record (JSON) and a queryable index covering time, place, people, and content.
- **G4.** Allow the owner to name people once (cluster → name) and have that apply to all past and future photos.
- **G5.** Natural-language search that handles people, dates/ranges, places, and visual content, alone or combined ("Yahvi at the beach last December").
- **G6.** Private by default. Only the owner (and people they explicitly invite) can see anything.
- **G7.** Minimal running cost: target **< $5/month** for a ~50k-photo library at rest, with one-time processing cost kept to tens of dollars, not hundreds.

### Non-goals (v1)
- Editing, albums, sharing links, or social features.
- Video ingest (note: plan the data model so it can be added; skip in v1).
- Real-time sync from iPhone; ingest is a batch job run by the owner.
- Multi-tenant SaaS. This is a single-household system, though the design shouldn't preclude a second household later.

## 3. Users

- **Owner (primary):** technically comfortable, runs the ingest CLI on a Mac, manages AWS account, names faces.
- **Family member (secondary, phase 3):** uses the search UI only, no admin access. Optional.

## 4. Phase 1 — Archive (compress + move to S3)

### User stories
- As the owner, I can point the tool at one or more folders and/or my Apple Photos library and it uploads every photo to S3 without duplicates.
- As the owner, I can re-run the tool at any time and it only processes new/changed photos (idempotent, resumable after interruption).
- As the owner, I can find the original for any compressed image by name alone.
- As the owner, I get a summary at the end: counted, uploaded, skipped (duplicate), failed, bytes moved.

### Functional requirements
- **F1.1 Sources:** local directories (recursive), Apple Photos library (originals incl. HEIC, Live Photo still frame, RAW; honour "hidden"/"trashed" flags — skip trashed, include hidden by default with a flag).
- **F1.2 Formats:** JPEG, HEIC/HEIF, PNG, TIFF, common RAW (CR2/NEF/ARW/DNG). Unknown types are copied to `raw/` but flagged "no derivative".
- **F1.3 Identity:** every photo gets a stable `photo_id` derived from content (hash), so the same bytes from two sources map to one record. Original filename, source path, and Apple Photos UUID are kept in metadata, not in the key.
- **F1.4 Naming:** compressed object key mirrors the raw key exactly, except for the top-level prefix and file extension: `raw/<path>/<photo_id>.<orig-ext>` ↔ `compressed/<path>/<photo_id>.jpg`. A human or a script can go from one to the other with string substitution alone.
- **F1.5 Derivative spec:** JPEG, long edge 1600 px (configurable), quality ~82, sRGB, orientation baked in, EXIF/GPS **kept** in the derivative (so phase 2 can read it without touching cold storage). Plus a 320 px thumbnail for UI grids (`thumb/` prefix). Target ≤ 400 KB per compressed image.
- **F1.6 Metadata capture at ingest:** because originals go to cold storage, capture EXIF/GPS/dimensions/camera/Apple Photos metadata (favourite flag, existing Apple face names, albums, user-entered titles/keywords) **at ingest time** into a local manifest and a per-photo JSON sidecar in S3. This is cheap now and expensive later.
- **F1.7 Dedup:** content-hash dedup before upload; perceptual-hash (near-duplicate) recorded but not acted on in v1.
- **F1.8 Idempotency and resume:** a local state DB tracks every file's status; interrupted runs continue where they left off; S3 uploads are verified by checksum.
- **F1.9 Integrity:** store SHA-256 of the original in object metadata and in the record; a `verify` command samples objects and re-checks.
- **F1.10 Reporting:** dry-run mode; final summary; log of failures with reason.

### Acceptance criteria
- Re-running on an unchanged library uploads zero bytes.
- For 100 randomly chosen compressed keys, the raw key derived by substitution exists and its SHA-256 matches the recorded value.
- Compressed median size ≤ 400 KB; faces ≥ 40 px wide in the original remain detectable in the derivative (sanity check on a sample).

## 5. Phase 2 — Enrich (metadata, place, content, faces)

### User stories
- As the owner, every photo has: when it was taken, where (coordinates **and** a human-readable place), what is in it (caption, tags, any visible text), and who is in it (face cluster IDs).
- As the owner, I can see face clusters (a grid of sample faces per cluster) and assign a name; the name propagates to every photo in that cluster, and future photos are matched automatically.
- As the owner, I can merge two clusters that are the same person and split a cluster that is wrong.
- As the owner, I can re-run enrichment for only the photos missing a given enrichment (e.g. "re-run places for photos with GPS but no place").

### Functional requirements
- **F2.1 Time:** `taken_at` from EXIF DateTimeOriginal (with timezone inference from GPS when possible), falling back to file/Apple Photos date; always record which source was used.
- **F2.2 Place:** reverse-geocode GPS to country / region / city / locality, and when available a point-of-interest (beach, park, landmark). Must work offline at least to city level; online lookup is optional and rate-limited/cached.
- **F2.3 Content:** a short caption, 5–15 tags (scene, objects, activities, weather, indoor/outdoor), OCR'd text (signs, documents), and an image embedding for semantic similarity search ("sea or lakes" should match photos never tagged "lake").
- **F2.4 Faces:** detect faces, compute an embedding per face, cluster embeddings into `person_id`s. Store bbox, embedding, confidence, and cluster per face. Support incremental assignment for new photos.
- **F2.5 People map:** a `people` table mapping `person_id` → name (nullable), with merge/split history. Bootstrap names from Apple Photos' existing person labels where present.
- **F2.6 Quality/meta flags:** is_screenshot, is_document, is_blurry (basic), dominant colours (cheap, useful for "the red dress photo").
- **F2.7 Record:** one canonical JSON per photo (schema in tech doc), written to S3 `meta/` and to the index. Every field records its provenance (exif / apple-photos / model name+version) and a timestamp so enrichments can be selectively recomputed.
- **F2.8 Privacy controls:** face embeddings and the people map are treated as sensitive and never sent to third-party services; content captioning can run either locally or via a cloud model per owner's choice (a config flag, default decided in tech doc).
- **F2.9 Cost control:** enrichment steps are batched; cloud-model usage reports estimated cost before running and actual cost after.

### Acceptance criteria
- ≥ 95% of photos with GPS get a city-level place.
- Face clustering: on a labelled sample of 200 faces, ≥ 90% cluster purity; the owner needs to name ≤ 1 cluster per real person in the top 20 clusters.
- A photo of a lake with no "lake" tag is still returned in the top 20 for "lake" via embedding search.
- Enrichment is resumable and skips already-enriched photos.

## 6. Phase 3 — Search

### User stories
- As a user, I type "photos of Yahvi in December 2024" and get a grid of matching photos, newest first, with the ability to open the compressed image full-size and to request the original.
- As a user, I can ask follow-ups ("only the ones at the beach") and the filter narrows.
- As a user, I can see *why* a photo matched (person, date, place, tag) so I trust the result.
- As the owner, I can access the same search from Claude (or another assistant) as a tool, so "share me a photo of Yahvi" works in chat.
- As the owner, I can restrict who can log in.

### Functional requirements
- **F3.1 Query understanding (local, no LLM):** a rule-based parser (regex + fuzzy matching) turns free text into structured filters using vocabularies built from our own index:
  - people: resolved via the people map, fuzzy on spelling
  - date/range: absolute or relative, e.g. "last December", "summer 2023"; named events like "Diwali 2022" come from an owner-editable events list
  - place: city/country/POI from places already in the index, plus aliases
  - content terms: matched against labels, expanded through a synonym list
  - count limit and sort

  No query text leaves the system. An optional small local NLU model (spaCy) may be enabled only to improve clarification messages.
- **F3.2 Retrieval:** everything runs on the local SQLite index: (a) structured filters on time/people/place, (b) keyword search (FTS5) over captions/tags/OCR/place labels, (c) optional vector similarity using a local CLIP text encoder against image embeddings. Results are merged and ranked. Labels-only mode (a+b) must work on its own.
- **F3.3 Results:** thumbnails grid, each with date/place/people chips, "open" (compressed) and "get original" (may take minutes if in deep archive — UI must say so and notify when ready).
- **F3.4 Conversation:** multi-turn refinement within a session: a follow-up starting with "only / also / without …" adds to or removes from the previous structured filters. Session state is server-side and short-lived. The UI shows the parsed filters as removable chips ("Yahvi · Dec 2024 · beach").
- **F3.5 Tool interface:** the same search is exposed as an MCP tool / simple HTTP API. It accepts either a free-text query (parsed locally) or structured filters, so an LLM client such as Claude can pass its own parsed filters.
- **F3.6 Access:** authenticated access only; owner can invite named users; everything else returns 401. Image URLs are short-lived signed URLs, never public.
- **F3.7 No-result and ambiguity handling:** "I don't know anyone called X — did you mean Y?" ; unknown relative dates ask for clarification.
- **F3.8 Feedback:** thumbs-down on a result is recorded for later tuning (no automated learning in v1).

### Acceptance criteria
- Queries of the form {person}, {month/year}, {place}, {content word}, and any 2-way combination return correct results on a 50-query hand-built test set with ≥ 90% precision@10.
- p95 search latency ≤ 500 ms for a 50k-photo library (excluding cold-start of a serverless host); query parsing ≤ 10 ms.
- Search works with no internet access beyond S3 (or fully offline when hosted on the Mac), and no API keys configured.
- Unauthenticated access to any image or API returns 401/403; signed URLs expire ≤ 15 min.

## 7. Cross-cutting requirements

- **Security (all phases):** private buckets, encryption at rest and in transit, least-privilege credentials, no long-lived secrets in code, audit trail of access, biometric data (face embeddings) never leaves owner-controlled storage. Details in tech doc.
- **Cost:** storage at rest < $5/mo for 50k photos (~250 GB raw); one-time enrichment cost presented before run; search infra ≈ $0 when idle.
- **Portability:** all data (originals, derivatives, JSON records, index) is in open formats in S3 — the system can be rebuilt from the bucket alone.
- **Observability:** every CLI run writes a structured log and a summary; the search service logs queries (without images) for debugging.
- **Recoverability:** index can be rebuilt from `meta/` JSON; `meta/` can be rebuilt from `compressed/` + re-running enrichment.

## 8. Success metrics

- 100% of source photos archived with verified checksum.
- Owner can find a specific remembered photo in ≤ 2 queries, 8 times out of 10.
- Monthly AWS bill at rest ≤ $5 at 50k photos.

## 9. Open questions

1. Should the compressed tier retain GPS EXIF? (Recommended yes for phase 2; can strip on serving via signed URL if shared externally later.)
2. Will family members ever be added to search, or is it single-user for the foreseeable future? (Affects auth choice — see tech doc; default assumes 1–5 users.)
3. Is sending photo content (not faces) to a cloud vision model acceptable, or must captioning be local-only? (Tech doc provides both paths and a cost/quality comparison.) This is now the only place an LLM could be used; search is LLM-free.
4. Do you want video in phase 1 as "archive only, no derivative"? Cheap to add and avoids a second pass over the library.

## 10. Phase roadmap

| Phase | Milestone | Done when |
|---|---|---|
| 1a | CLI ingests one folder → S3 raw + compressed + thumb + sidecar JSON | Acceptance criteria §4 met on a 1k-photo sample |
| 1b | Apple Photos source + full library run | Whole library archived, summary report produced |
| 2a | EXIF/time/place + content tags + embeddings | §5 place & content criteria |
| 2b | Faces + clustering + naming UI (CLI or minimal web) | §5 face criteria |
| 3a | Search API + MCP tool (owner only, via Claude) | §6 criteria on test set |
| 3b | Web UI + auth for family | Invited user can log in and search |
