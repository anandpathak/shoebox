"""The archive pipeline as a library: used by `photoctl archive` and by the server (runs started from the app).

For every item (an Apple Photos photo/video, or an uploaded file):
  1. hash the original  -> photo_id (content-addressed)
  2. compressed copy    -> <out>/compressed/YYYY/YYYY-MM/<photo_id>.jpg|.mp4 (+ poster for videos), EXIF/GPS kept
     + 320px thumbnail  -> <out>/thumb/YYYY/YYYY-MM/<photo_id>.jpg
  3. sidecar            -> <out>/meta/YYYY/YYYY-MM/<photo_id>.json
  4. original           -> s3://<bucket>/raw/YYYY/YYYY-MM/<photo_id>.<ext> (Deep Archive, create-if-absent)
     + compressed/thumb/meta to S3
  5. catalog ops, then (end of run) sync and a database backup.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import threading
from collections.abc import Callable, Iterable
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .derive import compress, compress_video, make_thumb, photo_id_from_sha256, sha256_file, video_poster
from .state import State

ARCHIVE_ALBUM = "Archived to S3"
_export_lock = threading.Lock()  # Photos.app AppleScript export is not thread-safe

Event = Callable[..., None]


class SelectionError(ValueError):
    """The selection can't be read (permissions, nothing selected, …). The message says what to do."""


@dataclass
class Selection:
    selected: bool = False
    albums: tuple[str, ...] = ()
    from_date: datetime | None = None
    to_date: datetime | None = None
    uuids: tuple[str, ...] = ()
    all: bool = False
    limit: int | None = None
    include_hidden: bool = True
    include_videos: bool = True
    library: str | None = None


@dataclass
class Options:
    out: Path = Path("library")
    bucket: object | None = None            # photoctl.s3.Bucket; None for --local-only / --dry-run
    storage_class: str = "DEEP_ARCHIVE"
    long_edge: int = 1600
    quality: int = 80
    video_height: int = 720
    video_crf: int = 28
    download_missing: bool = False
    live_video: bool = False
    workers: int = 4
    mark_album: bool = True
    backup: bool = True
    sync: bool = True
    dry_run: bool = False
    local_only: bool = False
    state_db: Path | None = None


@dataclass
class Result:
    source_ref: str
    status: str
    fields: dict = field(default_factory=dict)
    uploaded_bytes: int = 0
    uuid: str = ""
    error: str | None = None
    record: dict | None = None


@dataclass
class RunSummary:
    run_id: str
    matched: int = 0
    skipped: int = 0
    todo: int = 0
    counts: dict = field(default_factory=dict)
    uploaded_bytes: int = 0
    cancelled: bool = False
    archived_refs: list[str] = field(default_factory=list)


class AppleItem:
    """An osxphotos PhotoInfo (or a test double) seen through the item interface the pipeline uses."""

    source_type = "applephotos"

    def __init__(self, photo):
        self._p = photo

    def __getattr__(self, name):
        return getattr(self._p, name)

    @property
    def ref(self) -> str:
        return self._p.uuid

    @property
    def source_ref(self) -> str:
        return f"applephotos:{self._p.uuid}"


def _as_item(x):
    return x if hasattr(x, "source_type") else AppleItem(x)


def partition(dt: datetime | None) -> str:
    if dt is None:
        return "0000/0000-00"
    return f"{dt:%Y}/{dt:%Y-%m}"


