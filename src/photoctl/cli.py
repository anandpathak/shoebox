"""photoctl — archive Apple Photos originals to S3 and keep small, metadata-rich copies locally.

For every photo (videos: compressed/…/<id>.mp4 at 720p via ffmpeg + a compressed/…/<id>.jpg poster frame):
  1. hash the original  -> photo_id (content-addressed)
  2. compress with sips -> <out>/compressed/YYYY/YYYY-MM/<photo_id>.jpg   (EXIF + GPS kept)
     + 320px thumbnail  -> <out>/thumb/YYYY/YYYY-MM/<photo_id>.jpg
  3. write sidecar      -> <out>/meta/YYYY/YYYY-MM/<photo_id>.json        (Apple Photos metadata)
  4. upload original    -> s3://<bucket>/raw/YYYY/YYYY-MM/<photo_id>.<ext>   (Deep Archive)
     + compressed/thumb/meta -> s3://<bucket>/compressed/..., thumb/..., meta/...
  5. add it to the catalog (~/.photoctl/catalog.sqlite) and to a "Archived to S3" album in Photos.
At the end of a run the local databases are backed up to s3://<bucket>/backups/<device_id>/.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import click

from . import paths
from .derive import compress, compress_video, make_thumb, photo_id_from_sha256, sha256_file, video_poster
from .state import State

ARCHIVE_ALBUM = "Archived to S3"
_export_lock = threading.Lock()  # Photos.app AppleScript export is not thread-safe


@dataclass
class Result:
    source_ref: str
    status: str
    fields: dict = field(default_factory=dict)
    uploaded_bytes: int = 0
    uuid: str = ""
    error: str | None = None
    record: dict | None = None


def _partition(dt: datetime | None) -> str:
    if dt is None:
        return "0000/0000-00"
    return f"{dt:%Y}/{dt:%Y-%m}"


def _human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def _bucket(bucket: str | None, profile: str | None):
    from .s3 import Bucket

    if not bucket:
        raise click.UsageError("--bucket (or $PHOTOCTL_BUCKET) is required.")
    return Bucket(bucket, profile)


def _select_photos(library, uuids, selected, albums, from_date, to_date, include_hidden, include_videos,
                   all_photos, limit):
    import osxphotos

    if not (uuids or selected or albums or from_date or to_date or all_photos):
        raise click.UsageError("Choose what to archive: --selected, --album, --from/--to, --uuid, or --all.")

    if selected:
        import photoscript

        try:
            uuids = list(uuids) + [p.uuid for p in photoscript.PhotosLibrary().selection]
        except photoscript.exceptions.AppleScriptError as e:
            raise click.ClickException(
                f"Couldn't read the selection from Photos.app ({e}).\n"
                "Allow your terminal to control Photos: System Settings → Privacy & Security → "
                "Automation → <your terminal> → Photos.\n"
                "Or skip automation entirely: put the photo in an album and use --album <name>."
            ) from e
        if not uuids:
            raise click.UsageError("Nothing is selected in Photos.app.")

    try:
        db = osxphotos.PhotosDB(dbfile=library) if library else osxphotos.PhotosDB()
    except OSError as e:
        raise click.ClickException(
            f"Can't read the Photos library ({e}).\nGrant your terminal app Full Disk Access: "
            "System Settings → Privacy & Security → Full Disk Access, then restart the terminal."
        ) from e
    photos = db.photos(
        uuid=list(uuids) or None,
        albums=list(albums) or None,
        from_date=from_date,
        to_date=to_date,
    )
    photos = [p for p in photos if (include_videos or not p.ismovie)
              and not p.intrash and (include_hidden or not p.hidden)]
    photos.sort(key=lambda p: p.date or datetime.min)
    return photos[:limit] if limit else photos


def _process(photo, *, out: Path, bucket, storage_class, long_edge, quality, video_height, video_crf,
             download_missing, live_video, dry_run) -> Result:
    ref = f"applephotos:{photo.uuid}"
    tmpdir = None
    try:
        src = Path(photo.path) if photo.path else None
        if src is None or not src.exists():
            if not download_missing:
                return Result(ref, "missing_original", uuid=photo.uuid,
                              error="original not on disk (iCloud); re-run with --download-missing")
            tmpdir = Path(tempfile.mkdtemp(prefix="photoctl-"))
            with _export_lock:
                exported = photo.export(str(tmpdir), use_photos_export=True)
            if not exported:
                return Result(ref, "missing_original", uuid=photo.uuid, error="download from iCloud failed")
            src = Path(exported[0])

        sha = sha256_file(src)
        pid = photo_id_from_sha256(sha)
        part = _partition(photo.date)
        ext = src.suffix.lower().lstrip(".") or "bin"
        raw_key = f"raw/{part}/{pid}.{ext}"
        is_video = bool(photo.ismovie)
        comp_key = f"compressed/{part}/{pid}.{'mp4' if is_video else 'jpg'}"
        poster_key = f"compressed/{part}/{pid}.jpg" if is_video else None
        thumb_key = f"thumb/{part}/{pid}.jpg"
        meta_key = f"meta/{part}/{pid}.json"
        comp_path = out / comp_key
        meta_path = out / meta_key
        size = src.stat().st_size

        extras: list[tuple[Path, str]] = []  # companion originals that share the photo_id
        if photo.has_raw and photo.path_raw and Path(photo.path_raw).exists():
            p = Path(photo.path_raw)
            extras.append((p, f"raw/{part}/{pid}.{p.suffix.lower().lstrip('.')}"))
        if live_video and not is_video and photo.live_photo and photo.path_live_photo and Path(photo.path_live_photo).exists():
            p = Path(photo.path_live_photo)
            extras.append((p, f"raw/{part}/{pid}.{p.suffix.lower().lstrip('.')}"))

        if dry_run:
            return Result(ref, "would_archive", uuid=photo.uuid,
                          uploaded_bytes=size + sum(p.stat().st_size for p, _ in extras),
                          fields={"raw_key": raw_key})

        status = "done"
        error = None
        derived: list[tuple[Path, str, str]] = []  # (local path, key, content type)
        try:
            if is_video:
                compress_video(src, comp_path, short_edge=video_height, crf=video_crf)
                derived.append((comp_path, comp_key, "video/mp4"))
                video_poster(src, out / poster_key, long_edge=long_edge)
                derived.append((out / poster_key, poster_key, "image/jpeg"))
                make_thumb(out / poster_key, out / thumb_key)
            else:
                compress(src, comp_path, long_edge=long_edge, quality=quality)
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
            "file": {"sha256": sha, "orig_name": photo.original_filename, "orig_ext": ext, "bytes_raw": size,
                     "width": photo.width, "height": photo.height, "uti": photo.uti_original},
            "taken_at": photo.date.isoformat() if photo.date else None,
            "source": {"type": "applephotos", "ref": photo.uuid},
            "apple": json.loads(photo.json()),  # persons, albums, keywords, title, place, labels, …
        }
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        meta_path.write_text(json.dumps(record, indent=2, ensure_ascii=False))
        fields = {
            "photo_id": pid, "sha256": sha, "raw_key": raw_key,
            "compressed_key": record["keys"]["compressed"], "meta_key": meta_key, "bytes_raw": size,
            "bytes_compressed": comp_path.stat().st_size if comp_key in derived_keys else None,
        }

        s3_meta = {"sha256": sha, "orig-name": photo.original_filename or src.name,
                   "source": ref, "taken-at": record["taken_at"] or ""}
        uploaded = 0
        if bucket is None:  # --local-only
            return Result(ref, status, uuid=photo.uuid, error=error, record=record, fields=fields)
        # Content-addressed keys: if the object already exists (an earlier run, or another device racing us)
        # it holds the same bytes — check that rather than uploading again.
        for path, key in [(src, raw_key), *extras]:
            if bucket.create_if_absent(path, key, storage_class=storage_class, metadata=s3_meta):
                uploaded += path.stat().st_size
            elif path is src:
                _verify_existing(bucket, key, sha, size)
        for path, key, content_type in derived:
            bucket.upload(path, key, content_type=content_type, metadata=s3_meta)
            uploaded += path.stat().st_size
        bucket.upload(meta_path, meta_key, content_type="application/json")

        return Result(ref, status, uuid=photo.uuid, uploaded_bytes=uploaded, error=error, record=record,
                      fields=fields)
    except Exception as e:
        return Result(ref, "failed", uuid=photo.uuid, error=f"{type(e).__name__}: {e}")
    finally:
        if tmpdir:
            shutil.rmtree(tmpdir, ignore_errors=True)


def _verify_existing(bucket, key: str, sha: str, size: int) -> None:
    head = bucket.head(key)
    if head is None:
        raise RuntimeError(f"s3://{bucket.name}/{key} reported as existing but can't be read")
    if head["ContentLength"] != size or head.get("Metadata", {}).get("sha256") not in (None, sha):
        raise RuntimeError(f"s3://{bucket.name}/{key} already exists with different content")


def _add_to_album(uuids: list[str], name: str) -> None:
    import photoscript

    lib = photoscript.PhotosLibrary()
    album = lib.album(name) or lib.create_album(name)
    for i in range(0, len(uuids), 200):
        album.add([photoscript.Photo(u) for u in uuids[i:i + 200]])


def _run_sync(s3, device, quiet: bool = False, force_snapshot: bool = False) -> None:
    from .catalog import open_catalog
    from .runlock import AlreadyRunning, SyncLock
    from .sync import sync_once

    from .snapshot import bootstrap, needs_snapshot, publish_snapshot

    try:
        with SyncLock():
            head = bootstrap(s3)
            if head:
                click.echo(f"Started from the library snapshot of {head['created_at']} ({head['media']} items).")
            conn = open_catalog()
            try:
                r = sync_once(conn, s3, device)
                if force_snapshot or needs_snapshot(conn, s3):
                    snap = publish_snapshot(conn, s3, device)
                    if snap and not quiet:
                        click.echo(f"Published a library snapshot ({snap['media']} items).")
            finally:
                conn.close()
    except AlreadyRunning:
        click.echo("A sync is already running; skipped.", err=True)
        return
    except Exception as e:
        click.echo(f"Sync failed ({e}); run `photoctl sync` to retry.", err=True)
        return
    if not quiet or r.pushed_ops or r.applied_ops:
        click.echo(f"Synced: pushed {r.pushed_ops} ops in {r.pushed_batches} batches, "
                   f"applied {r.applied_ops} new ops from {r.pulled_batches} batches"
                   + (f", skipped {len(r.rejected_batches)} bad batches" if r.rejected_batches else ""))


def _run_backup(s3, device) -> None:
    from .backup import backup

    try:
        res = backup(s3, device)
        click.echo(f"Backed up databases to s3://{s3.name}/{res.prefix}")
    except Exception as e:
        click.echo(f"Database backup failed ({e}); run `photoctl db backup` to retry.", err=True)


@click.group()
def main():
    """Archive Apple Photos originals to S3; keep a small JPEG with full metadata for processing."""


@main.command()
@click.option("--bucket", envvar="PHOTOCTL_BUCKET", help="S3 bucket (or $PHOTOCTL_BUCKET).")
@click.option("--profile", envvar="AWS_PROFILE", default=None, help="AWS profile (or $AWS_PROFILE).")
@click.option("--out", type=click.Path(path_type=Path), default=Path("library"), show_default=True,
              help="Local dir for compressed copies, thumbnails and JSON sidecars.")
@click.option("--selected", is_flag=True, help="Photos currently selected in Photos.app.")
@click.option("--album", "albums", multiple=True, help="Album name (repeatable).")
@click.option("--from", "from_date", type=click.DateTime(["%Y-%m-%d"]), help="Taken on/after YYYY-MM-DD.")
@click.option("--to", "to_date", type=click.DateTime(["%Y-%m-%d"]), help="Taken before YYYY-MM-DD.")
@click.option("--uuid", "uuids", multiple=True, help="Apple Photos UUID (repeatable).")
@click.option("--all", "all_photos", is_flag=True, help="Entire library.")
@click.option("--limit", type=int, help="Process at most N photos (oldest first).")
@click.option("--library", type=click.Path(exists=True), help="Path to a .photoslibrary (default: system library).")
@click.option("--include-hidden/--skip-hidden", default=True, show_default=True)
@click.option("--download-missing", is_flag=True, help="Fetch iCloud-only originals via Photos.app (slow).")
@click.option("--live-video", is_flag=True, help="Also archive the Live Photo .mov.")
@click.option("--videos/--no-videos", default=True, show_default=True, help="Include videos (needs ffmpeg).")
@click.option("--video-height", default=720, show_default=True, help="Compressed video short edge in px.")
@click.option("--video-crf", default=28, show_default=True, help="x264 CRF; higher = smaller/worse.")
@click.option("--storage-class", default="DEEP_ARCHIVE", show_default=True,
              type=click.Choice(["DEEP_ARCHIVE", "GLACIER_IR", "GLACIER", "STANDARD_IA", "STANDARD"]),
              help="Storage class for originals.")
@click.option("--long-edge", default=1600, show_default=True, help="Compressed long edge in px.")
@click.option("--quality", default=80, show_default=True, help="JPEG quality 1-100.")
@click.option("--workers", default=4, show_default=True)
@click.option("--mark-album/--no-mark-album", default=True, show_default=True,
              help=f'Add archived photos to a "{ARCHIVE_ALBUM}" album in Photos.')
@click.option("--backup/--no-backup", "do_backup", default=True, show_default=True,
              help="Back up the local databases to S3 at the end of the run.")
@click.option("--dry-run", is_flag=True, help="Show what would be archived; touch nothing.")
@click.option("--local-only", is_flag=True,
              help="Only compress + write sidecars locally (catalog in <out>/catalog.sqlite): "
                   "no S3, no state DB, no Photos album.")
@click.option("--state-db", type=click.Path(path_type=Path), default=None, help="Default: ~/.photoctl/state.sqlite")
def archive(bucket, profile, out, selected, albums, from_date, to_date, uuids, all_photos, limit, library,
            include_hidden, download_missing, live_video, videos, video_height, video_crf, storage_class, long_edge,
            quality, workers, mark_album, do_backup, dry_run, local_only, state_db):
    """Compress + upload originals from Apple Photos to S3."""
    from .catalog import open_catalog
    from .catalog.store import upsert_record
    from .device import load_device
    from .runlock import AlreadyRunning, IngestLock, RunLog

    if not (bucket or dry_run or local_only):
        raise click.UsageError("--bucket (or $PHOTOCTL_BUCKET) is required unless --local-only or --dry-run.")
    record_state = not (dry_run or local_only)
    device = load_device()
    lock = IngestLock()
    try:
        lock.__enter__()
    except AlreadyRunning as e:
        raise click.ClickException(str(e)) from None
    log = RunLog(lock.run_id)
    try:
        state = State(state_db)
        photos = _select_photos(library, uuids, selected, albums, from_date, to_date, include_hidden, videos,
                                all_photos, None)
        todo = [p for p in photos if not (record_state and state.is_done(f"applephotos:{p.uuid}"))]
        skipped = len(photos) - len(todo)
        if limit:
            todo = todo[:limit]
        n_videos = sum(1 for p in photos if p.ismovie)
        click.echo(f"{len(photos)} items matched ({len(photos) - n_videos} photos, {n_videos} videos) · "
                   f"{skipped} already archived · {len(todo)} to process")
        log.event("start", run_id=lock.run_id, device_id=device.device_id, matched=len(photos), todo=len(todo),
                  dry_run=dry_run, local_only=local_only)
        if not todo:
            log.event("end", counts={}, uploaded_bytes=0)
            return

        s3 = _bucket(bucket, profile) if record_state else None
        if s3:
            s3.client.head_bucket(Bucket=bucket)  # fail fast on auth/bucket problems
        catalog = None if dry_run else open_catalog(out / "catalog.sqlite" if local_only else None)

        kwargs = dict(out=out, bucket=s3, storage_class=storage_class, long_edge=long_edge, quality=quality,
                      video_height=video_height, video_crf=video_crf,
                      download_missing=download_missing, live_video=live_video, dry_run=dry_run)
        counts: dict[str, int] = {}
        total_bytes = 0
        archived_uuids: list[str] = []
        with ThreadPoolExecutor(max_workers=workers) as pool, \
                click.progressbar(length=len(todo), label="Archiving", show_pos=True) as bar:
            futures = [pool.submit(_process, p, **kwargs) for p in todo]
            for fut in as_completed(futures):
                r = fut.result()
                counts[r.status] = counts.get(r.status, 0) + 1
                total_bytes += r.uploaded_bytes
                if record_state:
                    state.upsert(r.source_ref, status=r.status, error=r.error, **r.fields)
                    if r.status in ("done", "no_derivative"):
                        archived_uuids.append(r.uuid)
                if catalog is not None and r.record is not None:
                    upsert_record(catalog, r.record, device, bytes_compressed=r.fields.get("bytes_compressed"))
                log.event("item", source_ref=r.source_ref, status=r.status, photo_id=r.fields.get("photo_id"),
                          error=r.error, uploaded_bytes=r.uploaded_bytes)
                if r.error and r.status != "done":
                    bar.label = f"Archiving (last issue: {r.status})"
                    click.echo(f"\n  {r.status}: {r.source_ref} — {r.error}", err=True)
                bar.update(1)

        verb = "would upload" if dry_run else "uploaded"
        click.echo("\nSummary: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
                   + f", already_archived={skipped}, {verb}={_human(total_bytes)}")
        log.event("end", counts=counts, uploaded_bytes=total_bytes)
        if not dry_run:
            click.echo(f"Compressed images + sidecars: {out.resolve()}")
        if mark_album and archived_uuids:
            try:
                _add_to_album(archived_uuids, ARCHIVE_ALBUM)
                click.echo(f'Added {len(archived_uuids)} photos to the "{ARCHIVE_ALBUM}" album in Photos.')
            except Exception as e:
                click.echo(f"Could not update Photos album ({e}); archive itself succeeded.", err=True)
        if catalog is not None:
            catalog.close()
        if s3:
            _run_sync(s3, device)
        if s3 and do_backup:
            state.db.close()
            _run_backup(s3, device)
    finally:
        log.close()
        lock.__exit__(None, None, None)


@main.command()
@click.option("--state-db", type=click.Path(path_type=Path), default=None, help="Default: ~/.photoctl/state.sqlite")
def status(state_db):
    """This device, archive counts and catalog size."""
    from .catalog import open_catalog
    from .catalog.store import stats
    from .device import load_device

    dev = load_device()
    click.echo(f"Device   {dev.name} · {dev.platform} · {dev.device_id}")
    counts = State(state_db).counts()
    click.echo("Archive  " + (", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "nothing archived yet"))
    s = stats(open_catalog())
    click.echo(f"Catalog  {s['photos']} photos, {s['videos']} videos, {s['people']} people, {s['tags']} tags, "
               f"{_human(s['bytes_raw'])} of originals")


@main.command()
@click.option("--bucket", envvar="PHOTOCTL_BUCKET", required=True)
@click.option("--profile", envvar="AWS_PROFILE", default=None)
@click.option("--sample", default=50, show_default=True)
@click.option("--state-db", type=click.Path(path_type=Path), default=None, help="Default: ~/.photoctl/state.sqlite")
def verify(bucket, profile, sample, state_db):
    """HEAD a random sample of archived originals and check size + sha256 metadata."""
    s3 = _bucket(bucket, profile)
    bad = 0
    rows = State(state_db).sample_done(sample)
    for row in rows:
        head = s3.head(row["raw_key"])
        if head is None:
            problem = "missing"
        elif head["ContentLength"] != row["bytes_raw"]:
            problem = "size mismatch"
        elif head.get("Metadata", {}).get("sha256") != row["sha256"]:
            problem = "sha256 mismatch"
        else:
            continue
        bad += 1
        click.echo(f"{problem}: {row['raw_key']} ({row['source_ref']})")
    click.echo(f"Checked {len(rows)} originals: {len(rows) - bad} ok, {bad} bad")
    if bad:
        raise SystemExit(1)


@main.command()
@click.option("--rename", help="Set a new display name for this device.")
def device(rename):
    """Show (or rename) this device's identity."""
    from .device import load_device, save_device

    dev = load_device()
    if rename:
        dev.name = rename
        save_device(dev)
    click.echo(json.dumps(dev.__dict__, indent=2))


