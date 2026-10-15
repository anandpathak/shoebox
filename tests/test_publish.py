import gzip
import sqlite3
from datetime import datetime, timedelta, timezone

from opkit import record
from shoebox.catalog import open_catalog
from shoebox.catalog.ops import Emitter
from shoebox.catalog.store import upsert_record
from shoebox.device import Device
from shoebox.publish import catalog_key, list_catalogs, manifest_key, publish_catalog

MAC = Device("01MACMACMACMACMACMACMACMAC", "Mac", "macos", "x")
T0 = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)


def test_publish_when_changed_and_rate_limited(bucket, tmp_path, sidecar):
    conn = open_catalog(tmp_path / "c.sqlite")
    rec = record(sidecar, 1)
    upsert_record(conn, rec, MAC)
    m = publish_catalog(conn, bucket, MAC, now=T0)
    assert m["item_count"] == 1 and m["contributed_count"] == 1 and m["platform"] == "macos"
    db = tmp_path / "dl.sqlite"
    db.write_bytes(gzip.decompress(bucket.get_bytes(catalog_key(MAC.device_id))))
    assert sqlite3.connect(db).execute("SELECT COUNT(*) FROM media").fetchone()[0] == 1

    assert publish_catalog(conn, bucket, MAC, now=T0 + timedelta(minutes=1)) is None   # unchanged
    Emitter(conn, MAC.device_id).emit("media.set", {"photo_id": rec["photo_id"], "field": "title", "value": "x"})
    assert publish_catalog(conn, bucket, MAC, now=T0 + timedelta(minutes=5)) is None   # too soon
    assert publish_catalog(conn, bucket, MAC, now=T0 + timedelta(minutes=11)) is not None
    assert [c["device_id"] for c in list_catalogs(bucket)] == [MAC.device_id]


def test_older_catalog_never_replaces_a_newer_one(bucket, tmp_path, sidecar):
    old = open_catalog(tmp_path / "old.sqlite")
    upsert_record(old, record(sidecar, 1), MAC)
    new = open_catalog(tmp_path / "new.sqlite")
    upsert_record(new, record(sidecar, 1), MAC)
    upsert_record(new, record(sidecar, 2), MAC)   # later HLCs
    assert publish_catalog(new, bucket, MAC, now=T0)
    assert publish_catalog(old, bucket, MAC, now=T0 + timedelta(hours=1), force=True) is None
    import json
    assert json.loads(bucket.get_bytes(manifest_key(MAC.device_id)))["item_count"] == 2
