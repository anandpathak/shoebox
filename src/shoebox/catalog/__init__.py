"""The synced, queryable library (LOCAL_APP_PLAN.md §3). `state.sqlite` stays device-local; this one is shared."""

from __future__ import annotations

import sqlite3
from importlib import resources
from pathlib import Path

from .. import paths


def _migrations() -> dict[int, str]:
    """migrations/NNNN_name.sql, applied in order. Never edit a released migration; add a new one."""
    folder = resources.files(__package__).joinpath("migrations")
    return {int(f.name[:4]): f.read_text() for f in folder.iterdir() if f.name.endswith(".sql")}


SCHEMA_VERSION = max(_migrations())


def open_catalog(path: Path | None = None, *, any_thread: bool = False) -> sqlite3.Connection:
    """Open (creating and migrating if needed) the catalog with WAL + busy_timeout.

    `any_thread` lets one request's connection be used across the server's worker threads (it's
    still only used by one request at a time)."""
    path = Path(path) if path else paths.catalog_db()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=5.0, check_same_thread=not any_thread)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA foreign_keys = ON")
    migrate(conn)
    return conn


def schema_version(conn: sqlite3.Connection) -> int:
    try:
        row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    except sqlite3.OperationalError:
        return 0
    return int(row[0]) if row else 0


def _db_file(conn: sqlite3.Connection) -> str | None:
    row = conn.execute("PRAGMA database_list").fetchone()
    return row[2] if row and row[2] else None


def migrate(conn: sqlite3.Connection) -> int:
    current = schema_version(conn)
    if current > SCHEMA_VERSION:
        raise RuntimeError(f"catalog schema v{current} is newer than this shoebox (v{SCHEMA_VERSION}); upgrade shoebox")
    if current == SCHEMA_VERSION:
        return current
    # Several connections (threads or processes) may open the catalog at once: one migrates, the rest wait.
    # executescript() commits as it starts, so an SQLite transaction can't hold them off; a file lock can.
    db_file = _db_file(conn)
    if db_file is None:
        return _apply(conn)
    import fcntl

    with open(db_file + ".migrate-lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            return _apply(conn)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _apply(conn: sqlite3.Connection) -> int:
    current = schema_version(conn)
    for version, sql in sorted(_migrations().items()):
        if version <= current:
            continue
        with conn:
            conn.executescript(sql)
            conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?)", (str(version),))
    return schema_version(conn)


def epoch(conn: sqlite3.Connection) -> str:
    """Identifies this catalog file's lifetime; a rebuilt catalog gets a new epoch."""
    row = conn.execute("SELECT value FROM meta WHERE key = 'epoch'").fetchone()
    if row:
        return row[0]
    from ..device import new_ulid

    value = new_ulid()
    with conn:
        conn.execute("INSERT OR IGNORE INTO meta (key, value) VALUES ('epoch', ?)", (value,))
    return conn.execute("SELECT value FROM meta WHERE key = 'epoch'").fetchone()[0]