def select_photos(sel: Selection) -> list:
    import osxphotos

    if not (sel.uuids or sel.selected or sel.albums or sel.from_date or sel.to_date or sel.all):
        raise SelectionError("Choose what to archive: selected photos, an album, a date range, or everything.")
    uuids = list(sel.uuids)
    if sel.selected:
        import photoscript

        try:
            uuids += [p.uuid for p in photoscript.PhotosLibrary().selection]
        except photoscript.exceptions.AppleScriptError as e:
            raise SelectionError(
                f"Couldn't read the selection from Photos.app ({e}).\n"
                "Allow your terminal to control Photos: System Settings → Privacy & Security → "
                "Automation → <your terminal> → Photos.\n"
                "Or skip automation entirely: put the photo in an album and use --album <name>.") from e
        if not uuids:
            raise SelectionError("Nothing is selected in Photos.app.")
    try:
        db = osxphotos.PhotosDB(dbfile=sel.library) if sel.library else osxphotos.PhotosDB()
    except OSError as e:
        raise SelectionError(
            f"Can't read the Photos library ({e}).\nGrant Full Disk Access: System Settings → Privacy & Security → "
            "Full Disk Access, then restart the terminal (or the photoctl server).") from e
    photos = db.photos(uuid=uuids or None, albums=list(sel.albums) or None, from_date=sel.from_date,
                       to_date=sel.to_date)
    photos = [p for p in photos if (sel.include_videos or not p.ismovie)
              and not p.intrash and (sel.include_hidden or not p.hidden)]
    photos.sort(key=lambda p: p.date or datetime.min)
    return photos


def verify_existing(bucket, key: str, sha: str, size: int) -> None:
    head = bucket.head(key)
    if head is None:
        raise RuntimeError(f"s3://{bucket.name}/{key} reported as existing but can't be read")
    if head["ContentLength"] != size or head.get("Metadata", {}).get("sha256") not in (None, sha):
        raise RuntimeError(f"s3://{bucket.name}/{key} already exists with different content")


def add_to_album(uuids: list[str], name: str = ARCHIVE_ALBUM) -> None:
    import photoscript

    lib = photoscript.PhotosLibrary()
    album = lib.album(name) or lib.create_album(name)
    for i in range(0, len(uuids), 200):
        album.add([photoscript.Photo(u) for u in uuids[i:i + 200]])


