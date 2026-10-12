"""Operations: the only way the catalog changes (LOCAL_APP_PLAN.md §4).

An op is a JSON object validated against `ops.schema.json` (also at `spec/ops.schema.json` for the
TypeScript side). `op_id` equals the op's HLC value, which is unique because it embeds the device id
and the clock never repeats.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from functools import lru_cache
from importlib import resources

from ..hlc import HLC, load_clock, save_clock

ENTITY_KEY = {
    "media.upsert": ("media", lambda b: b["record"]["photo_id"]),
    "media.source": ("media", lambda b: b["photo_id"]),
    "media.set": ("media", lambda b: b["photo_id"]),
    "media.caption": ("media", lambda b: b["photo_id"]),
    "media.delete": ("media", lambda b: b["photo_id"]),
    "media.restore": ("media", lambda b: b["photo_id"]),
    "tag.add": ("media", lambda b: b["photo_id"]),
    "tag.remove": ("media", lambda b: b["photo_id"]),
    "album.add": ("media", lambda b: b["photo_id"]),
    "album.remove": ("media", lambda b: b["photo_id"]),
    "media.person.add": ("media", lambda b: b["photo_id"]),
    "media.person.remove": ("media", lambda b: b["photo_id"]),
    "person.create": ("person", lambda b: b["person_id"]),
    "person.set": ("person", lambda b: b["person_id"]),
    "person.merge": ("person", lambda b: b["from"]),
    "device.register": ("device", lambda b: b["device_id"]),
    "device.set": ("device", lambda b: b["device_id"]),
}


class InvalidOp(ValueError):
    pass


@lru_cache(maxsize=1)
def _validator():
    from jsonschema import Draft202012Validator

    schema = json.loads(resources.files(__package__).joinpath("ops.schema.json").read_text())
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def validate(op: dict) -> dict:
    errors = sorted(_validator().iter_errors(op), key=lambda e: list(e.absolute_path))
    if errors:
        e = errors[0]
        where = "/".join(str(p) for p in e.absolute_path) or "(op)"
        raise InvalidOp(f"invalid {op.get('kind', 'op')}: {where}: {e.message}")
    return op


def make_op(clock: HLC, kind: str, body: dict) -> dict:
    if kind not in ENTITY_KEY:
        raise InvalidOp(f"unknown op kind {kind!r}")
    hlc = clock.now()
    entity_type, key = ENTITY_KEY[kind]
    return validate({"op_id": hlc, "hlc": hlc, "device_id": clock.device_id, "kind": kind,
                     "entity": f"{entity_type}:{key(body)}", "body": body})


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Emitter:
    """Creates ops on this device: validate → apply locally → remember in `ops` + `outbox` (one transaction)."""

    def __init__(self, conn: sqlite3.Connection, device_id: str):
        self.conn = conn
        self.clock = load_clock(conn, device_id)

    def emit(self, kind: str, body: dict) -> dict:
        return self.emit_many([(kind, body)])[0]

    def emit_many(self, items: list[tuple[str, dict]]) -> list[dict]:
        from .apply import apply_ops

        ops = [make_op(self.clock, kind, body) for kind, body in items]
        with self.conn:
            apply_ops(self.conn, ops, in_transaction=True)
            self.conn.executemany("INSERT OR IGNORE INTO outbox (op_id) VALUES (?)", [(o["op_id"],) for o in ops])
            save_clock(self.conn, self.clock)
        return ops


def observe_all(conn: sqlite3.Connection, clock: HLC, ops: list[dict]) -> None:
    for op in ops:
        clock.observe(op["hlc"])
    save_clock(conn, clock)


def op_from_row(row) -> dict:
    return {"op_id": row["op_id"], "hlc": row["hlc"], "device_id": row["device_id"], "kind": row["kind"],
            "entity": row["entity"], "body": json.loads(row["body"])}


def store_op(conn: sqlite3.Connection, op: dict) -> bool:
    """Record an applied op; False if it was already there (idempotency)."""
    cur = conn.execute(
        "INSERT OR IGNORE INTO ops (op_id, hlc, device_id, kind, entity, body, applied_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (op["op_id"], op["hlc"], op["device_id"], op["kind"], op["entity"],
         json.dumps(op["body"], ensure_ascii=False, separators=(",", ":"), sort_keys=True), _now_iso()))
    return cur.rowcount == 1
