import time
from datetime import datetime, timedelta, timezone

from opkit import record
from photoctl.catalog import open_catalog
from photoctl.catalog.checksum import checksum
from photoctl.catalog.ops import Emitter
from photoctl.catalog.store import upsert_record
from photoctl.device import Device
from photoctl.snapshot import Lease, bootstrap, needs_snapshot, publish_snapshot, read_head
from photoctl.sync import pull, push

MAC = Device("01MACMACMACMACMACMACMACMAC", "Mac", "macos", "x")
NEW = Device("01NEWNEWNEWNEWNEWNEWNEWNEW", "New Mac", "macos", "x")


def test_lease_is_exclusive_and_expires(bucket):
    t = [datetime(2026, 10, 11, tzinfo=timezone.utc)]
    a = Lease(bucket, "A", now=lambda: t[0])
    b = Lease(bucket, "B", now=lambda: t[0])
    assert a.acquire() and not b.acquire()
    assert a.renew()
    t[0] += timedelta(seconds=301)          # A crashed and never released
    assert b.acquire() and not a.renew()    # B takes over; A's stale etag can't renew
    b.release()
    assert a.acquire()


def test_snapshot_bootstraps_a_new_device_then_catches_up(bucket, tmp_path, sidecar):
    src = open_catalog(tmp_path / "mac.sqlite")
    recs = [record(sidecar, n) for n in range(300)]
    for r in recs:
        upsert_record(src, r, MAC)
    push(src, bucket)
    pull(src, bucket, MAC.device_id)         # as in sync_once: push, pull, then snapshot
    assert needs_snapshot(src, bucket)
    head = publish_snapshot(src, bucket, MAC)
    assert head["media"] == 300 and read_head(bucket)[0]["etag"] == head["etag"]
    assert not needs_snapshot(src, bucket)

    # more changes after the snapshot
    Emitter(src, MAC.device_id).emit("media.set", {"photo_id": recs[0]["photo_id"], "field": "title", "value": "Late"})
    push(src, bucket)

    new_path = tmp_path / "new.sqlite"
    started = time.monotonic()
    assert bootstrap(bucket, new_path)["media"] == 300
    new = open_catalog(new_path)
    res = pull(new, bucket, NEW.device_id)
    assert time.monotonic() - started < 60
    assert res.applied_ops == 1 and res.pulled_batches == 1  # only the batch made after the snapshot
    assert checksum(new) == checksum(src)


def test_bootstrap_leaves_existing_catalogs_alone(bucket, tmp_path, sidecar):
    src = open_catalog(tmp_path / "mac.sqlite")
    upsert_record(src, record(sidecar, 1), MAC)
    publish_snapshot(src, bucket, MAC)
    assert bootstrap(bucket, tmp_path / "mac.sqlite") is None


def test_publish_is_skipped_while_someone_else_holds_the_lease(bucket, tmp_path, sidecar):
    src = open_catalog(tmp_path / "mac.sqlite")
    upsert_record(src, record(sidecar, 1), MAC)
    assert Lease(bucket, "OTHER").acquire()
    assert publish_snapshot(src, bucket, MAC) is None
