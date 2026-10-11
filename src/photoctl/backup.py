"""Back up this device's databases to S3 and restore them (LOCAL_APP_PLAN.md §12).

Layout:
  backups/<device_id>/<YYYY-MM-DDTHH-MM-SSZ>/{state,catalog}.sqlite.gz, device.json, config.toml, manifest.json
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

DATABASES = ("state", "catalog")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _photoctl_version() -> str:
    try:
        return version("photoctl")
    except PackageNotFoundError:
        return "dev"


def _db_path(name: str) -> Path:
    return {"state": paths.state_db(), "catalog": paths.catalog_db()}[name]


def _local_files() -> dict[str, Path]:
    """Name in the backup → local path, for everything that exists right now."""
    files = {f"{db}.sqlite.gz": _db_path(db) for db in DATABASES}
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
                "device_name": device.name, "photoctl_version": _photoctl_version(), "files": {}}
    with tempfile.TemporaryDirectory(prefix="photoctl-backup-") as tmp:
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
                out = Path(tmp) / name
                with open(raw, "rb") as f_in, gzip.open(out, "wb", compresslevel=6) as f_out:
                    shutil.copyfileobj(f_in, f_out)
                entry = {"sha256_db": _sha256(raw), "bytes_db": raw.stat().st_size}
            else:
                out = src
                entry = {}
            entry |= {"key": prefix + name, "sha256": _sha256(out), "bytes": out.stat().st_size}
            bucket.upload(out, prefix + name, content_type="application/octet-stream",
                          metadata={"sha256": entry["sha256"]})
            manifest["files"][name] = entry
    if not manifest["files"]:
        raise RuntimeError("nothing to back up yet (no state.sqlite, catalog.sqlite or device.json)")
    bucket.put_bytes(prefix + "manifest.json", json.dumps(manifest, indent=2).encode(), content_type="application/json")
    bucket.put_bytes(root + "LATEST.json", json.dumps({"prefix": prefix, **{k: manifest[k] for k in
                     ("created_at", "photoctl_version")}}).encode(), content_type="application/json")
    monthly = _copy_monthly(bucket, root, prefix, manifest, now)
    return BackupResult(prefix=prefix, files=manifest["files"], monthly=monthly)


def _copy_monthly(bucket, root: str, prefix: str, manifest: dict, now: datetime) -> str | None:
    month_prefix = f"{root}monthly/{now:%Y-%m}/"
    if month_prefix in bucket.list_prefixes(f"{root}monthly/"):
        return None
    for name, entry in manifest["files"].items():
        bucket.client.copy_object(Bucket=bucket.name, Key=month_prefix + name, ServerSideEncryption="AES256",
                                  CopySource={"Bucket": bucket.name, "Key": entry["key"]})
    bucket.client.copy_object(Bucket=bucket.name, Key=month_prefix + "manifest.json", ServerSideEncryption="AES256",
                              CopySource={"Bucket": bucket.name, "Key": prefix + "manifest.json"})
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
    with tempfile.TemporaryDirectory(prefix="photoctl-restore-") as tmp:
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
            os.replace(dest.with_suffix(dest.suffix + ".tmp"), dest)
            restored[dest.name] = chosen["created_at"]
    return {"backup": chosen["prefix"], "created_at": chosen["created_at"], "restored": restored}