def process(item, opts: Options) -> Result:
    """Archive one item. Never raises: failures come back as Result(status='failed')."""
    item = _as_item(item)
    ref = item.source_ref
    tmpdir = None
    out = Path(opts.out)
    try:
        src = Path(item.path) if item.path else None
        if src is None or not src.exists():
            if not (opts.download_missing and item.source_type == "applephotos"):
                return Result(ref, "missing_original", uuid=item.ref,
                              error="original not on disk (iCloud); re-run with --download-missing")
            tmpdir = Path(tempfile.mkdtemp(prefix="photoctl-"))
            with _export_lock:
                exported = item.export(str(tmpdir), use_photos_export=True)
            if not exported:
                return Result(ref, "missing_original", uuid=item.ref, error="download from iCloud failed")
            src = Path(exported[0])

        sha = sha256_file(src)
        pid = photo_id_from_sha256(sha)
        part = partition(item.date)
        ext = src.suffix.lower().lstrip(".") or "bin"
        raw_key = f"raw/{part}/{pid}.{ext}"
        is_video = bool(item.ismovie)
        comp_key = f"compressed/{part}/{pid}.{'mp4' if is_video else 'jpg'}"
        poster_key = f"compressed/{part}/{pid}.jpg" if is_video else None
        thumb_key = f"thumb/{part}/{pid}.jpg"
        meta_key = f"meta/{part}/{pid}.json"
        comp_path = out / comp_key
        meta_path = out / meta_key
        size = src.stat().st_size

        extras: list[tuple[Path, str]] = []  # companion originals that share the photo_id
        if getattr(item, "has_raw", False) and item.path_raw and Path(item.path_raw).exists():
            p = Path(item.path_raw)
            extras.append((p, f"raw/{part}/{pid}.{p.suffix.lower().lstrip('.')}"))
        if (opts.live_video and not is_video and getattr(item, "live_photo", False) and item.path_live_photo
                and Path(item.path_live_photo).exists()):
            p = Path(item.path_live_photo)
            extras.append((p, f"raw/{part}/{pid}.{p.suffix.lower().lstrip('.')}"))

        if opts.dry_run:
            return Result(ref, "would_archive", uuid=item.ref,
                          uploaded_bytes=size + sum(p.stat().st_size for p, _ in extras), fields={"raw_key": raw_key})

        status, error = "done", None
        derived: list[tuple[Path, str, str]] = []  # (local path, key, content type)
        try:
            if is_video:
                compress_video(src, comp_path, short_edge=opts.video_height, crf=opts.video_crf)
                derived.append((comp_path, comp_key, "video/mp4"))
                video_poster(src, out / poster_key, long_edge=opts.long_edge)
                derived.append((out / poster_key, poster_key, "image/jpeg"))
                make_thumb(out / poster_key, out / thumb_key)
            else:
                compress(src, comp_path, long_edge=opts.long_edge, quality=opts.quality)
                derived.append((comp_path, comp_key, "image/jpeg"))
                make_thumb(comp_path, out / thumb_key)
            derived.append((out / thumb_key, thumb_key, "image/jpeg"))
        except Exception as e:  # unknown/undecodable type: still archive the original
            status, error = "no_derivative", str(e)
        derived_keys = {k for _, k, _ in derived}

        record = {
            "schema_version": 1,
            "photo_id": pid,
            "media_type": "video" if is_video else "photo",
            "keys": {"raw": raw_key, "compressed": comp_key if comp_key in derived_keys else None,
                     "poster": poster_key if poster_key in derived_keys else None,
                     "thumb": thumb_key if thumb_key in derived_keys else None, "meta": meta_key,
                     "companions": [k for _, k in extras]},
            "file": {"sha256": sha, "orig_name": item.original_filename, "orig_ext": ext, "bytes_raw": size,
                     "width": item.width, "height": item.height, "uti": getattr(item, "uti_original", None)},
            "taken_at": item.date.isoformat() if item.date else None,
            "source": {"type": item.source_type, "ref": item.ref},
            "apple": json.loads(item.json()),  # persons, albums, keywords, title, place, labels, …
        }
        extra_meta = getattr(item, "extra_metadata", None)
        if extra_meta:
            record.update(extra_meta)
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        meta_path.write_text(json.dumps(record, indent=2, ensure_ascii=False))
        fields = {"photo_id": pid, "sha256": sha, "raw_key": raw_key, "compressed_key": record["keys"]["compressed"],
                  "meta_key": meta_key, "bytes_raw": size,
                  "bytes_compressed": comp_path.stat().st_size if comp_key in derived_keys else None}

        bucket = opts.bucket
        if bucket is None:  # --local-only
            return Result(ref, status, uuid=item.ref, error=error, record=record, fields=fields)
        s3_meta = {"sha256": sha, "orig-name": item.original_filename or src.name, "source": ref,
                   "taken-at": record["taken_at"] or ""}
        uploaded = 0
        # Content-addressed keys: if the object already exists (an earlier run, or another device racing us)
        # it holds the same bytes — check that rather than uploading again.
        for path, key in [(src, raw_key), *extras]:
            if bucket.create_if_absent(path, key, storage_class=opts.storage_class, metadata=s3_meta):
                uploaded += path.stat().st_size
            elif path is src:
                verify_existing(bucket, key, sha, size)
        for path, key, content_type in derived:
            bucket.upload(path, key, content_type=content_type, metadata=s3_meta)
            uploaded += path.stat().st_size
        bucket.upload(meta_path, meta_key, content_type="application/json")
        return Result(ref, status, uuid=item.ref, uploaded_bytes=uploaded, error=error, record=record, fields=fields)
    except Exception as e:
        return Result(ref, "failed", uuid=item.ref, error=f"{type(e).__name__}: {e}")
    finally:
        if tmpdir:
            shutil.rmtree(tmpdir, ignore_errors=True)


