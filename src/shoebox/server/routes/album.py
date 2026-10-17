"""The Apple Photos "Archived to S3" album: what's in it, what still needs adding, and a way to catch up."""

from __future__ import annotations

import threading
import time

from fastapi import APIRouter, Depends, Request

from ..deps import user

router = APIRouter(prefix="/api/album", dependencies=[Depends(user)])


class AlbumJob:
    """Compare with the real album, then add what's missing. One at a time, in the background."""

    def __init__(self):
        self._lock = threading.Lock()
        self.running = False
        self.last: dict | None = None

    def start(self, *, retry_problems: bool = False) -> bool:
        with self._lock:
            if self.running:
                return False
            self.running = True
        threading.Thread(target=self._work, args=(retry_problems,), name="album", daemon=True).start()
        return True

    def _work(self, retry_problems: bool) -> None:
        from ...archiver import flush_album, reconcile_album
        from ...state import State

        messages: list[str] = []
        try:
            state = State()
            if retry_problems:
                state.db.execute("UPDATE items SET album = 0, album_error = NULL WHERE album = 2")
                state.db.commit()
            try:
                changed = reconcile_album(state)
            except Exception as e:  # noqa: BLE001 — Photos library unreadable (Full Disk Access): still try to add
                changed = {}
                messages.append(f"Couldn't read the Photos library to check the album ({e}).")
            added = flush_album(state, lambda kind, **d: messages.append(d.get("text", "")))
            self.last = {"at": time.time(), "checked": changed, "added": added, "messages": messages}
        except Exception as e:  # noqa: BLE001
            self.last = {"at": time.time(), "error": f"{type(e).__name__}: {e}", "messages": messages}
        finally:
            self.running = False


@router.get("")
def status(request: Request):
    from ...state import State

    job: AlbumJob = request.app.state.album
    return {**State().album_summary(), "running": job.running, "last": job.last}


@router.post("/sync")
def sync(request: Request, retry_problems: bool = False):
    started = request.app.state.album.start(retry_problems=retry_problems)
    return {"started": started}
