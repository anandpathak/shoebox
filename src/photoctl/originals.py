"""Bring originals back from Deep Archive (LOCAL_APP_PLAN.md §8).

Deep Archive restores take up to 12 hours (Standard tier) or 48 hours (Bulk, cheaper). The restored
copy stays readable for `days`; the original object itself never changes.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from botocore.exceptions import ClientError

ARCHIVE_CLASSES = {"DEEP_ARCHIVE", "GLACIER"}
TIERS = {"standard": "Standard", "bulk": "Bulk"}
HOURS = {"Standard": 12, "Bulk": 48}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _raw_key(conn: sqlite3.Connection, photo_id: str) -> str:
    row = conn.execute("SELECT raw_key FROM media WHERE photo_id = ?", (photo_id,)).fetchone()
    if row is None:
        raise LookupError(f"{photo_id} isn't in the catalog")
    return row[0]


def _parse_restore_header(head: dict) -> tuple[str, str | None]:
    """→ (status, ready_until). S3 'Restore' header: ongoing-request="true" or "false", expiry-date="…"."""
    if head.get("StorageClass") not in ARCHIVE_CLASSES:
        return "available", None
    header = head.get("Restore")
    if not header:
        return "not_requested", None
    if 'ongoing-request="true"' in header:
        return "in_progress", None
    m = re.search(r'expiry-date="([^"]+)"', header)
    until = parsedate_to_datetime(m.group(1)).astimezone(timezone.utc).isoformat(timespec="seconds") if m else None
    if until and until < _now():
        return "expired", until
    return "ready", until


def _save(conn, photo_id, raw_key, tier, days, status, until, requested_at=None) -> dict:
    with conn:
        conn.execute(
            "INSERT INTO restore_requests (photo_id, raw_key, tier, days, requested_at, status, ready_until, checked_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(photo_id) DO UPDATE SET status = excluded.status, "
            "ready_until = excluded.ready_until, checked_at = excluded.checked_at, "
            "tier = CASE WHEN ? IS NULL THEN tier ELSE excluded.tier END, "
            "requested_at = COALESCE(?, requested_at)",
            (photo_id, raw_key, tier, days, requested_at or _now(), status, until, _now(), requested_at, requested_at))
    return dict(conn.execute("SELECT * FROM restore_requests WHERE photo_id = ?", (photo_id,)).fetchone())


def request(bucket, conn: sqlite3.Connection, photo_id: str, *, tier: str = "standard", days: int = 7) -> dict:
    key = _raw_key(conn, photo_id)
    head = bucket.head(key)
    if head is None:
        raise LookupError(f"original s3://{bucket.name}/{key} is missing")
    status, until = _parse_restore_header(head)
    t = TIERS[tier]
    if status in ("available", "ready", "in_progress"):
        return _save(conn, photo_id, key, t, days, status, until)
    try:
        bucket.client.restore_object(Bucket=bucket.name, Key=key,
                                     RestoreRequest={"Days": days, "GlacierJobParameters": {"Tier": t}})
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") != "RestoreAlreadyInProgress":
            raise
    status, until = _parse_restore_header(bucket.head(key))
    return _save(conn, photo_id, key, t, days, "in_progress" if status == "not_requested" else status, until,
                 requested_at=_now())


def status(bucket, conn: sqlite3.Connection, photo_id: str) -> dict:
    key = _raw_key(conn, photo_id)
    head = bucket.head(key)
    if head is None:
        raise LookupError(f"original s3://{bucket.name}/{key} is missing")
    st, until = _parse_restore_header(head)
    row = conn.execute("SELECT tier, days FROM restore_requests WHERE photo_id = ?", (photo_id,)).fetchone()
    return _save(conn, photo_id, key, row[0] if row else "Standard", row[1] if row else 7, st, until)


def eta_hours(tier: str) -> int:
    return HOURS.get(TIERS.get(tier, tier), 12)
