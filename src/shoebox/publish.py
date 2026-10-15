"""Publish this device's catalog for download (LOCAL_APP_PLAN.md §11.1).

  sync/catalogs/<device_id>/catalog.sqlite.gz
  sync/catalogs/<device_id>/manifest.json   replaced with If-Match; never moves backwards (max_hlc)
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .catalog import schema_version
from .catalog.checksum import checksum
from .catalog.export import export_catalog

CATALOGS_PREFIX = "sync/catalogs/"
MIN_INTERVAL = timedelta(minutes=10)


def catalog_key(device_id: str) -> str:
    return f"{CATALOGS_PREFIX}{device_id}/catalog.sqlite.gz"


def manifest_key(device_id: str) -> str:
    return f"{CATALOGS_PREFIX}{device_id}/manifest.json"


def _meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def publish_catalog(conn: sqlite3.Connection, bucket, device, *, force: bool = False,
                    now: datetime | None = None) -> dict | None:
    """Upload a fresh copy if the catalog changed and the last publish was 10+ minutes ago."""
    now = now or datetime.now(timezone.utc)
    current = checksum(conn)
    last = json.loads(_meta(conn, "catalog_published") or "{}")
    if not force and (last.get("checksum") == current or
                      (last.get("at") and now - datetime.fromisoformat(last["at"]) < MIN_INTERVAL)):
        return None
    max_hlc = conn.execute("SELECT MAX(hlc) FROM ops").fetchone()[0] or ""
    remote = bucket.get_with_etag(manifest_key(device.device_id))
    if remote and json.loads(remote[0]).get("max_hlc", "") > max_hlc:
        return None  # a newer copy is already published (e.g. restored from an older backup)
    with tempfile.TemporaryDirectory(prefix="shoebox-pub-") as tmp:
        out = export_catalog(conn, Path(tmp) / "catalog.sqlite.gz", strip_ops=True)
        size = out.stat().st_size
        bucket.put_bytes(catalog_key(device.device_id), out.read_bytes(), content_type="application/gzip")
    manifest = {
        "device_id": device.device_id, "name": device.name, "platform": device.platform,
        "schema_version": schema_version(conn), "created_at": now.isoformat(timespec="seconds"),
        "max_hlc": max_hlc, "checksum": current, "bytes": size,
        "item_count": conn.execute("SELECT COUNT(*) FROM media WHERE deleted = 0").fetchone()[0],
        "contributed_count": conn.execute(
            "SELECT COUNT(DISTINCT s.photo_id) FROM media_sources s JOIN media m ON m.photo_id = s.photo_id "
            "WHERE s.device_id = ? AND m.deleted = 0", (device.device_id,)).fetchone()[0],
    }
    body = json.dumps(manifest, indent=2).encode()
    ok = (bucket.put_bytes_if(manifest_key(device.device_id), body, if_match=remote[1],
                              content_type="application/json") if remote else
          bucket.put_bytes_if(manifest_key(device.device_id), body, if_none_match=True,
                              content_type="application/json"))
    if not ok:
        return None
    with conn:
        conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('catalog_published', ?)",
                     (json.dumps({"checksum": current, "at": manifest["created_at"]}),))
    return manifest


def list_catalogs(bucket) -> list[dict]:
    out = []
    for prefix in bucket.list_prefixes(CATALOGS_PREFIX):
        got = bucket.get_with_etag(prefix + "manifest.json")
        if got:
            out.append(json.loads(got[0]))
    return sorted(out, key=lambda m: m["created_at"], reverse=True)


def download_catalog(bucket, which: str, dest_dir: Path, *, flavour: str = "full") -> list[Path]:
    """Download catalogs as plain .sqlite files. `which` is a device id, 'all', or 'library' (the snapshot)."""
    import gzip
    import sqlite3

    from .snapshot import SNAPSHOT_KEY

    dest_dir.mkdir(parents=True, exist_ok=True)
    if which == "library":
        targets = [("library", SNAPSHOT_KEY, None)]
    else:
        ids = [m["device_id"] for m in list_catalogs(bucket)] if which == "all" else [which]
        targets = [(d, catalog_key(d), d) for d in ids]
    out = []
    for name, key, device_id in targets:
        got = bucket.get_with_etag(key)
        if got is None:
            raise RuntimeError(f"no catalog published at s3://{bucket.name}/{key}")
        path = dest_dir / f"{name}.sqlite"
        path.write_bytes(gzip.decompress(got[0]))
        if flavour == "contributed" and device_id:
            final = dest_dir / f"{name}.contributed.sqlite"
            conn = sqlite3.connect(path)
            try:
                export_catalog(conn, final, flavour="contributed", device_id=device_id)
            finally:
                conn.close()
            path.unlink()
            path = final
        out.append(path)
    return out
