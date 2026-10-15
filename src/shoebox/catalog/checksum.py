"""Deterministic fingerprint of the synced catalog state, used to prove devices have converged.

Covers every replicated table. Leaves out local bookkeeping (op log timestamps, outbox, cursors,
meta) and derived data (the FTS index), which legitimately differ between devices.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3

TABLES = {
    "media": "photo_id",
    "media_sources": "photo_id, device_id, source_ref",
    "devices": "device_id",
    "media_tags": "photo_id, tag, add_op",
    "media_albums": "photo_id, album, add_op",
    "people": "person_id",
    "media_people": "photo_id, person_id, add_op",
    "tombstones": "add_op",
}


def checksum(conn: sqlite3.Connection) -> str:
    h = hashlib.sha256()
    for table, order in TABLES.items():
        cur = conn.execute(f"SELECT * FROM {table} ORDER BY {order}")
        cols = [d[0] for d in cur.description]
        h.update(f"{table}:{','.join(cols)}\n".encode())
        for row in cur:
            h.update(json.dumps(list(row), separators=(",", ":"), default=str).encode() + b"\n")
    return h.hexdigest()
