"""Compacted catalog snapshots so new devices start fast (LOCAL_APP_PLAN.md §6.2, §7).

  sync/snapshots/latest.sqlite.gz   the compacted catalog (one key, overwritten; bucket versioning keeps history)
  sync/HEAD.json                    {etag, created_at, by, max_hlc, media} — replaced with If-Match
  sync/locks/compact.json           lease: only its holder may build a snapshot
"""

from __future__ import annotations

import gzip
import json
import shutil
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import paths
from .catalog.export import export_catalog, integrity_ok

SNAPSHOT_KEY = "sync/snapshots/latest.sqlite.gz"
HEAD_KEY = "sync/HEAD.json"
LEASE_KEY = "sync/locks/compact.json"
LEASE_SECONDS = 300
COMPACT_AFTER_OPS = 5000
COMPACT_AFTER_DAYS = 25


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------- lease

class Lease:
    """A time-limited lock in S3. Acquire with If-None-Match (new) or If-Match (take over an expired one)."""

    def __init__(self, bucket, device_id: str, key: str = LEASE_KEY, seconds: int = LEASE_SECONDS, now=_now):
        self.bucket, self.device_id, self.key, self.seconds, self._now = bucket, device_id, key, seconds, now
        self.etag: str | None = None

    def _body(self, expires: datetime) -> bytes:
        return json.dumps({"device_id": self.device_id, "expires_at": expires.isoformat()}).encode()

    def acquire(self) -> bool:
        current = self.bucket.get_with_etag(self.key)
        body = self._body(self._now() + timedelta(seconds=self.seconds))
        if current is None:
            self.etag = self.bucket.put_bytes_if(self.key, body, if_none_match=True, content_type="application/json")
        else:
            data, etag = current
            holder = json.loads(data)
            expired = datetime.fromisoformat(holder["expires_at"]) <= self._now()
            if holder["device_id"] != self.device_id and not expired:
                return False
            self.etag = self.bucket.put_bytes_if(self.key, body, if_match=etag, content_type="application/json")
        return self.etag is not None

    def renew(self) -> bool:
        if self.etag is None:
            return False
        body = self._body(self._now() + timedelta(seconds=self.seconds))
        self.etag = self.bucket.put_bytes_if(self.key, body, if_match=self.etag, content_type="application/json")
        return self.etag is not None

    def release(self) -> None:
        if self.etag is not None:
            self.bucket.put_bytes_if(self.key, self._body(self._now() - timedelta(seconds=1)), if_match=self.etag,
                                     content_type="application/json")
            self.etag = None


# ---------------------------------------------------------------- snapshot

def read_head(bucket) -> tuple[dict, str] | None:
    got = bucket.get_with_etag(HEAD_KEY)
    return (json.loads(got[0]), got[1]) if got else None


def needs_snapshot(conn: sqlite3.Connection, bucket) -> bool:
    head = read_head(bucket)
    if head is None:
        return conn.execute("SELECT COUNT(*) FROM media").fetchone()[0] > 0
    info = head[0]
    newer = conn.execute("SELECT COUNT(*) FROM ops WHERE hlc > ?", (info.get("max_hlc") or "",)).fetchone()[0]
    age = _now() - datetime.fromisoformat(info["created_at"])
    return newer >= COMPACT_AFTER_OPS or (newer > 0 and age >= timedelta(days=COMPACT_AFTER_DAYS))


def publish_snapshot(conn: sqlite3.Connection, bucket, device) -> dict | None:
    """Build and publish a snapshot if we can take the lease. Returns the new HEAD, or None if busy."""
    lease = Lease(bucket, device.device_id)
    if not lease.acquire():
        return None
    try:
        with tempfile.TemporaryDirectory(prefix="photoctl-snap-") as tmp:
            out = export_catalog(conn, Path(tmp) / "latest.sqlite.gz", strip_ops=True)
            if not lease.renew():
                return None  # lost the lease while exporting: someone else is publishing
            etag = bucket.put_bytes_if(SNAPSHOT_KEY, out.read_bytes(), content_type="application/gzip")
        head = {"etag": etag, "created_at": _now().isoformat(timespec="seconds"), "by": device.device_id,
                "max_hlc": conn.execute("SELECT MAX(hlc) FROM ops").fetchone()[0],
                "media": conn.execute("SELECT COUNT(*) FROM media").fetchone()[0]}
        current = read_head(bucket)
        body = json.dumps(head, indent=2).encode()
        ok = (bucket.put_bytes_if(HEAD_KEY, body, if_match=current[1], content_type="application/json") if current
              else bucket.put_bytes_if(HEAD_KEY, body, if_none_match=True, content_type="application/json"))
        return head if ok else None
    finally:
        lease.release()


def is_empty(path: Path) -> bool:
    if not path.exists():
        return True
    conn = sqlite3.connect(path)
    try:
        n = conn.execute("SELECT (SELECT COUNT(*) FROM media) + (SELECT COUNT(*) FROM ops) + "
                         "(SELECT COUNT(*) FROM sync_cursor)").fetchone()[0]
    except sqlite3.OperationalError:
        return True
    finally:
        conn.close()
    return n == 0


def bootstrap(bucket, path: Path | None = None) -> dict | None:
    """Give an empty catalog a head start from the latest snapshot. Returns HEAD info, or None."""
    path = Path(path) if path else paths.catalog_db()
    if not is_empty(path):
        return None
    for _ in range(3):  # HEAD and snapshot can change between reads; retry until they agree
        head = read_head(bucket)
        if head is None:
            return None
        got = bucket.get_with_etag(SNAPSHOT_KEY)
        if got is None or got[1] != head[0]["etag"]:
            continue
        with tempfile.TemporaryDirectory(prefix="photoctl-boot-") as tmp:
            db = Path(tmp) / "catalog.sqlite"
            db.write_bytes(gzip.decompress(got[0]))
            ok, msg = integrity_ok(db)
            if not ok:
                raise RuntimeError(f"snapshot failed integrity_check: {msg}")
            for suffix in ("-wal", "-shm"):
                Path(str(path) + suffix).unlink(missing_ok=True)
            shutil.copyfile(db, path)
        return head[0]
    raise RuntimeError("snapshot kept changing while downloading; try again")
