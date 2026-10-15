"""Back up this device's databases to S3 and restore them (LOCAL_APP_PLAN.md §12).

Layout:
  backups/<device_id>/<YYYY-MM-DDTHH-MM-SSZ>/{state,catalog,faces}.sqlite.gz, device.json, config.toml, manifest.json

faces.sqlite (face fingerprints and which face is whom) is included so a replacement Mac keeps recognising
people. It's biometric data: it goes only to your own private, encrypted bucket, and `[backup] faces = false`
in config.toml leaves it out.
  backups/<device_id>/monthly/<YYYY-MM>/...   first backup of each month (kept longer by lifecycle)
  backups/<device_id>/LATEST.json
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from . import paths
from .catalog.export import integrity_ok, snapshot
from .device import Device

DATABASES = ("state", "catalog", "faces")
PRIVATE = {"faces"}            # restored readable by this user only
DAILY, MONTHLY = "retention=daily", "retention=monthly"  # matched by the lifecycle rules in infra/main.tf


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _shoebox_version() -> str:
    try:
        return version("shoebox")
    except PackageNotFoundError:
        return "dev"


def _db_path(name: str) -> Path:
    return {"state": paths.state_db(), "catalog": paths.catalog_db(), "faces": paths.faces_db()}[name]


def back_up_faces() -> bool:
    import tomllib

    cfg = paths.config_file()
    data = tomllib.loads(cfg.read_text()).get("backup", {}) if cfg.exists() else {}
    return bool(data.get("faces", True))


def _local_files() -> dict[str, Path]:
    """Name in the backup → local path, for everything that exists right now."""
    dbs = [db for db in DATABASES if db != "faces" or back_up_faces()]
    files = {f"{db}.sqlite.gz": _db_path(db) for db in dbs}
    files["device.json"] = paths.device_file()
    files["config.toml"] = paths.config_file()
    return {k: v for k, v in files.items() if v.exists()}


@dataclass
class BackupResult:
    prefix: str
    files: dict
    monthly: str | None


def backup(bucket, device: Device, *, now: datetime | None = None) -> BackupResult:
    now = now or datetime.now(timezone.utc)
    stamp = now.strftime("%Y-%m-%dT%H-%M-%SZ")
    root = f"backups/{device.device_id}/"
    prefix = f"{root}{stamp}/"
    manifest = {"created_at": now.isoformat(timespec="seconds"), "device_id": device.device_id,
                "device_name": device.name, "shoebox_version": _shoebox_version(), "files": {}}
    with tempfile.TemporaryDirectory(prefix="shoebox-backup-") as tmp:
        for name, src in _local_files().items():
            if name.endswith(".sqlite.gz"):
                raw = Path(tmp) / name.removesuffix(".gz")
                conn = sqlite3.connect(src, timeout=10)
                try:
                    snapshot(conn, raw)  # consistent even while an archive run is writing
                finally:
                    conn.close()
                ok, msg = integrity_ok(raw)
                if not ok:
                    raise RuntimeError(f"{src.name} failed integrity_check before backup: {msg}")
                if name.startswith("catalog"):
                    manifest["catalog"] = _catalog_info(raw, device.device_id)
                out = Path(tmp) / name
                with open(raw, "rb") as f_in, gzip.open(out, "wb", compresslevel=6) as f_out:
                    shutil.copyfileobj(f_in, f_out)
                entry = {"sha256_db": _sha256(raw), "bytes_db": raw.stat().st_size}
            else:
                out = src
                entry = {}
            entry |= {"key": prefix + name, "sha256": _sha256(out), "bytes": out.stat().st_size}
            bucket.upload(out, prefix + name, content_type="application/octet-stream",
                          metadata={"sha256": entry["sha256"]}, tagging=DAILY)
            manifest["files"][name] = entry
    if not manifest["files"]:
        raise RuntimeError("nothing to back up yet (no state.sqlite, catalog.sqlite or device.json)")
    bucket.put_bytes(prefix + "manifest.json", json.dumps(manifest, indent=2).encode(), content_type="application/json",
                     tagging=DAILY)
    _update_latest(bucket, root, {"prefix": prefix, "created_at": manifest["created_at"],
                                  "shoebox_version": manifest["shoebox_version"]})
    monthly = _copy_monthly(bucket, root, prefix, manifest, now)
    _remember_local(manifest["created_at"], prefix, bucket.name)
    return BackupResult(prefix=prefix, files=manifest["files"], monthly=monthly)


def _catalog_info(db: Path, device_id: str) -> dict:
    """Facts a restore needs: schema version, checksum and the last op this device had pushed."""
    from .catalog.checksum import checksum

    conn = sqlite3.connect(db)
    try:
        version = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
        last_pushed = conn.execute(
            "SELECT MAX(hlc) FROM ops WHERE device_id = ? AND op_id NOT IN (SELECT op_id FROM outbox)",
            (device_id,)).fetchone()[0]
        unpushed = conn.execute("SELECT COUNT(*) FROM outbox").fetchone()[0]
        return {"schema_version": int(version[0]) if version else 0, "checksum": checksum(conn),
                "last_pushed_op": last_pushed, "unpushed_ops": unpushed}
    finally:
        conn.close()


def _update_latest(bucket, root: str, latest: dict) -> None:
    """Point LATEST.json at this backup unless a newer one already took its place (compare-and-swap)."""
    key = root + "LATEST.json"
    body = json.dumps(latest).encode()
    for _ in range(3):
        current = bucket.get_with_etag(key)
        if current is None:
            if bucket.put_bytes_if(key, body, if_none_match=True, content_type="application/json"):
                return
        elif json.loads(current[0]).get("created_at", "") > latest["created_at"]:
            return
        elif bucket.put_bytes_if(key, body, if_match=current[1], content_type="application/json"):
            return


def _remember_local(created_at: str, prefix: str, bucket_name: str) -> None:
    paths.last_backup_file().write_text(json.dumps({"created_at": created_at, "prefix": prefix,
                                                    "bucket": bucket_name}))


def last_local_backup() -> dict | None:
    f = paths.last_backup_file()
    try:
        return json.loads(f.read_text()) if f.exists() else None
    except ValueError:
        return None


def _copy_monthly(bucket, root: str, prefix: str, manifest: dict, now: datetime) -> str | None:
    month_prefix = f"{root}monthly/{now:%Y-%m}/"
    if month_prefix in bucket.list_prefixes(f"{root}monthly/"):
        return None
    sources = [(entry["key"], month_prefix + name) for name, entry in manifest["files"].items()]
    sources.append((prefix + "manifest.json", month_prefix + "manifest.json"))
    for src, dest in sources:
        bucket.client.copy_object(Bucket=bucket.name, Key=dest, ServerSideEncryption="AES256",
                                  CopySource={"Bucket": bucket.name, "Key": src},
                                  TaggingDirective="REPLACE", Tagging=MONTHLY)
    return month_prefix


def list_backups(bucket, device_id: str) -> list[dict]:
    """Newest first: [{prefix, created_at, files}] for daily backups (monthly copies listed separately)."""
    root = f"backups/{device_id}/"
    out = []
    for prefix in bucket.list_prefixes(root):
        if prefix.endswith("monthly/"):
            continue
        try:
            manifest = json.loads(bucket.get_bytes(prefix + "manifest.json"))
        except Exception:  # an interrupted backup has no manifest: skip it
            continue
        out.append({"prefix": prefix, **manifest})
    return sorted(out, key=lambda m: m["created_at"], reverse=True)


def list_devices(bucket) -> list[str]:
    return [p.split("/")[1] for p in bucket.list_prefixes("backups/")]


def pick_backup(bucket, device_id: str, at: datetime | None = None) -> dict:
    backups = list_backups(bucket, device_id)
    if at is not None:
        at = at if at.tzinfo else at.replace(tzinfo=timezone.utc)
        backups = [b for b in backups if datetime.fromisoformat(b["created_at"]) <= at]
    if not backups:
        when = f" at or before {at:%Y-%m-%d %H:%M}" if at else ""
        raise RuntimeError(f"no backups found for device {device_id}{when}")
    return backups[0]


def restore(bucket, device_id: str, *, at: datetime | None = None, with_identity: bool = False) -> dict:
    """Download, verify and swap in a backup. Existing files are kept alongside as `.bak`.

    `with_identity` also restores device.json/config.toml (recovering a lost Mac as the same device).
    The caller must hold the ingest lock so no archive run is writing meanwhile.
    """
    chosen = pick_backup(bucket, device_id, at)
    restored: dict[str, str] = {}
    with tempfile.TemporaryDirectory(prefix="shoebox-restore-") as tmp:
        staged: list[tuple[Path, Path]] = []
        for name, entry in chosen["files"].items():
            if name in ("device.json", "config.toml") and not with_identity:
                continue
            got = Path(tmp) / name
            bucket.download(entry["key"], got)
            if _sha256(got) != entry["sha256"]:
                raise RuntimeError(f"{name}: checksum mismatch after download; backup not restored")
            if name.endswith(".sqlite.gz"):
                db = Path(tmp) / name.removesuffix(".gz")
                with gzip.open(got, "rb") as f_in, open(db, "wb") as f_out:
                    shutil.copyfileobj(f_in, f_out)
                ok, msg = integrity_ok(db)
                if not ok or _sha256(db) != entry.get("sha256_db", _sha256(db)):
                    raise RuntimeError(f"{name}: failed verification ({msg}); backup not restored")
                staged.append((db, _db_path(name.split(".")[0])))
            else:
                staged.append((got, {"device.json": paths.device_file(), "config.toml": paths.config_file()}[name]))
        # Everything verified: swap files in.
        for src, dest in staged:
            for suffix in ("", "-wal", "-shm"):
                old = Path(str(dest) + suffix)
                if old.exists():
                    os.replace(old, Path(str(dest) + suffix + ".bak"))
            shutil.copyfile(src, dest.with_suffix(dest.suffix + ".tmp"))
            if dest.stem in PRIVATE:
                os.chmod(dest.with_suffix(dest.suffix + ".tmp"), 0o600)
            os.replace(dest.with_suffix(dest.suffix + ".tmp"), dest)
            restored[dest.name] = chosen["created_at"]
    return {"backup": chosen["prefix"], "created_at": chosen["created_at"], "restored": restored}


def rebuild_state_from_s3(bucket, device: Device, state_path: Path | None = None) -> int:
    """Recreate state.sqlite from the sidecars in meta/: each one names its Apple Photos UUID and sha256.
    Only items this device archived from Apple Photos are restored (other devices' UUIDs mean nothing here)."""
    from .state import State

    state = State(state_path)
    mine = _device_source_refs(device)
    n = 0
    for key in bucket.list_keys("meta/"):
        if not key.endswith(".json"):
            continue
        rec = json.loads(bucket.get_bytes(key))
        src = rec.get("source") or {}
        ref = f"{src.get('type')}:{src.get('ref')}"
        if src.get("type") != "applephotos" or (mine is not None and ref not in mine):
            continue
        keys, file = rec.get("keys") or {}, rec.get("file") or {}
        state.upsert(ref, photo_id=rec["photo_id"], sha256=file.get("sha256"), raw_key=keys.get("raw"),
                     compressed_key=keys.get("compressed"), meta_key=keys.get("meta"), bytes_raw=file.get("bytes_raw"),
                     status="done" if keys.get("compressed") else "no_derivative", error=None)
        n += 1
    return n


def _device_source_refs(device: Device) -> set[str] | None:
    """Source refs the catalog attributes to this device, or None if the catalog doesn't know yet."""
    path = paths.catalog_db()
    if not path.exists():
        return None
    conn = sqlite3.connect(path)
    try:
        refs = {r[0] for r in conn.execute("SELECT source_ref FROM media_sources WHERE device_id = ?",
                                            (device.device_id,))}
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()
    return refs or None
