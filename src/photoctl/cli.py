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
from datetime import datetime
from pathlib import Path

import click

from . import paths
from .archiver import ARCHIVE_ALBUM
from .derive import make_thumb
from .state import State


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


def _run_sync(s3, device, quiet: bool = False, force_snapshot: bool = False) -> None:
    from .runlock import AlreadyRunning
    from .sync import full_sync

    try:
        r = full_sync(s3, device, force_snapshot=force_snapshot)
    except AlreadyRunning:
        click.echo("A sync is already running; skipped.", err=True)
        return
    except Exception as e:
        click.echo(f"Sync failed ({e}); run `photoctl sync` to retry.", err=True)
        return
    if r.bootstrapped_from:
        h = r.bootstrapped_from
        click.echo(f"Started from the library snapshot of {h['created_at']} ({h['media']} items).")
    if r.snapshot and not quiet:
        click.echo(f"Published a library snapshot ({r.snapshot['media']} items).")
    if r.published and not quiet:
        click.echo(f"Published this device's catalog ({r.published['item_count']} items, "
                   f"{_human(r.published['bytes'])}).")
    if not quiet or r.pushed_ops or r.applied_ops:
        click.echo(f"Synced: pushed {r.pushed_ops} ops in {r.pushed_batches} batches, "
                   f"applied {r.applied_ops} new ops from {r.pulled_batches} batches"
                   + (f", skipped {len(r.rejected_batches)} bad batches" if r.rejected_batches else ""))


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
    from .archiver import Options, Selection, SelectionError, run_archive
    from .runlock import AlreadyRunning

    if not (bucket or dry_run or local_only):
        raise click.UsageError("--bucket (or $PHOTOCTL_BUCKET) is required unless --local-only or --dry-run.")
    record_state = not (dry_run or local_only)
    sel = Selection(selected=selected, albums=tuple(albums), from_date=from_date, to_date=to_date, uuids=tuple(uuids),
                    all=all_photos, limit=limit, include_hidden=include_hidden, include_videos=videos, library=library)
    opts = Options(out=out, bucket=_bucket(bucket, profile) if record_state else None, storage_class=storage_class,
                   long_edge=long_edge, quality=quality, video_height=video_height, video_crf=video_crf,
                   download_missing=download_missing, live_video=live_video, workers=workers, mark_album=mark_album,
                   backup=do_backup, dry_run=dry_run, local_only=local_only, state_db=state_db,
                   catalog_path=Path(out) / "catalog.sqlite" if local_only else None)
    bar = None

    def on_event(kind, **d):
        nonlocal bar
        if kind == "start":
            click.echo(f"{d['matched']} items matched ({d['matched'] - d['videos']} photos, {d['videos']} videos) · "
                       f"{d['skipped']} already archived · {d['todo']} to process")
            if d["todo"]:
                bar = click.progressbar(length=d["todo"], label="Archiving", show_pos=True)
                bar.__enter__()
        elif kind == "item":
            if d["error"] and d["status"] != "done":
                click.echo(f"\n  {d['status']}: {d['source_ref']} — {d['error']}", err=True)
            if bar:
                bar.update(1)
        elif kind == "end":
            if bar:
                bar.__exit__(None, None, None)
            if d["counts"]:
                verb = "would upload" if dry_run else "uploaded"
                click.echo("\nSummary: " + ", ".join(f"{k}={v}" for k, v in sorted(d["counts"].items()))
                           + f", {verb}={_human(d['uploaded_bytes'])}")
                if not dry_run:
                    click.echo(f"Compressed images + sidecars: {Path(out).resolve()}")
        elif kind == "message":
            click.echo(d["text"])

    try:
        run_archive(None, opts, selection=sel, on_event=on_event)
    except AlreadyRunning as e:
        raise click.ClickException(str(e)) from None
    except SelectionError as e:
        raise click.ClickException(str(e)) from None


def _backup_age_line(now: datetime | None = None) -> str:
    from datetime import timezone

    from .backup import last_local_backup

    last = last_local_backup()
    if not last:
        return "never backed up — run `photoctl db backup`"
    age = (now or datetime.now(timezone.utc)) - datetime.fromisoformat(last["created_at"])
    hours = age.total_seconds() / 3600
    when = f"{int(hours * 60)} min ago" if hours < 1 else f"{hours:.0f} h ago" if hours < 48 else f"{hours / 24:.0f} days ago"
    line = f"last backup {when} (s3://{last['bucket']}/{last['prefix']})"
    return line + " — older than 48 h, run `photoctl db backup`" if hours >= 48 else line


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
    click.echo("Backup   " + _backup_age_line())


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


