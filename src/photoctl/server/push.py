"""Web push notifications: "archive finished", "original ready" (LOCAL_APP_PLAN.md §9.2).

Keys (VAPID) and subscriptions live in ~/.photoctl with owner-only permissions. Sending goes through
the browser maker's push service (Apple, Google, Mozilla); iPhones need the app installed to the
Home Screen (iOS 16.4+).
"""

from __future__ import annotations

import base64
import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

from .. import paths

log = logging.getLogger(__name__)
_lock = threading.Lock()


def _private(path: Path, data: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(data)


def _keys_file() -> Path:
    return paths.home() / "vapid.json"


def _subs_file() -> Path:
    return paths.home() / "push_subscriptions.json"


def keys() -> dict:
    """{"private_pem": ..., "public_key": base64url uncompressed point} — created on first use."""
    f = _keys_file()
    if f.exists():
        return json.loads(f.read_text())
    from cryptography.hazmat.primitives import serialization
    from py_vapid import Vapid01

    v = Vapid01()
    v.generate_keys()
    pem = v.private_pem().decode()
    raw = v.public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    data = {"private_pem": pem, "public_key": base64.urlsafe_b64encode(raw).decode().rstrip("=")}
    _private(f, json.dumps(data))
    return data


def subscriptions() -> list[dict]:
    f = _subs_file()
    return json.loads(f.read_text()) if f.exists() else []


def subscribe(sub: dict, device_id: str | None) -> int:
    if not sub.get("endpoint") or not (sub.get("keys") or {}).get("p256dh"):
        raise ValueError("Not a push subscription")
    with _lock:
        subs = [s for s in subscriptions() if s["endpoint"] != sub["endpoint"]]
        subs.append({"endpoint": sub["endpoint"], "keys": sub["keys"], "device_id": device_id,
                     "added_at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
        _private(_subs_file(), json.dumps(subs, indent=2))
    return len(subs)


def unsubscribe(endpoint: str) -> None:
    with _lock:
        _private(_subs_file(), json.dumps([s for s in subscriptions() if s["endpoint"] != endpoint], indent=2))


def notify(title: str, body: str, url: str = "/#/library", contact: str = "mailto:owner@localhost") -> int:
    """Send to every subscribed device. Returns how many accepted it; drops expired subscriptions."""
    subs = subscriptions()
    if not subs:
        return 0
    from pywebpush import WebPushException, webpush

    k = keys()
    payload = json.dumps({"title": title, "body": body, "url": url})
    sent, gone = 0, []
    for s in subs:
        try:
            webpush({"endpoint": s["endpoint"], "keys": s["keys"]}, payload, vapid_private_key=k["private_pem"],
                    vapid_claims={"sub": contact}, ttl=24 * 3600)
            sent += 1
        except WebPushException as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            if status in (404, 410):
                gone.append(s["endpoint"])
            else:
                log.warning("push to %s failed: %s", s["endpoint"][:40], e)
        except Exception as e:  # network trouble: try again next time
            log.warning("push failed: %s", e)
    for endpoint in gone:
        unsubscribe(endpoint)
    return sent


def check_originals(s3) -> int:
    """Notify about restores that became ready since the last check."""
    from ..catalog import open_catalog
    from ..originals import status

    conn = open_catalog()
    try:
        waiting = [r[0] for r in conn.execute("SELECT photo_id FROM restore_requests WHERE status = 'in_progress'")]
        ready = 0
        for pid in waiting:
            if status(s3, conn, pid)["status"] == "ready":
                name = conn.execute("SELECT orig_name FROM media WHERE photo_id = ?", (pid,)).fetchone()
                notify("Original ready", f"{name[0] if name and name[0] else 'Your original'} can be downloaded now.",
                       url=f"/#/media/{pid}")
                ready += 1
        return ready
    finally:
        conn.close()
