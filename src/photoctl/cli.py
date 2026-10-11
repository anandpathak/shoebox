"""photoctl — archive Apple Photos originals to S3 and keep small, metadata-rich copies locally.

For every photo (videos: compressed/…/<id>.mp4 at 720p via ffmpeg + a compressed/…/<id>.jpg poster frame):
  1. hash the original  -> photo_id (content-addressed)
  2. compress with sips -> <out>/compressed/YYYY/YYYY-MM/<photo_id>.jpg   (EXIF + GPS kept)
  3. write sidecar      -> <out>/meta/YYYY/YYYY-MM/<photo_id>.json        (Apple Photos metadata)
  4. upload original    -> s3://<bucket>/raw/YYYY/YYYY-MM/<photo_id>.<ext>   (Deep Archive)
     + compressed/meta  -> s3://<bucket>/compressed/... and meta/...
  5. add to a "Archived to S3" album in Photos so you can review/delete from the library.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import click

from .derive import compress, compress_video, photo_id_from_sha256, sha256_file, video_poster
from .state import DEFAULT_DB, State

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
            else:
                compress(src, comp_path, long_edge=long_edge, quality=quality)
                derived.append((comp_path, comp_key, "image/jpeg"))
        except Exception as e:  # unknown/undecodable type: still archive the original
            status, error = "no_derivative", str(e)
        derived_keys = {k for _, k, _ in derived}

        record = {
            "schema_version": 1,
            "photo_id": pid,
            "media_type": "video" if is_video else "photo",
            "keys": {"raw": raw_key, "compressed": comp_key if comp_key in derived_keys else None,
                     "poster": poster_key if poster_key in derived_keys else None, "meta": meta_key,
                     "companions": [k for _, k in extras]},
            "file": {"sha256": sha, "orig_name": photo.original_filename, "orig_ext": ext, "bytes_raw": size,
                     "width": photo.width, "height": photo.height, "uti": photo.uti_original},
            "taken_at": photo.date.isoformat() if photo.date else None,
            "source": {"type": "applephotos", "ref": photo.uuid},
            "apple": json.loads(photo.json()),  # persons, albums, keywords, title, place, labels, …
        }
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        meta_path.write_text(json.dumps(record, indent=2, ensure_ascii=False))

        s3_meta = {"sha256": sha, "orig-name": photo.original_filename or src.name,
                   "source": ref, "taken-at": record["taken_at"] or ""}
        uploaded = 0
        if bucket is None:  # --local-only
            return Result(ref, status, uuid=photo.uuid, error=error)
        if bucket.head(raw_key) is None:  # content-addressed: same key means same bytes
            bucket.upload(src, raw_key, storage_class=storage_class, metadata=s3_meta)
            uploaded += size
        for p, key in extras:
            if bucket.head(key) is None:
                bucket.upload(p, key, storage_class=storage_class, metadata=s3_meta)
                uploaded += p.stat().st_size
        for path, key, content_type in derived:
            bucket.upload(path, key, content_type=content_type, metadata=s3_meta)
            uploaded += path.stat().st_size
        bucket.upload(meta_path, meta_key, content_type="application/json")

        return Result(ref, status, uuid=photo.uuid, uploaded_bytes=uploaded, error=error, fields={
            "photo_id": pid, "sha256": sha, "raw_key": raw_key,
            "compressed_key": record["keys"]["compressed"], "meta_key": meta_key, "bytes_raw": size,
            "bytes_compressed": comp_path.stat().st_size if comp_key in derived_keys else None,
        })
    except Exception as e:
        return Result(ref, "failed", uuid=photo.uuid, error=f"{type(e).__name__}: {e}")
    finally:
        if tmpdir:
            shutil.rmtree(tmpdir, ignore_errors=True)


def _add_to_album(uuids: list[str], name: str) -> None:
    import photoscript

    lib = photoscript.PhotosLibrary()
    album = lib.album(name) or lib.create_album(name)
    for i in range(0, len(uuids), 200):
        album.add([photoscript.Photo(u) for u in uuids[i:i + 200]])


@click.group()
def main():
    """Archive Apple Photos originals to S3; keep a small JPEG with full metadata for processing."""


@main.command()
@click.option("--bucket", envvar="PHOTOCTL_BUCKET", help="S3 bucket (or $PHOTOCTL_BUCKET).")
@click.option("--profile", envvar="AWS_PROFILE", default=None, help="AWS profile (or $AWS_PROFILE).")
@click.option("--out", type=click.Path(path_type=Path), default=Path("library"), show_default=True,
              help="Local dir for compressed JPEGs and JSON sidecars.")
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
@click.option("--dry-run", is_flag=True, help="Show what would be archived; touch nothing.")
@click.option("--local-only", is_flag=True,
              help="Only compress + write sidecars locally: no S3, no state DB, no Photos album.")
@click.option("--state-db", type=click.Path(path_type=Path), default=DEFAULT_DB, show_default=True)
def archive(bucket, profile, out, selected, albums, from_date, to_date, uuids, all_photos, limit, library,
            include_hidden, download_missing, live_video, videos, video_height, video_crf, storage_class, long_edge,
            quality, workers, mark_album, dry_run, local_only, state_db):
    """Compress + upload originals from Apple Photos to S3."""
    from .s3 import Bucket

    if not (bucket or dry_run or local_only):
        raise click.UsageError("--bucket (or $PHOTOCTL_BUCKET) is required unless --local-only or --dry-run.")
    record_state = not (dry_run or local_only)
    state = State(state_db)
    photos = _select_photos(library, uuids, selected, albums, from_date, to_date, include_hidden, videos,
                            all_photos, None)
    todo = [p for p in photos if not state.is_done(f"applephotos:{p.uuid}")]
    skipped = len(photos) - len(todo)
    if limit:
        todo = todo[:limit]
    n_videos = sum(1 for p in photos if p.ismovie)
    click.echo(f"{len(photos)} items matched ({len(photos) - n_videos} photos, {n_videos} videos) · "
               f"{skipped} already archived · {len(todo)} to process")
    if not todo:
        return

    s3 = Bucket(bucket, profile) if record_state else None
    if s3:
        s3.client.head_bucket(Bucket=bucket)  # fail fast on auth/bucket problems

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
            if r.error and r.status != "done":
                bar.label = f"Archiving (last issue: {r.status})"
                click.echo(f"\n  {r.status}: {r.source_ref} — {r.error}", err=True)
            bar.update(1)

    verb = "would upload" if dry_run else "uploaded"
    click.echo("\nSummary: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
               + f", already_archived={skipped}, {verb}={_human(total_bytes)}")
    if not dry_run:
        click.echo(f"Compressed images + sidecars: {out.resolve()}")
    if mark_album and archived_uuids:
        try:
            _add_to_album(archived_uuids, ARCHIVE_ALBUM)
            click.echo(f'Added {len(archived_uuids)} photos to the "{ARCHIVE_ALBUM}" album in Photos.')
        except Exception as e:
            click.echo(f"Could not update Photos album ({e}); archive itself succeeded.", err=True)


@main.command()
@click.option("--state-db", type=click.Path(path_type=Path), default=DEFAULT_DB, show_default=True)
def status(state_db):
    """Counts by status from the local state DB."""
    counts = State(state_db).counts()
    if not counts:
        click.echo("Nothing archived yet.")
    for k, v in sorted(counts.items()):
        click.echo(f"{k:18} {v}")


@main.command()
@click.option("--bucket", envvar="PHOTOCTL_BUCKET", required=True)
@click.option("--profile", envvar="AWS_PROFILE", default=None)
@click.option("--sample", default=50, show_default=True)
@click.option("--state-db", type=click.Path(path_type=Path), default=DEFAULT_DB, show_default=True)
def verify(bucket, profile, sample, state_db):
    """HEAD a random sample of archived originals and check size + sha256 metadata."""
    from .s3 import Bucket

    s3 = Bucket(bucket, profile)
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


if __name__ == "__main__":
    main()
