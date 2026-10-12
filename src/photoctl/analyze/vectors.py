"""Photo vectors for plain-English search (~/.photoctl/embeddings.sqlite, this Mac only).

Kept out of the catalog: they're large, only the Mac can search them (it turns the query into a vector
with the same model), and they can always be recomputed.
"""

from __future__ import annotations

import os
import sqlite3
import threading

import numpy as np

from .. import paths

SCHEMA = """
CREATE TABLE IF NOT EXISTS vectors (photo_id TEXT NOT NULL, model TEXT NOT NULL, vec BLOB NOT NULL,
  PRIMARY KEY (photo_id, model));
-- which items each model has looked at (also those it couldn't: no local copy, unreadable file)
CREATE TABLE IF NOT EXISTS done (photo_id TEXT NOT NULL, step TEXT NOT NULL, model TEXT NOT NULL, at TEXT NOT NULL,
  error TEXT, PRIMARY KEY (photo_id, step, model));
"""


def open_vectors(path=None) -> sqlite3.Connection:
    path = path or paths.home() / "embeddings.sqlite"
    new = not os.path.exists(path)
    conn = sqlite3.connect(path, timeout=30, check_same_thread=False)
    if new:
        os.chmod(path, 0o600)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(SCHEMA)
    return conn


def save(conn: sqlite3.Connection, model: str, items: list[tuple[str, np.ndarray]]) -> None:
    conn.executemany("INSERT OR REPLACE INTO vectors (photo_id, model, vec) VALUES (?, ?, ?)",
                     [(pid, model, v.astype(np.float16).tobytes()) for pid, v in items])
    conn.commit()


class Index:
    """All vectors of one model in memory (float16), reloaded when new ones arrive."""

    def __init__(self):
        self._lock = threading.Lock()
        self._key = None
        self.ids: list[str] = []
        self.matrix = np.zeros((0, 0), np.float16)

    def load(self, conn: sqlite3.Connection, model: str) -> "Index":
        key = (model, *conn.execute("SELECT COUNT(*), MAX(rowid) FROM vectors WHERE model = ?", (model,)).fetchone())
        with self._lock:
            if key != self._key:
                rows = conn.execute("SELECT photo_id, vec FROM vectors WHERE model = ?", (model,)).fetchall()
                self.ids = [r[0] for r in rows]
                self.matrix = (np.stack([np.frombuffer(r[1], np.float16) for r in rows]) if rows
                               else np.zeros((0, 0), np.float16))
                self._key = key
        return self

    def scores(self, query: np.ndarray, only: set[str] | None = None) -> list[tuple[str, float]]:
        if not self.ids:
            return []
        sims = self.matrix.astype(np.float32) @ query.astype(np.float32)
        pairs = zip(self.ids, sims.tolist())
        return [(p, s) for p, s in pairs if only is None or p in only]


INDEX = Index()