@main.group(invoke_without_command=True)
@click.option("--bucket", envvar="PHOTOCTL_BUCKET", required=True)
@click.option("--profile", envvar="AWS_PROFILE", default=None)
@click.option("--watch", type=int, default=None, metavar="SECONDS", help="Keep syncing every N seconds.")
@click.option("--snapshot", "force_snapshot", is_flag=True, help="Also publish a fresh library snapshot.")
@click.pass_context
def sync(ctx, bucket, profile, watch, force_snapshot):
    """Send this device's changes to S3 and apply everyone else's."""
    import time

    from .device import load_device

    s3, dev = _bucket(bucket, profile), load_device()
    ctx.obj = {"s3": s3, "device": dev}
    if ctx.invoked_subcommand:
        return
    while True:
        _run_sync(s3, dev, quiet=bool(watch), force_snapshot=force_snapshot)
        if not watch:
            return
        time.sleep(watch)


@sync.command("verify")
@click.pass_obj
def sync_verify(obj):
    """Sync, then check that every device that has synced holds the same library."""
    from .catalog import open_catalog
    from .sync import verify

    _run_sync(obj["s3"], obj["device"], quiet=True)
    report = verify(open_catalog(), obj["s3"], obj["device"])
    for d in report["different"]:
        hint = f"{d['pending_ops']} unsent changes" if d.get("pending_ops") else "hasn't synced since the last change"
        click.echo(f"differs: {d['name']} ({d['platform']}), last seen {d['last_seen']} — {hint}")
    if report["converged"]:
        click.echo(f"converged: {1 + len(report['same'])} devices share checksum {report['checksum'][:12]}…")
    else:
        click.echo("not converged yet: sync the devices listed above, then verify again.")
        raise SystemExit(1)


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


@main.command()
@click.argument("text", required=False)
@click.option("--person")
@click.option("--place")
@click.option("--tag")
@click.option("--album")
@click.option("--from", "date_from", help="Taken on/after YYYY-MM-DD.")
@click.option("--to", "date_to", help="Taken before YYYY-MM-DD.")
@click.option("--type", "media_type", type=click.Choice(["photo", "video"]))
@click.option("--fav", "favorite", is_flag=True, default=None, help="Favourites only.")
@click.option("--device", help="Device id, name or platform (ios, android, macos).")
@click.option("--hidden", is_flag=True, help="Include hidden items.")
@click.option("--limit", default=50, show_default=True)
@click.option("--catalog", "catalogs", multiple=True, type=click.Path(path_type=Path, exists=True),
              help="Search these catalog files instead (repeatable; results are merged).")
@click.option("--out", type=click.Path(path_type=Path), default=Path("library"), show_default=True,
              help="Where local copies live, to show their paths.")
@click.option("--json", "as_json", is_flag=True, help="Print JSON.")
def search(text, person, place, tag, album, date_from, date_to, media_type, favorite, device, hidden, limit,
           catalogs, out, as_json):
    """Search the library: words, places, people, dates ("beach december 2024"), type and device."""
    from .catalog import open_catalog
    from .catalog.query import search as run_search
    from .catalog.query import search_catalogs

    filters = dict(person=person, place=place, tag=tag, album=album, date_from=date_from, date_to=date_to,
                   media_type=media_type, favorite=favorite, device=device, include_hidden=hidden)
    res = (search_catalogs(list(catalogs), text, limit=limit, **filters) if catalogs
           else run_search(open_catalog(), text, limit=limit, **filters))
    for item in res["items"]:
        local = out / (item["compressed_key"] or "")
        item["local_path"] = str(local) if item["compressed_key"] and local.exists() else None
    if as_json:
        click.echo(json.dumps(res["items"], indent=2, ensure_ascii=False, default=str))
        return
    if not res["items"]:
        click.echo("No matches.")
        return
    for i in res["items"]:
        where = ", ".join(filter(None, (i["place_city"] or i["place_name"], i["place_country"])))
        kind = "video" if i["media_type"] == "video" else "photo"
        devs = f"{len(i['devices'])} devices" if len(i["devices"]) > 1 else ""
        click.echo(f"{(i['taken_at'] or '?')[:10]}  {kind:5}  {i['photo_id']}  {(i['title'] or where or '-')[:40]:40}"
                   f"  {devs:9}  {i['local_path'] or ''}")
    if res.get("next"):
        click.echo("… more results: use --limit to see more.")


