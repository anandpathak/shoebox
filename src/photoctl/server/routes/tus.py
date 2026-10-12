"""Resumable uploads (tus 1.0.0: core + creation + termination) → ~/.photoctl/inbox/<device>/ → archive.

A dropped connection loses nothing: the client asks for the offset (HEAD) and continues (PATCH).
"""

from __future__ import annotations

import base64
import json
import os
import re
import threading
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from ... import paths
from ...device import new_ulid
from ..deps import user

router = APIRouter(prefix="/api/uploads/files", dependencies=[Depends(user)])
TUS = {"Tus-Resumable": "1.0.0"}
MAX_SIZE = 20 * 1024 ** 3
SAFE = re.compile(r"[^A-Za-z0-9._ -]+")
DEVICE = re.compile(r"^[0-9A-Za-z_.:-]{1,64}$")
LOCAL = "archived-locally"


def _staging() -> Path:
    d = paths.inbox_dir() / ".uploads"
    d.mkdir(exist_ok=True)
    return d


def _info_path(upload_id: str) -> Path:
    if not re.fullmatch(r"[0-9A-Z]{26}", upload_id):
        raise HTTPException(404, "No such upload")
    return _staging() / f"{upload_id}.json"


def _load(upload_id: str) -> dict:
    p = _info_path(upload_id)
    if not p.exists():
        raise HTTPException(404, "No such upload")
    return json.loads(p.read_text())


def _metadata(header: str | None) -> dict:
    out = {}
    for pair in (header or "").split(","):
        if not pair.strip():
            continue
        key, _, value = pair.strip().partition(" ")
        try:
            out[key] = base64.b64decode(value).decode() if value else ""
        except ValueError:
            raise HTTPException(400, f"Bad Upload-Metadata for {key}") from None
    return out


@router.options("")
def options():
    return Response(status_code=204, headers={**TUS, "Tus-Version": "1.0.0", "Tus-Extension": "creation,termination",
                                              "Tus-Max-Size": str(MAX_SIZE)})


@router.post("", status_code=201)
def create(request: Request):
    try:
        length = int(request.headers.get("upload-length", ""))
    except ValueError:
        raise HTTPException(400, "Upload-Length is required") from None
    if not 0 < length <= MAX_SIZE:
        raise HTTPException(413, "File too large")
    meta = _metadata(request.headers.get("upload-metadata"))
    name = SAFE.sub("_", Path(meta.get("filename") or "upload").name).strip() or "upload"
    device_id = meta.get("device_id") or request.app.state.device.device_id
    if not DEVICE.match(device_id):
        raise HTTPException(400, "Bad device_id")
    upload_id = new_ulid()
    info = {"id": upload_id, "length": length, "filename": name, "filetype": meta.get("filetype"),
            "device_id": device_id, "last_modified": meta.get("lastModified"), "created": time.time()}
    _info_path(upload_id).write_text(json.dumps(info))
    (_staging() / f"{upload_id}.part").touch()
    return Response(status_code=201, headers={**TUS, "Location": f"/api/uploads/files/{upload_id}", "Upload-Offset": "0"})


@router.head("/{upload_id}")
def head(upload_id: str):
    info = _load(upload_id)
    part = _staging() / f"{upload_id}.part"
    offset = part.stat().st_size if part.exists() else info["length"]
    return Response(status_code=200, headers={**TUS, "Upload-Offset": str(offset), "Upload-Length": str(info["length"]),
                                              "Cache-Control": "no-store"})


@router.patch("/{upload_id}")
async def patch(upload_id: str, request: Request):
    if request.headers.get("content-type") != "application/offset+octet-stream":
        raise HTTPException(415, "Content-Type must be application/offset+octet-stream")
    info = _load(upload_id)
    part = _staging() / f"{upload_id}.part"
    if not part.exists():
        raise HTTPException(409, "Upload already complete")
    offset = int(request.headers.get("upload-offset", "-1"))
    if offset != part.stat().st_size:
        raise HTTPException(409, "Upload-Offset doesn't match; ask for the current offset first")
    with open(part, "ab") as f:
        async for chunk in request.stream():
            if f.tell() + len(chunk) > info["length"]:
                raise HTTPException(400, "More data than Upload-Length")
            f.write(chunk)
        f.flush()
        os.fsync(f.fileno())
        new = f.tell()
    if new == info["length"]:
        _finish(request.app, info, part)
    return Response(status_code=204, headers={**TUS, "Upload-Offset": str(new)})


@router.delete("/{upload_id}")
def delete(upload_id: str):
    _load(upload_id)
    for suffix in (".part", ".json"):
        (_staging() / f"{upload_id}{suffix}").unlink(missing_ok=True)
    return Response(status_code=204, headers=TUS)


