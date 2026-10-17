"""Local state DB so runs are idempotent and resumable."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from . import paths

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
  source_ref     TEXT PRIMARY KEY,   -- 'applephotos:<uuid>'
  photo_id       TEXT,
  sha256         TEXT,
  status         TEXT,               -- done | no_derivative | missing_original | failed
  raw_key        TEXT,
  compressed_key TEXT,
  meta_key       TEXT,
  bytes_raw      INTEGER,
  bytes_compressed INTEGER,
  error          TEXT,
  updated_at     TEXT
);
CREATE INDEX IF NOT EXISTS items_sha ON items(sha256);
"""


class State:
    def __init__(self, path: Path | None = None):
        path = Path(path) if path else paths.state_db()
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(items)")}
        if "album" not in cols:
            # The "Archived to S3" album: 0 still to add · 1 in the album · 2 Photos couldn't add it ·
            # 3 already deleted from Photos (nothing to do). Rows from before this column start at 1; the
            # first reconcile (archiver.reconcile_album) corrects them against the real album.
            self.db.execute("ALTER TABLE items ADD COLUMN album INTEGER NOT NULL DEFAULT 1")
        if "album_error" not in cols:
            self.db.execute("ALTER TABLE items ADD COLUMN album_error TEXT")
        self.db.commit()

    def get(self, source_ref: str) -> sqlite3.Row | None:
        return self.db.execute("SELECT * FROM items WHERE source_ref = ?", (source_ref,)).fetchone()

    def is_done(self, source_ref: str) -> bool:
        row = self.get(source_ref)
        return row is not None and row["status"] in ("done", "no_derivative")

    def upsert(self, source_ref: str, **fields) -> None:
        fields["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        cols = ["source_ref", *fields]
        self.db.execute(
            f"INSERT INTO items ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))}) "
            f"ON CONFLICT(source_ref) DO UPDATE SET {', '.join(f'{c}=excluded.{c}' for c in fields)}",
            [source_ref, *fields.values()],
        )
        self.db.commit()

    def album_pending(self, limit: int = 5000) -> list[str]:
        """Apple Photos items archived but not yet added to the album (e.g. Photos was busy, or a restart)."""
        return [r[0] for r in self.db.execute(
            "SELECT source_ref FROM items WHERE album = 0 AND status IN ('done', 'no_derivative') "
            "AND source_ref LIKE 'applephotos:%' ORDER BY updated_at LIMIT ?", (limit,))]

    def album_done(self, refs: list[str]) -> None:
        self.set_album(refs, 1)

    def set_album(self, refs: list[str], value: int, error: str | None = None) -> None:
        self.db.executemany("UPDATE items SET album = ?, album_error = ? WHERE source_ref = ?",
                            [(value, error, r) for r in refs])
        self.db.commit()

    def apple_archived(self) -> list[sqlite3.Row]:
        return self.db.execute("SELECT source_ref, album FROM items WHERE status IN ('done', 'no_derivative') "
                               "AND source_ref LIKE 'applephotos:%'").fetchall()

    def album_summary(self) -> dict:
        names = {0: "to_add", 1: "in_album", 2: "could_not_add", 3: "gone"}
        rows = self.db.execute("SELECT album, COUNT(*) FROM items WHERE status IN ('done', 'no_derivative') "
                               "AND source_ref LIKE 'applephotos:%' GROUP BY album").fetchall()
        out = {v: 0 for v in names.values()}
        for album, n in rows:
            out[names.get(album, "to_add")] += n
        out["problems"] = [{"uuid": r[0].split(":", 1)[1], "error": r[1]} for r in self.db.execute(
            "SELECT source_ref, album_error FROM items WHERE album = 2 ORDER BY updated_at DESC LIMIT 50")]
        return out

    def counts(self) -> dict[str, int]:
        return dict(self.db.execute("SELECT status, COUNT(*) FROM items GROUP BY status").fetchall())

    def sample_done(self, n: int) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT * FROM items WHERE status IN ('done','no_derivative') ORDER BY RANDOM() LIMIT ?", (n,)
        ).fetchall()