@main.command()
@click.argument("photo_id")
@click.option("--kind", type=click.Choice(["compressed", "thumb", "poster", "meta"]), default="compressed",
              show_default=True)
@click.option("--url", "as_url", is_flag=True, help="Print a 15-minute download link instead of downloading.")
@click.option("-o", "--output", "dest", type=click.Path(path_type=Path), default=Path("."), show_default=True)
@click.option("--out", "library", type=click.Path(path_type=Path), default=Path("library"), show_default=True,
              help="Local library to look in first.")
@click.option("--bucket", envvar="PHOTOCTL_BUCKET")
@click.option("--profile", envvar="AWS_PROFILE", default=None)
def get(photo_id, kind, as_url, dest, library, bucket, profile):
    """Fetch an item's compressed copy, thumbnail, poster or sidecar (local first, then S3)."""
    from .catalog import open_catalog
    from .catalog.query import media_detail
    from .fetch import fetch, key_for, presign

    item = media_detail(open_catalog(), photo_id)
    if item is None:
        raise click.ClickException(f"{photo_id} isn't in the catalog")
    s3 = _bucket(bucket, profile) if bucket else None
    if as_url:
        if s3 is None:
            raise click.UsageError("--url needs --bucket")
        key = key_for(item, kind)
        if not key:
            raise click.ClickException(f"{photo_id} has no {kind} copy")
        click.echo(presign(s3, key, filename=Path(key).name))
        return
    try:
        click.echo(fetch(item, kind, dest, library=library, bucket=s3))
    except LookupError as e:
        raise click.ClickException(str(e)) from None


@main.command()
@click.argument("photo_id")
@click.option("--tier", type=click.Choice(["standard", "bulk"]), default="standard", show_default=True,
              help="standard: up to 12 h; bulk: up to 48 h and cheaper.")
@click.option("--days", default=7, show_default=True, help="How long the restored copy stays downloadable.")
@click.option("--status", "check", is_flag=True, help="Only check how the restore is going.")
@click.option("--download", "dest", type=click.Path(path_type=Path), help="Download it here once it's ready.")
@click.option("--bucket", envvar="PHOTOCTL_BUCKET", required=True)
@click.option("--profile", envvar="AWS_PROFILE", default=None)
def original(photo_id, tier, days, check, dest, bucket, profile):
    """Get an original back from Deep Archive: request it, check on it, then download it."""
    from .catalog import open_catalog
    from .originals import eta_hours, request, status

    s3, conn = _bucket(bucket, profile), open_catalog()
    try:
        info = status(s3, conn, photo_id) if (check or dest) else request(s3, conn, photo_id, tier=tier, days=days)
    except LookupError as e:
        raise click.ClickException(str(e)) from None
    st = info["status"]
    if dest and st in ("available", "ready"):
        target = Path(dest) / Path(info["raw_key"]).name
        s3.download(info["raw_key"], target)
        click.echo(f"Downloaded {target}")
    elif st == "in_progress":
        click.echo(f"Restoring from Deep Archive ({info['tier']} tier): ready within about "
                   f"{eta_hours(info['tier'].lower())} hours of {info['requested_at']}. Check with --status.")
    elif st == "ready":
        click.echo(f"Ready to download until {info['ready_until']}. Use --download DIR.")
    elif st == "available":
        click.echo("This original isn't in Deep Archive, so it can be downloaded now. Use --download DIR.")
    elif st == "expired":
        click.echo(f"The restored copy expired on {info['ready_until']}. Request it again.")
    else:
        click.echo("Not requested yet. Run without --status to request it.")


@main.command()
@click.option("--port", default=8765, show_default=True)
@click.option("--host", default="127.0.0.1", show_default=True,
              help="Keep 127.0.0.1; reach it from phones with `tailscale serve`, never by opening a port.")
@click.option("--out", "library", type=click.Path(path_type=Path), default=Path("library"), show_default=True)
@click.option("--bucket", envvar="PHOTOCTL_BUCKET")
@click.option("--profile", envvar="AWS_PROFILE", default=None)
@click.option("--print-token", is_flag=True, help="Print the API token (for scripts) and exit.")
def serve(port, host, library, bucket, profile, print_token):
    """Run the web app and API on this Mac."""
    import uvicorn

    from .server.app import create_app
    from .server.auth import get_or_create_token
    from .server.config import Settings

    if print_token:
        click.echo(get_or_create_token())
        return
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise click.UsageError("photoctl only listens on this Mac. Use `tailscale serve` to reach it from phones.")
    web = Path(__file__).parent / "web" / "dist"
    app = create_app(Settings.load(library=library, bucket=bucket, profile=profile, port=port,
                                   static_dir=web if web.exists() else None))
    click.echo(f"photoctl is running at http://127.0.0.1:{port}  (Ctrl-C to stop)")
    uvicorn.run(app, host=host, port=port, log_level="warning")


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