@router.post("/retry")
def retry(request: Request):
    """Try again with uploads whose archive failed (they stay in the inbox until they succeed)."""
    request.app.state.inbox.kick()
    return request.app.state.inbox.status()


@router.get("")
def inbox(request: Request):
    """Uploads that are complete but not archived yet, and recent results."""
    return request.app.state.inbox.status()


def _finish(app, info: dict, part: Path) -> None:
    folder = paths.inbox_dir() / info["device_id"]
    folder.mkdir(parents=True, exist_ok=True)
    final = folder / f"{info['id']}__{info['filename']}"
    os.replace(part, final)
    (_staging() / f"{info['id']}.json").replace(folder / f"{info['id']}.json")
    app.state.inbox.kick()


class InboxProcessor:
    """Archives finished uploads as soon as no other run holds the ingest lock."""

    def __init__(self, app):
        self.app = app
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()        # guards starting/stopping the thread so no wake-up is lost
        self.last_run_id: str | None = None

    def pending(self) -> list[tuple[Path, dict]]:
        out = []
        for meta in paths.inbox_dir().glob("*/*.json"):
            if meta.parent.name.startswith("."):
                continue
            info = json.loads(meta.read_text())
            data = meta.parent / f"{info['id']}__{info['filename']}"
            if data.exists():
                out.append((data, info))
        return sorted(out, key=lambda x: x[1]["created"])

    def status(self) -> dict:
        return {"pending": [{"filename": i["filename"], "device_id": i["device_id"], "bytes": i["length"]}
                            for _, i in self.pending()], "last_run_id": self.last_run_id}

    def kick(self) -> None:
        with self._lock:
            self._wake.set()
            if self._thread is None:
                self._thread = threading.Thread(target=self._loop, name="inbox", daemon=True)
                self._thread.start()

    def _loop(self) -> None:
        from datetime import datetime, timezone

        from ...runlock import AlreadyRunning
        from ...uploads import FileItem
        from .runs import options_for_app

        while True:
            self._wake.wait(timeout=5)
            self._wake.clear()
            batch = self.pending()
            if not batch:
                with self._lock:
                    if self._wake.is_set():      # a file arrived while we were checking: go round again
                        continue
                    self._thread = None
                    return
            items = []
            for data, info in batch:
                lm = info.get("last_modified")
                when = datetime.fromtimestamp(int(lm) / 1000, tz=timezone.utc) if lm and str(lm).isdigit() else None
                items.append(FileItem(str(data), info["filename"], info["device_id"], info["id"], last_modified=when,
                                      asset_ref=info.get("asset_ref")))
            try:
                run = self.app.state.runs.start("upload", f"{len(items)} uploaded file(s)", options_for_app(self.app),
                                                items=items, device=self.app.state.device)
            except AlreadyRunning:
                time.sleep(2)
                self._wake.set()
                continue
            self.last_run_id = run.run_id
            while run.status == "running":
                time.sleep(0.5)
            self._clean(batch)

    def _clean(self, batch) -> None:
        """Delete inbox files once their original is safely in S3. Failures stay for 'retry failed'.
        Without S3 the uploaded file is the only copy: it moves to archived-locally/ and is uploaded
        the next time the server runs with a bucket."""
        from ...state import State

        state = State()
        local_only = self.app.state.settings.bucket is None
        for data, info in batch:
            meta = data.parent / f"{info['id']}.json"
            if local_only:
                keep = data.parent / LOCAL
                keep.mkdir(exist_ok=True)
                os.replace(data, keep / data.name)
                os.replace(meta, keep / meta.name)
                data.with_name(data.name + ".sha256").unlink(missing_ok=True)
                continue
            row = state.get(info.get("asset_ref") or f"upload:{info['device_id']}:{info['id']}:{info['filename']}")
            if row is not None and row["status"] in ("done", "no_derivative"):
                data.unlink(missing_ok=True)
                meta.unlink(missing_ok=True)
                data.with_name(data.name + ".sha256").unlink(missing_ok=True)

    def requeue_local(self) -> int:
        """Server started with S3: send files that were only archived locally through again."""
        n = 0
        for meta in paths.inbox_dir().glob(f"*/{LOCAL}/*.json"):
            info = json.loads(meta.read_text())
            data = meta.parent / f"{info['id']}__{info['filename']}"
            if data.exists():
                os.replace(data, meta.parent.parent / data.name)
                os.replace(meta, meta.parent.parent / meta.name)
                n += 1
        if n:
            self.kick()
        return n