def run_archive(items: Iterable | None, opts: Options, *, selection: Selection | None = None, device=None,
                on_event: Event | None = None, cancel: threading.Event | None = None) -> RunSummary:
    """Archive `items` (or what `selection` picks from Apple Photos) holding the ingest lock.

    on_event(kind, **data) receives: start, item, message, end. Setting `cancel` stops after the
    items already in progress. Raises runlock.AlreadyRunning if another run holds the lock, and
    SelectionError if the selection can't be read.
    """
    from .catalog import open_catalog
    from .catalog.store import upsert_record
    from .device import load_device
    from .runlock import IngestLock, RunLog

    emit = on_event or (lambda kind, **data: None)
    device = device or load_device()
    record_state = not (opts.dry_run or opts.local_only)
    with IngestLock() as lock:
        log = RunLog(lock.run_id)

        def event(kind: str, **data) -> None:
            log.event(kind, **data)
            emit(kind, **data)

        summary = RunSummary(run_id=lock.run_id)
        try:
            source = list(items) if items is not None else select_photos(selection or Selection())
            source = [_as_item(x) for x in source]
            state = State(opts.state_db)
            todo = [x for x in source if opts.local_only or not state.is_done(x.source_ref)]
            summary.matched, summary.skipped = len(source), len(source) - len(todo)
            if selection and selection.limit:
                todo = todo[:selection.limit]
            summary.todo = len(todo)
            videos = sum(1 for x in source if x.ismovie)
            event("start", run_id=lock.run_id, device_id=device.device_id, matched=len(source), videos=videos,
                  skipped=summary.skipped, todo=len(todo), dry_run=opts.dry_run, local_only=opts.local_only)
            if not todo:
                event("end", counts={}, uploaded_bytes=0, cancelled=False)
                return summary
            if opts.bucket is not None and record_state:
                opts.bucket.client.head_bucket(Bucket=opts.bucket.name)  # fail fast on auth/bucket problems
            catalog = None if opts.dry_run else open_catalog(Path(opts.out) / "catalog.sqlite" if opts.local_only else None)
            apple_done: list[str] = []
            pending = iter(todo)
            with ThreadPoolExecutor(max_workers=opts.workers) as pool:
                running = set()

                def refill():
                    while len(running) < opts.workers and not (cancel and cancel.is_set()):
                        nxt = next(pending, None)
                        if nxt is None:
                            return
                        running.add(pool.submit(process, nxt, opts))

                refill()
                while running:
                    finished, _ = wait(running, return_when=FIRST_COMPLETED)
                    for fut in finished:
                        running.discard(fut)
                        r = fut.result()
                        summary.counts[r.status] = summary.counts.get(r.status, 0) + 1
                        summary.uploaded_bytes += r.uploaded_bytes
                        if record_state:
                            state.upsert(r.source_ref, status=r.status, error=r.error, **r.fields)
                            if r.status in ("done", "no_derivative"):
                                summary.archived_refs.append(r.source_ref)
                                if r.source_ref.startswith("applephotos:"):
                                    apple_done.append(r.uuid)
                        if catalog is not None and r.record is not None:
                            upsert_record(catalog, r.record, device, bytes_compressed=r.fields.get("bytes_compressed"))
                        event("item", source_ref=r.source_ref, status=r.status, photo_id=r.fields.get("photo_id"),
                              error=r.error, uploaded_bytes=r.uploaded_bytes)
                    refill()
            summary.cancelled = bool(cancel and cancel.is_set() and sum(summary.counts.values()) < len(todo))
            event("end", counts=summary.counts, uploaded_bytes=summary.uploaded_bytes, cancelled=summary.cancelled)
            if catalog is not None:
                catalog.close()
            if opts.mark_album and apple_done:
                try:
                    add_to_album(apple_done)
                    event("message", text=f'Added {len(apple_done)} items to the "{ARCHIVE_ALBUM}" album in Photos.')
                except Exception as e:
                    event("message", text=f"Couldn't update the Photos album ({e}); the archive itself succeeded.")
            if opts.bucket is not None and record_state:
                state.db.close()
                _after_run(opts, device, event)
            return summary
        finally:
            log.close()


def _after_run(opts: Options, device, event: Event) -> None:
    from .backup import backup
    from .runlock import AlreadyRunning
    from .sync import full_sync

    if opts.sync:
        try:
            r = full_sync(opts.bucket, device)
            event("message", text=f"Synced: sent {r.pushed_ops} changes, received {r.applied_ops}.")
        except AlreadyRunning:
            event("message", text="A sync is already running; skipped.")
        except Exception as e:
            event("message", text=f"Sync failed ({e}); run `photoctl sync` to retry.")
    if opts.backup:
        try:
            res = backup(opts.bucket, device)
            event("message", text=f"Backed up databases to s3://{opts.bucket.name}/{res.prefix}")
        except Exception as e:
            event("message", text=f"Database backup failed ({e}); run `photoctl db backup` to retry.")