@catalog.command("rebuild")
@click.option("--bucket", envvar="PHOTOCTL_BUCKET", required=True)
@click.option("--profile", envvar="AWS_PROFILE", default=None)
@click.option("--from-meta", is_flag=True, help="Ignore snapshots and ops; rebuild only from meta/ sidecars.")
@click.confirmation_option(prompt="Replace the local catalog with one rebuilt from S3? (the current file is kept as .bak)")
def catalog_rebuild(bucket, profile, from_meta):
    """Rebuild the catalog from S3: the latest snapshot plus newer ops, or every sidecar in meta/."""
    import os

    from .catalog import open_catalog
    from .catalog.store import upsert_record
    from .device import load_device
    from .runlock import IngestLock, SyncLock

    s3, dev, path = _bucket(bucket, profile), load_device(), paths.catalog_db()
    with IngestLock(), SyncLock():
        for suffix in ("", "-wal", "-shm"):
            f = Path(str(path) + suffix)
            if f.exists():
                os.replace(f, Path(str(f) + ".bak"))
        if from_meta:
            conn, n = open_catalog(), 0
            for key in s3.list_keys("meta/"):
                if key.endswith(".json"):
                    upsert_record(conn, json.loads(s3.get_bytes(key)), dev)
                    n += 1
            conn.close()
            click.echo(f"Rebuilt {n} records from meta/ sidecars.")
    if not from_meta:
        _run_sync(s3, dev)  # bootstraps from the snapshot (or replays all ops), then catches up
    from .catalog.store import stats

    s = stats(open_catalog())
    click.echo(f"Catalog now has {s['photos']} photos and {s['videos']} videos.")


@catalog.command("download")
@click.option("--bucket", envvar="PHOTOCTL_BUCKET", required=True)
@click.option("--profile", envvar="AWS_PROFILE", default=None)
@click.option("--device", "which", default="all", show_default=True,
              help="A device id, `all` (every device's catalog) or `library` (the merged snapshot).")
@click.option("--flavour", type=click.Choice(["full", "contributed"]), default="full", show_default=True)
@click.option("-o", "--output", "dest", type=click.Path(path_type=Path), default=Path("catalogs"), show_default=True)
def catalog_download(bucket, profile, which, flavour, dest):
    """Download published catalogs (per device or the whole library) as .sqlite files."""
    from .publish import download_catalog, list_catalogs

    s3 = _bucket(bucket, profile)
    names = {m["device_id"]: f"{m['name']} ({m['platform']})" for m in list_catalogs(s3)}
    for path in download_catalog(s3, which, dest, flavour=flavour):
        label = names.get(path.name.split(".")[0], path.stem)
        click.echo(f"{path}  {_human(path.stat().st_size):>10}  {label}")


@catalog.command("list-published")
@click.option("--bucket", envvar="PHOTOCTL_BUCKET", required=True)
@click.option("--profile", envvar="AWS_PROFILE", default=None)
def catalog_list_published(bucket, profile):
    """Catalogs that devices have published, newest first."""
    from .publish import list_catalogs

    for m in list_catalogs(_bucket(bucket, profile)):
        click.echo(f"{m['device_id']}  {m['name']} · {m['platform']} · {m['item_count']} items "
                   f"({m['contributed_count']} from this device) · {_human(m['bytes'])} · {m['created_at']}")


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


@main.group()
def state():
    """This Mac's archive state (~/.photoctl/state.sqlite)."""


@state.command("rebuild")
@click.option("--bucket", envvar="PHOTOCTL_BUCKET", required=True)
@click.option("--profile", envvar="AWS_PROFILE", default=None)
def state_rebuild(bucket, profile):
    """Recreate which Apple Photos items are archived from the sidecars in S3 (no backup needed)."""
    from .backup import rebuild_state_from_s3
    from .device import load_device
    from .runlock import IngestLock

    with IngestLock():
        n = rebuild_state_from_s3(_bucket(bucket, profile), load_device())
    click.echo(f"Marked {n} Apple Photos items as archived.")


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


@db.group("schedule")
def db_schedule():
    """Daily automatic backups (macOS LaunchAgent)."""


