import sqlite3

from opkit import record
from shoebox.catalog import open_catalog
from shoebox.catalog.checksum import checksum
from shoebox.catalog.ops import Emitter
from shoebox.catalog.store import upsert_record
from shoebox.device import Device
from shoebox.sync import OPS_PREFIX, batch_key, decode_batch, encode_batch, pull, push

MAC = Device("01MACMACMACMACMACMACMACMAC", "Mac", "macos", "x")
MINI = Device("01MINIMINIMINIMINIMINIMINI", "Mini", "macos", "x")


def outbox(conn):
    return conn.execute("SELECT COUNT(*) FROM outbox").fetchone()[0]


def test_push_then_pull_on_another_device(bucket, tmp_path, sidecar):
    a, b = open_catalog(tmp_path / "a.sqlite"), open_catalog(tmp_path / "b.sqlite")
    upsert_record(a, record(sidecar, 1), MAC)
    res = push(a, bucket)
    assert res.pushed_batches == 1 and res.pushed_ops == 3 and outbox(a) == 0
    keys = bucket.list_keys(OPS_PREFIX)
    assert keys == [batch_key(MAC.device_id, keys[0].rsplit("/", 1)[1].removesuffix(".jsonl.gz"))]

    got = pull(b, bucket, MINI.device_id)
    assert got.applied_ops == 3 and checksum(a) == checksum(b)
    assert pull(b, bucket, MINI.device_id).pulled_batches == 0  # cursor moved


def test_crash_after_upload_before_outbox_clear_loses_nothing(bucket, tmp_path, sidecar):
    a = open_catalog(tmp_path / "a.sqlite")
    upsert_record(a, record(sidecar, 1), MAC)
    push(a, bucket)
    # simulate: the next push uploaded but crashed before clearing its outbox
    upsert_record(a, record(sidecar, 2), MAC)
    from shoebox.sync import pending_ops
    ops = pending_ops(a, MAC.device_id)
    bucket.put_bytes(batch_key(MAC.device_id, ops[0]["hlc"]), encode_batch(ops[:1]))
    res = push(a, bucket)  # first batch exists with 1 op: cleared, rest pushed in a new batch
    assert outbox(a) == 0 and res.pushed_ops == len(ops) - 1
    b = open_catalog(tmp_path / "b.sqlite")
    pull(b, bucket, MINI.device_id)
    assert checksum(a) == checksum(b)
    every = [o["op_id"] for k in bucket.list_keys(OPS_PREFIX) for o in decode_batch(bucket.get_bytes(k))]
    assert len(every) == len(set(every))  # no op uploaded twice


def test_two_devices_edit_and_converge(bucket, tmp_path, sidecar):
    a, b = open_catalog(tmp_path / "a.sqlite"), open_catalog(tmp_path / "b.sqlite")
    rec = record(sidecar, 1)
    upsert_record(a, rec, MAC)
    push(a, bucket)
    pull(b, bucket, MINI.device_id)
    Emitter(a, MAC.device_id).emit("media.set", {"photo_id": rec["photo_id"], "field": "title", "value": "A"})
    Emitter(b, MINI.device_id).emit("media.set", {"photo_id": rec["photo_id"], "field": "title", "value": "B"})
    Emitter(b, MINI.device_id).emit("tag.add", {"photo_id": rec["photo_id"], "tag": "mini"})
    for conn, dev in ((a, MAC), (b, MINI), (a, MAC)):
        push(conn, bucket)
        pull(conn, bucket, dev.device_id)
    assert checksum(a) == checksum(b)
    assert a.execute("SELECT title FROM media").fetchone()[0] == "B"  # B's edit has the higher HLC


def test_bad_batches_are_skipped_not_fatal(bucket, tmp_path, sidecar):
    a, b = open_catalog(tmp_path / "a.sqlite"), open_catalog(tmp_path / "b.sqlite")
    upsert_record(a, record(sidecar, 1), MAC)
    push(a, bucket)
    bucket.put_bytes(f"{OPS_PREFIX}EVIL/0000000000001-0000-EVIL.jsonl.gz", b"not gzip")
    spoof = decode_batch(bucket.get_bytes(bucket.list_keys(f"{OPS_PREFIX}{MAC.device_id}/")[0]))
    bucket.put_bytes(f"{OPS_PREFIX}OTHER/0000000000001-0000-OTHER.jsonl.gz", encode_batch(spoof))
    res = pull(b, bucket, MINI.device_id)
    assert len(res.rejected_batches) == 2 and res.applied_ops == 3
    assert sqlite3.connect(tmp_path / "b.sqlite").execute("SELECT COUNT(*) FROM media").fetchone()[0] == 1