@main.command()
@click.option("--bucket", envvar="PHOTOCTL_BUCKET", required=True)
@click.option("--profile", envvar="AWS_PROFILE", default=None)
@click.option("--watch", type=int, default=None, metavar="SECONDS", help="Keep syncing every N seconds.")
@click.option("--snapshot", "force_snapshot", is_flag=True, help="Also publish a fresh library snapshot.")
def sync(bucket, profile, watch, force_snapshot):
    """Send this device's changes to S3 and apply everyone else's."""
    import time

    from .device import load_device

    s3, dev = _bucket(bucket, profile), load_device()
    while True:
        _run_sync(s3, dev, quiet=bool(watch), force_snapshot=force_snapshot)
        if not watch:
            return
        time.sleep(watch)


@main.command()
@click.option("--bucket", envvar="PHOTOCTL_BUCKET", required=True)
@click.option("--profile", envvar="AWS_PROFILE", default=None)
def devices(bucket, profile):
    """Every device that has synced: platform, last seen, catalog size and checksum."""
    from .device import load_device
    from .sync import list_registry

    me = load_device().device_id
    entries = list_registry(_bucket(bucket, profile))
    if not entries:
        click.echo("No devices have synced yet. Run `photoctl sync`.")
    for d in entries:
        mark = " (this device)" if d["device_id"] == me else ""
        click.echo(f"{d['name']}{mark} · {d['platform']} · last seen {d['last_seen']} · {d['media']} items · "
                   f"{d['pending_ops']} unsent · checksum {d['checksum'][:12]}…")


