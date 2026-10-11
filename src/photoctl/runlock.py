"""One archive run at a time per device, plus a JSONL progress log per run (LOCAL_APP_PLAN.md §6.1)."""

from __future__ import annotations

import fcntl
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from . import paths
from .device import new_ulid


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class AlreadyRunning(RuntimeError):
    def __init__(self, holder: dict):
        self.holder = holder
        super().__init__(f"an archive run is already in progress (pid {holder.get('pid', '?')}, "
                         f"started {holder.get('started_at', '?')}, run {holder.get('run_id', '?')})")


class IngestLock:
    """Exclusive, non-blocking flock. The OS releases it if the process dies, so it never goes stale."""

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else paths.ingest_lock()
        self._fd: int | None = None
        self.run_id = new_ulid()

    def __enter__(self) -> "IngestLock":
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            try:
                holder = json.loads(os.pread(fd, 4096, 0).decode() or "{}")
            except (ValueError, OSError):
                holder = {}
            os.close(fd)
            raise AlreadyRunning(holder) from None
        os.ftruncate(fd, 0)
        os.pwrite(fd, json.dumps({"pid": os.getpid(), "started_at": _now(), "run_id": self.run_id}).encode(), 0)
        self._fd = fd
        return self

    def __exit__(self, *exc) -> None:
        if self._fd is not None:
            os.ftruncate(self._fd, 0)
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            os.close(self._fd)
            self._fd = None


class RunLog:
    """Append-only JSONL events at ~/.photoctl/runs/<run_id>.jsonl; the app tails this for live progress."""

    def __init__(self, run_id: str):
        self.path = paths.runs_dir() / f"{run_id}.jsonl"
        self._f = open(self.path, "a", encoding="utf-8")

    def event(self, kind: str, **data) -> None:
        self._f.write(json.dumps({"ts": _now(), "event": kind, **data}, ensure_ascii=False) + "\n")
        self._f.flush()

    def close(self) -> None:
        self._f.close()
