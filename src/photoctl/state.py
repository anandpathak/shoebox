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

    def counts(self) -> dict[str, int]:
        return dict(self.db.execute("SELECT status, COUNT(*) FROM items GROUP BY status").fetchall())

    def sample_done(self, n: int) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT * FROM items WHERE status IN ('done','no_derivative') ORDER BY RANDOM() LIMIT ?", (n,)
        ).fetchall()
