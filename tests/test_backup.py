import sqlite3
from datetime import datetime, timezone

import pytest

from photoctl import paths
from photoctl.backup import backup, list_backups, restore
from photoctl.catalog import open_catalog
from photoctl.catalog.store import upsert_record
from photoctl.state import State


def _seed(sidecar, device):
    State().upsert("applephotos:x", status="done", photo_id="P1", sha256="aa", raw_key="raw/x.heic", bytes_raw=1)
    conn = open_catalog()
    upsert_record(conn, sidecar, device)
    conn.close()


def test_backup_then_restore_after_loss(bucket, sidecar, device):
    _seed(sidecar, device)
    res = backup(bucket, device, now=datetime(2026, 10, 11, 9, 0, tzinfo=timezone.utc))
    assert set(res.files) >= {"state.sqlite.gz", "catalog.sqlite.gz", "device.json"}
    assert res.monthly == f"backups/{device.device_id}/monthly/2026-10/"

    paths.state_db().unlink()
    paths.catalog_db().unlink()
    out = restore(bucket, device.device_id)
    assert set(out["restored"]) == {"state.sqlite", "catalog.sqlite"}
    assert State().is_done("applephotos:x")
    assert sqlite3.connect(paths.catalog_db()).execute("SELECT COUNT(*) FROM media").fetchone()[0] == 1


def test_restore_keeps_bak_and_picks_point_in_time(bucket, sidecar, device):
    _seed(sidecar, device)
    backup(bucket, device, now=datetime(2026, 10, 1, tzinfo=timezone.utc))
    State().upsert("applephotos:y", status="done", photo_id="P2", sha256="bb", raw_key="raw/y.heic", bytes_raw=1)
    backup(bucket, device, now=datetime(2026, 10, 5, tzinfo=timezone.utc))
    assert [b["created_at"][:10] for b in list_backups(bucket, device.device_id)] == ["2026-10-05", "2026-10-01"]

    restore(bucket, device.device_id, at=datetime(2026, 10, 2))
    assert State().is_done("applephotos:x") and not State().is_done("applephotos:y")
    assert paths.state_db().with_suffix(".sqlite.bak").exists()


def test_restore_rejects_tampered_backup(bucket, sidecar, device):
    _seed(sidecar, device)
    res = backup(bucket, device)
    bucket.put_bytes(res.files["state.sqlite.gz"]["key"], b"not a database")
    with pytest.raises(RuntimeError, match="checksum mismatch"):
        restore(bucket, device.device_id)
    assert State().is_done("applephotos:x")  # nothing was replaced


def test_new_machine_recovers_identity(bucket, sidecar, device):
    _seed(sidecar, device)
    backup(bucket, device)
    for f in (paths.state_db(), paths.catalog_db(), paths.device_file()):
        f.unlink()
    restore(bucket, device.device_id, with_identity=True)
    from photoctl.device import load_device
    assert load_device(create=False).device_id == device.device_id


def test_backups_are_tagged_for_lifecycle(bucket, sidecar, device):
    _seed(sidecar, device)
    res = backup(bucket, device, now=datetime(2026, 10, 11, tzinfo=timezone.utc))
    tags = lambda k: {t["Key"]: t["Value"] for t in bucket.client.get_object_tagging(Bucket=bucket.name, Key=k)["TagSet"]}
    assert tags(res.files["state.sqlite.gz"]["key"]) == {"retention": "daily"}
    assert tags(res.prefix + "manifest.json") == {"retention": "daily"}
    assert tags(res.monthly + "state.sqlite.gz") == {"retention": "monthly"}


def test_backup_while_another_thread_writes(bucket, sidecar, device, tmp_path):
    import gzip
    import threading

    from opkit import record
    from photoctl.catalog.export import integrity_ok

    _seed(sidecar, device)
    stop = threading.Event()

    def writer():
        conn = open_catalog()
        n = 100
        while not stop.is_set():
            upsert_record(conn, record(sidecar, n), device)
            n += 1
        conn.close()

    t = threading.Thread(target=writer)
    t.start()
    try:
        results = [backup(bucket, device, now=datetime(2026, 10, 11, 9, i, tzinfo=timezone.utc)) for i in range(3)]
    finally:
        stop.set()
        t.join()
    for res in results:
        db = tmp_path / "check.sqlite"
        db.write_bytes(gzip.decompress(bucket.get_bytes(res.files["catalog.sqlite.gz"]["key"])))
        assert integrity_ok(db)[0]


def test_manifest_and_latest_pointer(bucket, sidecar, device):
    import json

    from photoctl.backup import last_local_backup

    _seed(sidecar, device)
    new = backup(bucket, device, now=datetime(2026, 10, 12, tzinfo=timezone.utc))
    backup(bucket, device, now=datetime(2026, 10, 11, tzinfo=timezone.utc))   # an older, late finisher
    latest = json.loads(bucket.get_bytes(f"backups/{device.device_id}/LATEST.json"))
    assert latest["prefix"] == new.prefix
    manifest = json.loads(bucket.get_bytes(new.prefix + "manifest.json"))
    assert manifest["catalog"]["schema_version"] >= 2 and len(manifest["catalog"]["checksum"]) == 64
    assert manifest["catalog"]["unpushed_ops"] == 3   # nothing has synced in this test
    assert last_local_backup()["prefix"].startswith(f"backups/{device.device_id}/")