@db_schedule.command("install")
@click.option("--bucket", envvar="PHOTOCTL_BUCKET", required=True)
@click.option("--profile", envvar="AWS_PROFILE", default=None)
@click.option("--at", "at_time", default="03:30", show_default=True, help="Local time, HH:MM.")
def db_schedule_install(bucket, profile, at_time):
    """Back up every day at the given time (and still after every archive run)."""
    from .schedule import install

    hour, minute = (int(x) for x in at_time.split(":"))
    path = install(bucket, hour=hour, minute=minute, profile=profile,
                   load=not bool(__import__("os").environ.get("PHOTOCTL_LAUNCHAGENTS_DIR")))
    click.echo(f"Daily backup at {at_time} installed: {path}")
    click.echo("It needs AWS credentials that are still valid at that time (an SSO profile that refreshes, "
               "or an IAM user with the ingest policy). An expired `aws login` session makes the backup fail; "
               "check ~/.photoctl/logs/backup.err.log.")


@db_schedule.command("uninstall")
def db_schedule_uninstall():
    """Stop the daily backup."""
    from .schedule import uninstall

    removed = uninstall(load=not bool(__import__("os").environ.get("PHOTOCTL_LAUNCHAGENTS_DIR")))
    click.echo("Daily backup removed." if removed else "No daily backup was installed.")


@main.command()
@click.option("--bucket", envvar="PHOTOCTL_BUCKET", required=True)
@click.option("--profile", envvar="AWS_PROFILE", default=None)
@click.option("--device", "device_id", help="Device to carry on as (default: choose from a list).")
@click.option("--yes", is_flag=True, help="Don't ask for confirmation.")
def recover(bucket, profile, device_id, yes):
    """Set up this Mac as a device that was lost or wiped: identity, databases, then catch up."""
    from .backup import list_backups, list_devices, restore
    from .device import Device, load_device, save_device
    from .runlock import IngestLock
    from .sync import list_registry

    local = load_device(create=False)
    if local is not None and not yes:
        click.confirm(f"This Mac is already set up as {local.name} ({local.device_id}). Replace it?", abort=True)
    s3 = _bucket(bucket, profile)
    registry = {d["device_id"]: d for d in list_registry(s3)}
    candidates = sorted(set(registry) | set(list_devices(s3)))
    if not candidates:
        raise click.ClickException("No devices have synced or been backed up to this bucket yet.")
    if not device_id:
        for i, d in enumerate(candidates, 1):
            info = registry.get(d, {})
            backups = list_backups(s3, d)
            click.echo(f"  {i}. {info.get('name', '?')} · {info.get('platform', '?')} · last seen "
                       f"{info.get('last_seen', '?')} · last backup {backups[0]['created_at'] if backups else 'none'}")
        device_id = candidates[click.prompt("Which device is this Mac replacing?", type=click.IntRange(1, len(candidates))) - 1]
    with IngestLock():
        if list_backups(s3, device_id):
            res = restore(s3, device_id, with_identity=True)
            click.echo(f"Restored {', '.join(res['restored'])} from the backup of {res['created_at']}.")
        else:
            info = registry.get(device_id) or {}
            save_device(Device(device_id, info.get("name", "Recovered Mac"), info.get("platform", "macos"),
                               info.get("last_seen", "")))
            for f in (paths.catalog_db(), paths.state_db()):
                f.unlink(missing_ok=True)
            click.echo("No database backup for that device: starting from the library snapshot instead.")
    dev = load_device()
    _run_sync(s3, dev)
    if not paths.state_db().exists() or not State().counts():
        click.echo("Rebuilding the archive state from S3…")
        from .backup import rebuild_state_from_s3

        n = rebuild_state_from_s3(s3, dev)
        click.echo(f"Marked {n} Apple Photos items as archived.")
    click.echo(f"This Mac is now {dev.name} ({dev.device_id}). Run `photoctl archive` as usual; "
               "items already in S3 are skipped.")


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
    s3 = _bucket(bucket, profile)
    try:
        with IngestLock():
            res = restore(s3, device_id, at=at, with_identity=with_identity)
    except AlreadyRunning as e:
        raise click.ClickException(f"{e}. Wait for it to finish, then restore.") from None
    click.echo(f"Restored {', '.join(res['restored'])} from {res['created_at']} ({res['backup']})")
    if "catalog.sqlite" in res["restored"]:
        click.echo("Catching up on changes made since that backup…")
        _run_sync(s3, load_device())


if __name__ == "__main__":
    main()
