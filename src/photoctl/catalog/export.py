"""Consistent, compact catalog files for download and backup (LOCAL_APP_PLAN.md §11.2)."""

from __future__ import annotations

import gzip
import shutil
import sqlite3
import tempfile
from pathlib import Path


def snapshot(src: sqlite3.Connection, dest: Path) -> None:
    """Copy a live database with SQLite's online backup API (safe while others write)."""
    dest.unlink(missing_ok=True)
    out = sqlite3.connect(dest)
    try:
        src.backup(out)
    finally:
        out.close()


def integrity_ok(path: Path) -> tuple[bool, str]:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        result = conn.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        conn.close()
    return result == "ok", result


def _keep_only_device(conn: sqlite3.Connection, device_id: str) -> None:
    keep = "SELECT photo_id FROM media_sources WHERE device_id = :d"
    with conn:
        for table in ("media_tags", "media_albums", "media_people", "media_fts", "media_sources", "media"):
            conn.execute(f"DELETE FROM {table} WHERE photo_id NOT IN ({keep})", {"d": device_id})
        conn.execute("DELETE FROM people WHERE person_id NOT IN (SELECT person_id FROM media_people) "
                     "AND person_id NOT IN (SELECT merged_into FROM people WHERE merged_into IS NOT NULL)")
        # Op history describes the whole library, not one device's slice.
        conn.execute("DELETE FROM ops")
        conn.execute("DELETE FROM outbox")
        conn.execute("DELETE FROM sync_cursor")
        conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('export_flavour', 'contributed'), "
                     "('export_device', ?)", (device_id,))


def export_catalog(src: sqlite3.Connection, dest: Path, *, flavour: str = "full", device_id: str | None = None,
                   gzip_output: bool | None = None) -> Path:
    """Write `full` (everything) or `contributed` (only media sourced from `device_id`) to `dest`.

    Output is VACUUM'd and integrity-checked; gzip when `dest` ends in .gz (or gzip_output=True).
    """
    if flavour not in ("full", "contributed"):
        raise ValueError("flavour must be 'full' or 'contributed'")
    if flavour == "contributed" and not device_id:
        raise ValueError("contributed export needs a device_id")
    dest = Path(dest)
    gz = dest.suffix == ".gz" if gzip_output is None else gzip_output
    with tempfile.TemporaryDirectory(prefix="photoctl-export-") as tmp:
        work = Path(tmp) / "catalog.sqlite"
        snapshot(src, work)
        conn = sqlite3.connect(work)
        try:
            conn.execute("PRAGMA journal_mode = DELETE")
            if flavour == "contributed":
                _keep_only_device(conn, device_id)
            conn.execute("VACUUM")
        finally:
            conn.close()
        ok, msg = integrity_ok(work)
        if not ok:
            raise RuntimeError(f"exported catalog failed integrity_check: {msg}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        if gz:
            with open(work, "rb") as f_in, gzip.open(dest, "wb", compresslevel=6) as f_out:
                shutil.copyfileobj(f_in, f_out)
        else:
            shutil.copyfile(work, dest)
    return dest
