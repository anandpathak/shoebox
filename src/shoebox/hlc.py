"""Hybrid logical clock (LOCAL_APP_PLAN.md §4).

An HLC value is the string "<wall_ms:013d>-<counter:04d>-<device_id>". Plain string comparison gives a
total order that respects causality even when device clocks drift: every new value is greater than
anything this device has created or observed, and `device_id` breaks ties.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass

MAX_COUNTER = 9999


@dataclass(frozen=True, order=True)
class Stamp:
    wall: int
    counter: int
    device_id: str

    def __str__(self) -> str:
        return f"{self.wall:013d}-{self.counter:04d}-{self.device_id}"

    @classmethod
    def parse(cls, value: str) -> "Stamp":
        wall, counter, device_id = value.split("-", 2)
        return cls(int(wall), int(counter), device_id)


class HLC:
    """Thread-safe clock for one device. Persist `last` (e.g. in catalog meta) to survive restarts."""

    def __init__(self, device_id: str, last: str | None = None, clock=lambda: int(time.time() * 1000)):
        self.device_id = device_id
        self._clock = clock
        self._lock = threading.Lock()
        s = Stamp.parse(last) if last else None
        self._wall, self._counter = (s.wall, s.counter) if s else (0, 0)

    @property
    def last(self) -> str:
        return str(Stamp(self._wall, self._counter, self.device_id))

    def now(self) -> str:
        with self._lock:
            physical = self._clock()
            if physical > self._wall:
                self._wall, self._counter = physical, 0
            else:
                self._bump()
            return self.last

    def observe(self, remote: str) -> None:
        """Advance past a value seen from another device (or our own, after a restore)."""
        r = Stamp.parse(remote)
        with self._lock:
            if r.wall > self._wall:
                self._wall, self._counter = r.wall, r.counter
            elif r.wall == self._wall and r.counter > self._counter:
                self._counter = r.counter

    def _bump(self) -> None:
        if self._counter >= MAX_COUNTER:  # 10k events in one ms: borrow the next millisecond
            self._wall, self._counter = self._wall + 1, 0
        else:
            self._counter += 1


def load_clock(conn: sqlite3.Connection, device_id: str) -> HLC:
    row = conn.execute("SELECT value FROM meta WHERE key = 'hlc_last'").fetchone()
    last = row[0] if row else None
    if last and Stamp.parse(last).device_id != device_id:
        last = f"{Stamp.parse(last).wall:013d}-{Stamp.parse(last).counter:04d}-{device_id}"
    return HLC(device_id, last)


def save_clock(conn: sqlite3.Connection, clock: HLC) -> None:
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('hlc_last', ?)", (clock.last,))
