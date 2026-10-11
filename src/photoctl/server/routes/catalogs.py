"""Download catalogs (LOCAL_APP_PLAN.md §11): this server's live library, any device's published
catalog, one device's contributions, or all of them; and accept catalogs uploaded by phones."""

from __future__ import annotations

import gzip
import json
import sqlite3
import tempfile
import threading
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse

from ... import paths
from ...catalog import epoch, schema_version
from ...catalog.checksum import checksum
from ...catalog.export import export_catalog, integrity_ok
from ...publish import CATALOGS_PREFIX, catalog_key, list_catalogs, manifest_key
from ..deps import bucket, db, user

router = APIRouter(prefix="/api", dependencies=[Depends(user)])
_export_lock = threading.Lock()
MAX_UPLOAD = 512 * 1024 * 1024


def _library_export(conn: sqlite3.Connection) -> tuple[Path, dict]:
    """Cached gzip of the live catalog, rebuilt when the catalog changed."""
    cache = paths.cache_dir()
    out, meta_file = cache / "library.sqlite.gz", cache / "library.json"
    with _export_lock:
        state = {"top": conn.execute("SELECT COALESCE(MAX(rowid), 0) FROM ops").fetchone()[0], "epoch": epoch(conn)}
        if out.exists() and meta_file.exists():
            meta = json.loads(meta_file.read_text())
            if meta.get("top") == state["top"] and meta.get("epoch") == state["epoch"]:
                return out, meta
        seen: dict = {}

        def inspect(copy: sqlite3.Connection) -> None:
            seen["cursor"] = copy.execute("SELECT COALESCE(MAX(rowid), 0) FROM ops").fetchone()[0]
            seen["items"] = copy.execute("SELECT COUNT(*) FROM media WHERE deleted = 0").fetchone()[0]

        tmp = cache / "library.sqlite.gz.tmp"
        export_catalog(conn, tmp, strip_ops=True, gzip_output=True, inspect=inspect)
        tmp.replace(out)
        meta = {**state, "top": seen["cursor"], "cursor": seen["cursor"], "items": seen["items"],
                "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        meta_file.write_text(json.dumps(meta))
        return out, meta


def _gz_response(path: Path, filename: str, headers: dict | None = None) -> FileResponse:
    return FileResponse(path, media_type="application/gzip", filename=filename,
                        headers={"Cache-Control": "private, no-cache", **(headers or {})})


@router.get("/catalogs")
def catalogs(request: Request, conn: sqlite3.Connection = Depends(db)):
    me = request.app.state.device
    own = {"device_id": me.device_id, "name": me.name, "platform": me.platform, "kind": "library",
           "item_count": conn.execute("SELECT COUNT(*) FROM media WHERE deleted = 0").fetchone()[0],
           "checksum": checksum(conn), "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    s3 = bucket(request)
    published = [{**m, "kind": "published"} for m in list_catalogs(s3)] if s3 else []
    return {"library": own, "devices": published}


@router.get("/catalogs/library")
def library_catalog(conn: sqlite3.Connection = Depends(db)):
    path, meta = _library_export(conn)
    return _gz_response(path, "library.sqlite.gz", {"X-Ops-Cursor": str(meta["cursor"]),
                                                     "X-Catalog-Epoch": meta["epoch"],
                                                     "X-Item-Count": str(meta["items"])})


@router.get("/catalogs/all.zip")
def all_catalogs(request: Request, conn: sqlite3.Connection = Depends(db)):
    path, _ = _library_export(conn)
    out = paths.cache_dir() / "all-catalogs.zip"
    s3 = bucket(request)
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_STORED) as z:
        z.write(path, "library.sqlite.gz")
        for m in (list_catalogs(s3) if s3 else []):
            got = s3.get_with_etag(catalog_key(m["device_id"]))
            if got:
                safe = "".join(c if c.isalnum() or c in " -_" else "_" for c in m["name"]).strip() or m["device_id"]
                z.writestr(f"{safe} ({m['platform']}) {m['device_id']}.sqlite.gz", got[0])
                z.writestr(f"{safe} ({m['platform']}) {m['device_id']}.json", json.dumps(m, indent=2))
    return FileResponse(out, media_type="application/zip", filename="catalogs.zip")


@router.get("/catalogs/{device_id}")
def device_catalog(device_id: str, request: Request, flavour: str = Query("full", pattern="^(full|contributed)$"),
                   conn: sqlite3.Connection = Depends(db)):
    if flavour == "contributed":
        known = conn.execute("SELECT 1 FROM media_sources WHERE device_id = ? LIMIT 1", (device_id,)).fetchone()
        if known is None:
            raise HTTPException(404, "No items came from that device")
        out = paths.cache_dir() / f"{device_id}.contributed.sqlite.gz"
        export_catalog(conn, out, flavour="contributed", device_id=device_id, strip_ops=True)
        return _gz_response(out, f"{device_id}.contributed.sqlite.gz")
    if device_id == request.app.state.device.device_id:
        return library_catalog(conn)
    s3 = bucket(request)
    if s3 is None:
        raise HTTPException(404, "Published catalogs need S3; this server runs without it")
    got = s3.get_with_etag(catalog_key(device_id))
    if got is None:
        raise HTTPException(404, "That device hasn't published a catalog")
    out = paths.cache_dir() / f"{device_id}.sqlite.gz"
    out.write_bytes(got[0])
    return _gz_response(out, f"{device_id}.sqlite.gz")


@router.put("/catalogs/{device_id}")
async def upload_catalog(device_id: str, request: Request):
    """A phone publishes its on-device catalog (gzip body). Checked, then stored in S3 for others."""
    from ...catalog import open_catalog

    if device_id == request.app.state.device.device_id:
        raise HTTPException(409, "That device id belongs to this server")
    conn = open_catalog()  # async route: open on this thread rather than via the threadpool dependency
    try:
        dev = conn.execute("SELECT name, platform FROM devices WHERE device_id = ?", (device_id,)).fetchone()
    finally:
        conn.close()
    if dev is None:
        raise HTTPException(404, "Register the device first (POST /api/devices/register)")
    s3 = bucket(request)
    if s3 is None:
        raise HTTPException(503, "Publishing catalogs needs S3")
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_UPLOAD:
            raise HTTPException(413, "Catalog too large")
    with tempfile.TemporaryDirectory(prefix="photoctl-up-") as tmp:
        db_path = Path(tmp) / "c.sqlite"
        try:
            db_path.write_bytes(gzip.decompress(bytes(body)))
            ok, msg = integrity_ok(db_path)
        except (OSError, EOFError, sqlite3.DatabaseError) as e:
            raise HTTPException(422, f"Not a gzipped SQLite catalog: {e}") from None
        if not ok:
            raise HTTPException(422, f"Catalog failed integrity_check: {msg}")
        up = sqlite3.connect(db_path)
        try:
            manifest = {
                "device_id": device_id, "name": dev["name"], "platform": dev["platform"],
                "schema_version": schema_version(up), "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "max_hlc": up.execute("SELECT COALESCE(MAX(hlc), '') FROM ops").fetchone()[0],
                "checksum": checksum(up), "bytes": len(body),
                "item_count": up.execute("SELECT COUNT(*) FROM media WHERE deleted = 0").fetchone()[0],
                "contributed_count": up.execute("SELECT COUNT(DISTINCT photo_id) FROM media_sources WHERE device_id = ?",
                                                (device_id,)).fetchone()[0],
            }
        except sqlite3.DatabaseError as e:
            raise HTTPException(422, f"Not a photoctl catalog: {e}") from None
        finally:
            up.close()
    s3.put_bytes(catalog_key(device_id), bytes(body), content_type="application/gzip")
    s3.put_bytes(manifest_key(device_id), json.dumps(manifest, indent=2).encode(), content_type="application/json")
    return manifest


__all__ = ["router", "CATALOGS_PREFIX"]
