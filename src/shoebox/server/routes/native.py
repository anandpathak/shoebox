"""Uploads from the native iPhone/Android app (LOCAL_APP_PLAN.md §13).

The native apps upload whole files in the background (iOS background URLSession, Android WorkManager),
so this is one streamed request per file instead of tus. The app sends the file's SHA-256, which is
checked on arrival; photos already in the archive aren't stored again.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ... import paths
from ...catalog import open_catalog
from ...device import new_ulid
from ..deps import db, user

router = APIRouter(prefix="/api", dependencies=[Depends(user)])
SAFE = re.compile(r"[^A-Za-z0-9._ -]+")
DEVICE = re.compile(r"^[0-9A-Za-z_.:-]{1,64}$")
ASSET = re.compile(r"^[0-9A-Za-z_./:+-]{1,200}$")
MAX = 20 * 1024 ** 3


@router.post("/uploads/raw")
async def upload_raw(request: Request):
    h = request.headers
    device_id, asset_id = h.get("x-device-id", ""), h.get("x-asset-id", "")
    sha_expected = (h.get("x-sha256") or "").lower()
    name = SAFE.sub("_", Path(h.get("x-filename") or "upload").name).strip() or "upload"
    if not DEVICE.match(device_id) or not ASSET.match(asset_id):
        raise HTTPException(400, "X-Device-Id and X-Asset-Id are required")
    if sha_expected and not re.fullmatch(r"[0-9a-f]{64}", sha_expected):
        raise HTTPException(400, "X-Sha256 must be hex SHA-256")
    conn = open_catalog()
    try:
        dev = conn.execute("SELECT platform FROM devices WHERE device_id = ?", (device_id,)).fetchone()
        if dev is None:
            raise HTTPException(404, "Register the device first (POST /api/devices/register)")
        if sha_expected and conn.execute("SELECT 1 FROM media WHERE sha256 = ?", (sha_expected,)).fetchone():
            return {"status": "already_archived"}
        platform = dev["platform"] if dev["platform"] in ("ios", "android") else "native"
    finally:
        conn.close()

    folder = paths.inbox_dir() / device_id
    folder.mkdir(parents=True, exist_ok=True)
    upload_id = new_ulid()
    part = folder / f".{upload_id}.part"
    digest, size = hashlib.sha256(), 0
    with open(part, "wb") as f:
        async for chunk in request.stream():
            size += len(chunk)
            if size > MAX:
                part.unlink(missing_ok=True)
                raise HTTPException(413, "File too large")
            digest.update(chunk)
            f.write(chunk)
    got = digest.hexdigest()
    if size == 0 or (sha_expected and got != sha_expected):
        part.unlink(missing_ok=True)
        raise HTTPException(422, "The file didn't arrive intact (checksum mismatch); it will be retried")
    final = folder / f"{upload_id}__{name}"
    os.replace(part, final)
    final.with_name(final.name + ".sha256").write_text(got)
    lm = h.get("x-last-modified")
    (folder / f"{upload_id}.json").write_text(json.dumps({
        "id": upload_id, "length": size, "filename": name, "filetype": h.get("content-type"), "device_id": device_id,
        "last_modified": lm if lm and lm.isdigit() else None, "created": time.time(),
        "asset_ref": f"{platform}:{asset_id}"}))
    request.app.state.inbox.kick()
    return {"status": "received", "sha256": got, "bytes": size}


@router.get("/backup/status")
def backup_status(device: str = Query(..., pattern=r"^[0-9A-Za-z_.:-]{1,64}$"), conn: sqlite3.Connection = Depends(db)):
    archived = conn.execute("SELECT COUNT(DISTINCT s.photo_id) FROM media_sources s JOIN media m USING (photo_id) "
                            "WHERE s.device_id = ? AND m.deleted = 0", (device,)).fetchone()[0]
    last = conn.execute("SELECT MAX(seen_hlc) FROM media_sources WHERE device_id = ?", (device,)).fetchone()[0]
    folder = paths.inbox_dir() / device
    pending = len(list(folder.glob("*.json"))) if folder.exists() else 0
    return {"device_id": device, "archived": archived, "waiting_on_mac": pending,
            "last_backup_hlc": last}