# ---------------------------------------------------------------- catalog

@main.group()
def catalog():
    """The searchable SQLite catalog (~/.photoctl/catalog.sqlite)."""


def _catalog_path_option(f):
    return click.option("--catalog", "catalog_path", type=click.Path(path_type=Path), default=None,
                        help="Default: ~/.photoctl/catalog.sqlite")(f)


@catalog.command("init")
@_catalog_path_option
def catalog_init(catalog_path):
    """Create or migrate the catalog."""
    from .catalog import open_catalog, schema_version

    conn = open_catalog(catalog_path)
    click.echo(f"Catalog ready (schema v{schema_version(conn)}): {catalog_path or paths.catalog_db()}")


@catalog.command("stats")
@_catalog_path_option
def catalog_stats(catalog_path):
    """Counts of photos, videos, people, tags and devices."""
    from .catalog import open_catalog
    from .catalog.store import stats

    click.echo(json.dumps(stats(open_catalog(catalog_path)), indent=2))


@catalog.command("checksum")
@_catalog_path_option
def catalog_checksum(catalog_path):
    """Fingerprint of the synced state; equal on every device once they have converged."""
    from .catalog import open_catalog
    from .catalog.checksum import checksum

    click.echo(checksum(open_catalog(catalog_path)))


@catalog.command("backfill")
@_catalog_path_option
@click.option("--from-dir", type=click.Path(path_type=Path, exists=True), default=None,
              help="Read sidecars from <dir>/meta/** (default: ./library).")
