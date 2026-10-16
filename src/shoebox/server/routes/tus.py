"""Resumable uploads (tus 1.0.0: core + creation + termination) → ~/.shoebox/inbox/<device>/ → archive.

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
    request.app.state.inbox.retry_failed()
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
    """Archives finished uploads as soon as no other run holds the ingest lock.

    Fault tolerance: everything it needs is on disk (the file and its <id>.json), so a restart simply picks
    up where it was; items already archived are recognised and cleared. A file that fails is retried after
    1, 2, 4 … minutes (at most an hour) and set aside after MAX_ATTEMPTS, until "Retry failed". Unexpected
    errors never stop the processor.
    """

    BATCH = 50              # files per archive run: new uploads join quickly, finished ones are cleared sooner
    MAX_ATTEMPTS = 5

    def __init__(self, app):
        self.app = app
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()        # guards starting/stopping the thread so no wake-up is lost
        self.last_run_id: str | None = None
        self.last_error: str | None = None

    def _all(self) -> list[tuple[Path, dict, Path]]:
        out = []
        for meta in paths.inbox_dir().glob("*/*.json"):
            if meta.parent.name.startswith("."):
                continue
            try:
                info = json.loads(meta.read_text())
            except (OSError, ValueError):
                continue
            data = meta.parent / f"{info['id']}__{info['filename']}"
            if data.exists():
                out.append((data, info, meta))
        return sorted(out, key=lambda x: x[1]["created"])

    def pending(self, *, due_only: bool = False) -> list[tuple[Path, dict]]:
        now = time.time()
        out = []
        for data, info, _ in self._all():
            if due_only and (info.get("gave_up") or info.get("next_try", 0) > now):
                continue
            out.append((data, info))
        return out

    def status(self) -> dict:
        return {"pending": [{"filename": i["filename"], "device_id": i["device_id"], "bytes": i["length"],
                             "attempts": i.get("attempts", 0), "failed": bool(i.get("gave_up")),
                             "error": i.get("last_error")} for _, i in self.pending()],
                "last_run_id": self.last_run_id, "last_error": self.last_error}

    def retry_failed(self) -> int:
        n = 0
        for _, info, meta in self._all():
            if info.get("attempts") or info.get("gave_up"):
                for k in ("attempts", "next_try", "gave_up", "last_error"):
                    info.pop(k, None)
                _write_json(meta, info)
                n += 1
        self.kick()
        return n

    def kick(self) -> None:
        with self._lock:
            self._wake.set()
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._loop, name="inbox", daemon=True)
                self._thread.start()

    def _loop(self) -> None:
        while True:
            self._wake.wait(timeout=5)
            self._wake.clear()
            try:
                batch = self.pending(due_only=True)[:self.BATCH]
                if not batch:
                    waiting = [i for _, i in self.pending() if not i.get("gave_up")]
                    with self._lock:
                        if self._wake.is_set():      # a file arrived while we were checking: go round again
                            continue
                        if not waiting:
                            self._thread = None
                            return
                    # failed files waiting for their next try: sleep until the first one is due
                    first = min(i.get("next_try", 0) for i in waiting)
                    self._wake.wait(timeout=max(1.0, min(60.0, first - time.time())))
                    self._wake.set()
                    continue
                self._run(batch)
                self.last_error = None
            except Exception as e:  # noqa: BLE001 — never let one bad moment stop uploads for good
                self.last_error = f"{type(e).__name__}: {e}"
                time.sleep(5)
                self._wake.set()

    def _run(self, batch) -> None:
        from datetime import datetime, timezone

        from ...runlock import AlreadyRunning
        from ...uploads import FileItem
        from .runs import options_for_app

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
            time.sleep(2)                    # e.g. an Apple Photos run: go again when it's done
            self._wake.set()
            return
        self.last_run_id = run.run_id
        while run.status == "running":
            time.sleep(0.5)
        self._clean(batch, run.results)
        self._note_failures(batch, run.results)
        self._wake.set()                     # more may be waiting

    def _note_failures(self, batch, results: dict) -> None:
        """Files still here after their run failed: back off before the next try, set aside after MAX_ATTEMPTS."""
        for data, info in batch:
            meta = data.parent / f"{info['id']}.json"
            if not data.exists() or not meta.exists():
                continue                     # archived and cleared (or moved to archived-locally)
            status, error = _outcome(info, results)
            if status in ("done", "no_derivative"):
                continue
            attempts = int(info.get("attempts", 0)) + 1
            info["attempts"] = attempts
            info["last_error"] = error or "archiving didn't finish"
            info["next_try"] = time.time() + min(60 * 2 ** (attempts - 1), 3600)
            if attempts >= self.MAX_ATTEMPTS:
                info["gave_up"] = True
            _write_json(meta, info)

    def _clean(self, batch, results: dict | None = None) -> None:
        """Delete inbox files once their original is safely in S3. Failures stay for 'retry failed'.
        Without S3 the uploaded file is the only copy: it moves to archived-locally/ and is uploaded
        the next time the server runs with a bucket."""
        from ...state import State

        state = State()
        local_only = self.app.state.settings.bucket is None
        for data, info in batch:
            meta = data.parent / f"{info['id']}.json"
            status, _ = _outcome(info, results or {}, state)
            if status not in ("done", "no_derivative"):
                continue                     # failed or not reached: stays for a retry
            if local_only:
                keep = data.parent / LOCAL
                keep.mkdir(exist_ok=True)
                os.replace(data, keep / data.name)
                os.replace(meta, keep / meta.name)
                data.with_name(data.name + ".sha256").unlink(missing_ok=True)
                continue
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


def _outcome(info: dict, results: dict, state=None) -> tuple[str | None, str | None]:
    """How this file's archive went: from the run's own results, else from the archive state (earlier runs)."""
    ref = _ref(info)
    if ref in results:
        return results[ref]
    if state is None:
        from ...state import State
        state = State()
    row = state.get(ref)
    return (row["status"], row["error"]) if row is not None else (None, None)


def _ref(info: dict) -> str:
    return info.get("asset_ref") or f"upload:{info['device_id']}:{info['id']}:{info['filename']}"


def _write_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data))
    os.replace(tmp, path)


def tidy_inbox(*, stale_part_days: float = 7) -> dict:
    """At startup: remove debris a crash or an old bug could leave behind. Never touches an upload itself."""
    removed = {"cache": 0, "partial": 0}
    inbox = paths.inbox_dir()
    for f in inbox.rglob("*.sha256*"):
        if f.name.count(".sha256") > 1 or (f.name.endswith(".sha256") and not f.with_suffix("").exists()):
            f.unlink(missing_ok=True)        # re-cached caches (old bug) and caches of files that are gone
            removed["cache"] += 1
    cutoff = time.time() - stale_part_days * 86400
    for f in list(inbox.glob("*/.*.part")) + list((inbox / ".uploads").glob("*.part")):
        try:
            if f.stat().st_mtime < cutoff:   # an upload abandoned a week ago won't be resumed
                f.unlink()
                f.with_suffix(".json").unlink(missing_ok=True)
                removed["partial"] += 1
        except OSError:
            pass
    for f in inbox.rglob("*.json.tmp"):
        f.unlink(missing_ok=True)
    return removed
