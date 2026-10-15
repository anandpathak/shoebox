"""A read-only copy of the library for phones to browse when no Mac is on (LOCAL_APP_PLAN.md §18).

  viewer/library.sqlite.gz   the whole catalog, without the op log (what the app searches on the phone)
  viewer/HEAD.json           {created_at, items, bytes, sha256, by}: phones check this small file first

Published after a sync when the library changed, at most every 15 minutes. Phones read it, the thumbnails
and the small copies with a read-only key (infra: aws_iam_user.reader) that can't see originals or backups
and can't write anything.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from . import paths
from .catalog.export import export_catalog

VIEWER_KEY = "viewer/library.sqlite.gz"
HEAD_KEY = "viewer/HEAD.json"
MIN_INTERVAL = 15 * 60


def _marker_file() -> Path:
    return paths.home() / "viewer_published.json"


def _marker(conn) -> list:
    """Changes whenever the library does (new ops applied, items added)."""
    return [conn.execute("SELECT MAX(hlc) FROM ops").fetchone()[0],
            conn.execute("SELECT COUNT(*) FROM media").fetchone()[0]]


def publish_viewer(conn, bucket, device, *, force: bool = False, min_interval: int = MIN_INTERVAL,
                   now=time.time) -> dict | None:
    """Upload a fresh copy if the library changed since the last one. Returns the new HEAD, or None."""
    marker = _marker(conn)
    try:
        last = json.loads(_marker_file().read_text())
    except (FileNotFoundError, ValueError):
        last = {}
    if not force and (last.get("marker") == marker or now() - last.get("at", 0) < min_interval):
        return None
    with tempfile.TemporaryDirectory(prefix="shoebox-viewer-") as tmp:
        out = export_catalog(conn, Path(tmp) / "library.sqlite.gz", strip_ops=True)
        data = out.read_bytes()
    bucket.put_bytes(VIEWER_KEY, data, content_type="application/gzip")
    head = {"created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "items": marker[1],
            "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(), "by": device.device_id}
    bucket.put_bytes(HEAD_KEY, json.dumps(head).encode(), content_type="application/json")
    tmp_marker = _marker_file().with_suffix(".tmp")
    tmp_marker.write_text(json.dumps({"marker": marker, "at": now()}))
    os.replace(tmp_marker, _marker_file())
    return head


# ---------------------------------------------------------------- the read-only key phones use

SERVICE, ACCOUNT = "shoebox", "viewer-access"


def _access_file() -> Path:
    return paths.home() / "viewer_access.json"


def save_access(key_id: str, secret: str) -> None:
    """Keep the read-only key on this Mac (Keychain; a 0600 file where there's none) to hand to phones."""
    body = json.dumps({"key_id": key_id, "secret": secret})
    if os.environ.get("SHOEBOX_TOKEN_STORE", "keychain") == "keychain":
        try:
            import keyring

            keyring.set_password(SERVICE, ACCOUNT, body)
            return
        except Exception:  # noqa: BLE001 — no keychain: fall back to a private file
            pass
    fd = os.open(_access_file(), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(body)


def load_access() -> dict | None:
    if os.environ.get("SHOEBOX_TOKEN_STORE", "keychain") == "keychain":
        try:
            import keyring

            got = keyring.get_password(SERVICE, ACCOUNT)
            if got:
                return json.loads(got)
        except Exception:  # noqa: BLE001
            pass
    try:
        return json.loads(_access_file().read_text())
    except (FileNotFoundError, ValueError):
        return None


def clear_access() -> None:
    try:
        import keyring

        keyring.delete_password(SERVICE, ACCOUNT)
    except Exception:  # noqa: BLE001
        pass
    _access_file().unlink(missing_ok=True)