@click.option("--from-s3", is_flag=True, help="Read sidecars from s3://<bucket>/meta/ instead.")
@click.option("--bucket", envvar="PHOTOCTL_BUCKET")
@click.option("--profile", envvar="AWS_PROFILE", default=None)
@click.option("--force", is_flag=True, help="Re-import records that are already in the catalog.")
def catalog_backfill(catalog_path, from_dir, from_s3, bucket, profile, force):
    """Build or refresh the catalog from existing JSON sidecars."""
    from .catalog import open_catalog
    from .catalog.store import load_record, upsert_record
    from .device import load_device

    conn, dev, n = open_catalog(catalog_path), load_device(), 0
    if from_s3:
        s3 = _bucket(bucket, profile)
        keys = [k for k in s3.list_keys("meta/") if k.endswith(".json")]
        with click.progressbar(keys, label="Backfilling from S3") as bar:
            for key in bar:
                if force or not _in_catalog(conn, key.rsplit("/", 1)[-1].removesuffix(".json"), dev):
                    upsert_record(conn, json.loads(s3.get_bytes(key)), dev)
                    n += 1
    else:
        root = (from_dir or Path("library")) / "meta"
        files = sorted(root.rglob("*.json")) if root.exists() else []
        if not files:
            raise click.ClickException(f"No sidecars found under {root}")
        with click.progressbar(files, label="Backfilling") as bar:
            for f in bar:
                if not force and _in_catalog(conn, f.stem, dev):
                    continue
                rec = load_record(f)
                comp = (from_dir or Path("library")) / ((rec.get("keys") or {}).get("compressed") or "")
                upsert_record(conn, rec, dev, bytes_compressed=comp.stat().st_size if comp.is_file() else None)
                n += 1
    click.echo(f"Added {n} records.")


