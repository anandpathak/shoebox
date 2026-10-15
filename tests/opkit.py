"""Helpers for building ops from simulated devices in tests."""

from __future__ import annotations

import copy
import hashlib
import sqlite3

from shoebox.catalog import open_catalog
from shoebox.catalog.ops import make_op
from shoebox.hlc import HLC


def pid_for(n: int) -> str:
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
    h = hashlib.sha256(str(n).encode()).digest()
    return "".join(alphabet[b % 32] for b in h[:26])


class Dev:
    """A simulated device with a controllable wall clock."""

    def __init__(self, device_id: str, start_ms: int = 1_700_000_000_000):
        self.device_id = device_id
        self.t = start_ms
        self.clock = HLC(device_id, clock=lambda: self.t)

    def op(self, kind: str, body: dict, advance: int = 1) -> dict:
        self.t += advance
        return make_op(self.clock, kind, body)

    def see(self, ops) -> None:
        for o in ops:
            self.clock.observe(o["hlc"])


def record(sidecar: dict, n: int = 1, **apple) -> dict:
    rec = copy.deepcopy(sidecar)
    pid = pid_for(n)
    rec["photo_id"] = pid
    rec["file"]["sha256"] = hashlib.sha256(pid.encode()).hexdigest()
    for k in ("raw", "compressed", "thumb", "meta"):
        rec["keys"][k] = rec["keys"][k].replace(sidecar["photo_id"], pid)
    rec["apple"].update(apple)
    return rec


def fresh(tmp_path, name: str) -> sqlite3.Connection:
    return open_catalog(tmp_path / f"{name}.sqlite")


def media(conn, pid):
    return conn.execute("SELECT * FROM media WHERE photo_id = ?", (pid,)).fetchone()


def tags(conn, pid):
    return sorted(r[0] for r in conn.execute(
        "SELECT DISTINCT tag FROM media_tags WHERE photo_id = ? AND add_op NOT IN (SELECT add_op FROM tombstones)",
        (pid,)))
