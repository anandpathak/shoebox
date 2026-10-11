"""Exchange ops through S3 (LOCAL_APP_PLAN.md §7).

  sync/ops/<device_id>/<first_hlc>.jsonl.gz   one batch of this device's ops, written once (If-None-Match)

Push sends the outbox; pull lists every device's folder after its cursor, applies everything new in
one transaction and moves the cursors in that same transaction, so a crash never skips or repeats.
"""

from __future__ import annotations

import gzip
import json
import logging
import sqlite3
from dataclasses import dataclass, field

from .catalog.apply import apply_ops
from .catalog.ops import InvalidOp, op_from_row, validate
from .hlc import load_clock, save_clock

OPS_PREFIX = "sync/ops/"
BATCH_SIZE = 5000
log = logging.getLogger(__name__)


@dataclass
class SyncResult:
    pushed_ops: int = 0
    pushed_batches: int = 0
    pulled_batches: int = 0
    applied_ops: int = 0
    rejected_batches: list[str] = field(default_factory=list)


def batch_key(device_id: str, first_hlc: str) -> str:
    return f"{OPS_PREFIX}{device_id}/{first_hlc}.jsonl.gz"


def encode_batch(ops: list[dict]) -> bytes:
    lines = "".join(json.dumps(o, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n" for o in ops)
    return gzip.compress(lines.encode("utf-8"), mtime=0)


def decode_batch(data: bytes) -> list[dict]:
    return [json.loads(line) for line in gzip.decompress(data).decode("utf-8").splitlines() if line.strip()]


def pending_ops(conn: sqlite3.Connection, device_id: str, limit: int = BATCH_SIZE) -> list[dict]:
    rows = conn.execute(
        "SELECT o.* FROM outbox x JOIN ops o ON o.op_id = x.op_id WHERE o.device_id = ? ORDER BY o.hlc LIMIT ?",
        (device_id, limit)).fetchall()
    return [op_from_row(r) for r in rows]


def outbox_devices(conn: sqlite3.Connection) -> list[str]:
    """Devices with unpushed ops here: this device, plus phones whose ops the server holds for them."""
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT o.device_id FROM outbox x JOIN ops o ON o.op_id = x.op_id ORDER BY 1")]


def push(conn: sqlite3.Connection, bucket, result: SyncResult | None = None) -> SyncResult:
    result = result or SyncResult()
    for device_id in outbox_devices(conn):
        while ops := pending_ops(conn, device_id):
            key = batch_key(device_id, ops[0]["hlc"])
            if bucket.put_bytes_if(key, encode_batch(ops), if_none_match=True,
                                   content_type="application/gzip") is None:
                # A batch with this first op already exists (we crashed after uploading last time, or this
                # is a restored device). Clear what it holds and carry on with the rest.
                existing = {o["op_id"] for o in decode_batch(bucket.get_bytes(key))}
                if not existing & {o["op_id"] for o in ops}:
                    raise RuntimeError(f"{key} exists but holds none of the ops being pushed")
                ops = [o for o in ops if o["op_id"] in existing]
            else:
                result.pushed_batches += 1
                result.pushed_ops += len(ops)
            with conn:
                conn.executemany("DELETE FROM outbox WHERE op_id = ?", [(o["op_id"],) for o in ops])
    return result


def pull(conn: sqlite3.Connection, bucket, device_id: str, result: SyncResult | None = None) -> SyncResult:
    result = result or SyncResult()
    cursors = dict(conn.execute("SELECT device_id, last_key FROM sync_cursor").fetchall())
    incoming: list[dict] = []
    new_cursors: dict[str, str] = {}
    for prefix in bucket.list_prefixes(OPS_PREFIX):
        source = prefix[len(OPS_PREFIX):].rstrip("/")
        for key in bucket.list_keys(prefix, start_after=cursors.get(source)):
            try:
                ops = decode_batch(bucket.get_bytes(key))
                for op in ops:
                    validate(op)
                    if op["device_id"] != source:
                        raise InvalidOp(f"op {op['op_id']} is from {op['device_id']}, not {source}")
            except (InvalidOp, ValueError, OSError) as e:  # OSError: bad gzip
                log.warning("rejected %s: %s", key, e)
                result.rejected_batches.append(key)
            else:
                incoming += ops
            result.pulled_batches += 1
            new_cursors[source] = key
    if not new_cursors:
        return result
    clock = load_clock(conn, device_id)
    with conn:
        result.applied_ops += apply_ops(conn, incoming, in_transaction=True, trusted=True)
        conn.executemany("INSERT OR REPLACE INTO sync_cursor (device_id, last_key) VALUES (?, ?)",
                         list(new_cursors.items()))
        for op in incoming:
            clock.observe(op["hlc"])
        save_clock(conn, clock)
    return result


def sync_once(conn: sqlite3.Connection, bucket, device) -> SyncResult:
    """Push this device's outbox, then pull everyone else's ops. Caller holds the SyncLock."""
    result = push(conn, bucket)
    pull(conn, bucket, device.device_id, result)
    return result