@catalog.command("export")
@_catalog_path_option
@click.option("--flavour", type=click.Choice(["full", "contributed"]), default="full", show_default=True)
@click.option("--device", "device_id", help="Device whose contributions to keep (default: this device).")
@click.option("-o", "--output", type=click.Path(path_type=Path), required=True,
              help="Output file; ends in .gz → gzipped.")
def catalog_export(catalog_path, flavour, device_id, output):
    """Write a consistent, VACUUM'd copy of the catalog (all of it, or one device's contributions)."""
    from .catalog import open_catalog
    from .catalog.export import export_catalog
    from .device import load_device

    if flavour == "contributed" and not device_id:
        device_id = load_device().device_id
    out = export_catalog(open_catalog(catalog_path), output, flavour=flavour, device_id=device_id)
    click.echo(f"Wrote {out} ({_human(out.stat().st_size)})")


def _in_catalog(conn, photo_id: str, dev) -> bool:
    return conn.execute("SELECT 1 FROM media_sources WHERE photo_id = ? AND device_id = ?",
                        (photo_id, dev.device_id)).fetchone() is not None


# ---------------------------------------------------------------- thumbnails

@main.group()
def thumbs():
    """320px thumbnails for grids."""


@thumbs.command("backfill")
@click.option("--out", type=click.Path(path_type=Path), default=Path("library"), show_default=True)
@click.option("--bucket", envvar="PHOTOCTL_BUCKET", help="Also upload thumbnails to S3.")
@click.option("--profile", envvar="AWS_PROFILE", default=None)
@_catalog_path_option
def thumbs_backfill(out, bucket, profile, catalog_path):
    """Make thumbnails for archived items that don't have one yet."""
    from .catalog import open_catalog
    from .catalog.store import load_record, upsert_record
    from .device import load_device

    s3 = _bucket(bucket, profile) if bucket else None
    dev = load_device()
    conn = open_catalog(catalog_path)
    made = 0
    for meta in sorted((out / "meta").rglob("*.json")):
        rec = load_record(meta)
        keys = rec.get("keys") or {}
        src_key = keys.get("poster") or (keys.get("compressed") if rec.get("media_type") != "video" else None)
        if keys.get("thumb") or not src_key or not (out / src_key).exists():
            continue
        thumb_key = src_key.replace("compressed/", "thumb/", 1).rsplit(".", 1)[0] + ".jpg"
        make_thumb(out / src_key, out / thumb_key)
        rec["keys"]["thumb"] = thumb_key
        meta.write_text(json.dumps(rec, indent=2, ensure_ascii=False))
        if s3:
            s3.upload(out / thumb_key, thumb_key, content_type="image/jpeg")
            s3.upload(meta, keys["meta"], content_type="application/json")
        upsert_record(conn, rec, dev)
        made += 1
    click.echo(f"Made {made} thumbnails{' and uploaded them' if s3 and made else ''}.")


