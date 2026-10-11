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