# ---------------------------------------------------------------- database backups

@main.group()
def db():
    """Back up / restore this device's databases (state, catalog, identity) to S3."""


@db.command("backup")
@click.option("--bucket", envvar="PHOTOCTL_BUCKET", required=True)
@click.option("--profile", envvar="AWS_PROFILE", default=None)
def db_backup(bucket, profile):
    """Back up now."""
    from .backup import backup
    from .device import load_device

    res = backup(_bucket(bucket, profile), load_device())
    for name, entry in res.files.items():
        click.echo(f"  {name:22} {_human(entry['bytes']):>10}  sha256 {entry['sha256'][:12]}…")
    click.echo(f"Backed up to s3://{bucket}/{res.prefix}" + (f" (also kept as {res.monthly})" if res.monthly else ""))


@db.command("list")
@click.option("--bucket", envvar="PHOTOCTL_BUCKET", required=True)
@click.option("--profile", envvar="AWS_PROFILE", default=None)
@click.option("--device", "device_id", help="Default: this device. Use `all` to list devices with backups.")
def db_list(bucket, profile, device_id):
    """List backups, newest first."""
    from .backup import list_backups, list_devices
    from .device import load_device

    s3 = _bucket(bucket, profile)
    if device_id == "all":
        for d in list_devices(s3):
            latest = list_backups(s3, d)[:1]
            click.echo(f"{d}  {latest[0]['device_name'] if latest else '?'}  "
                       f"last backup {latest[0]['created_at'] if latest else 'none'}")
        return
    for b in list_backups(s3, device_id or load_device().device_id):
        size = sum(f["bytes"] for f in b["files"].values())
        click.echo(f"{b['created_at']}  {_human(size):>10}  {', '.join(b['files'])}")


@db.command("restore")
@click.option("--bucket", envvar="PHOTOCTL_BUCKET", required=True)
@click.option("--profile", envvar="AWS_PROFILE", default=None)
@click.option("--device", "device_id", help="Device to restore from (default: this device).")
@click.option("--at", type=click.DateTime(["%Y-%m-%d", "%Y-%m-%dT%H:%M"]), help="Latest backup at or before this time.")
@click.option("--with-identity", is_flag=True, help="Also restore device.json/config.toml (carry on as that device).")
@click.confirmation_option(prompt="Replace the local databases with the backup? (current files are kept as .bak)")
def db_restore(bucket, profile, device_id, at, with_identity):
    """Restore databases from S3. Current files are kept next to them as .bak."""
    from .backup import restore
    from .device import load_device
    from .runlock import AlreadyRunning, IngestLock

    local = load_device(create=False)
    if not device_id:
        if local is None:
            raise click.UsageError("No local device identity. Pass --device <id> (see `photoctl db list --device all`).")
        device_id = local.device_id
    if local is None:
        with_identity = True  # a fresh machine carries on as the restored device
    try:
        with IngestLock():
            res = restore(_bucket(bucket, profile), device_id, at=at, with_identity=with_identity)
    except AlreadyRunning as e:
        raise click.ClickException(f"{e}. Wait for it to finish, then restore.") from None
    click.echo(f"Restored {', '.join(res['restored'])} from {res['created_at']} ({res['backup']})")


if __name__ == "__main__":
    main()
