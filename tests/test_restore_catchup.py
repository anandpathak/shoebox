from click.testing import CliRunner

from opkit import record
from photoctl import cli, paths
from photoctl.backup import backup
from photoctl.catalog import open_catalog
from photoctl.catalog.checksum import checksum
from photoctl.catalog.ops import Emitter
from photoctl.catalog.store import upsert_record
from photoctl.device import Device
from photoctl.sync import sync_once

PEER = Device("01PEERPEERPEERPEERPEERPEER", "Mini", "macos", "x")


def test_restore_then_sync_converges_with_peers(bucket, tmp_path, sidecar, device, monkeypatch):
    monkeypatch.setattr(cli, "_bucket", lambda name, profile: bucket)
    me = open_catalog()
    rec = record(sidecar, 1)
    upsert_record(me, rec, device)
    sync_once(me, bucket, device)
    backup(bucket, device)

    # after the backup: my own edit (pushed) and a peer's edit
    Emitter(me, device.device_id).emit("tag.add", {"photo_id": rec["photo_id"], "tag": "after-backup"})
    sync_once(me, bucket, device)
    me.close()
    peer = open_catalog(tmp_path / "peer.sqlite")
    sync_once(peer, bucket, PEER)
    Emitter(peer, PEER.device_id).emit("media.set", {"photo_id": rec["photo_id"], "field": "title", "value": "Peer"})
    sync_once(peer, bucket, PEER)

    for suffix in ("", "-wal", "-shm"):  # disk failure
        (paths.home() / f"catalog.sqlite{suffix}").unlink(missing_ok=True)
    res = CliRunner().invoke(cli.main, ["db", "restore", "--bucket", "test-archive", "--yes"])
    assert res.exit_code == 0, res.output
    assert "Catching up" in res.output

    sync_once(peer, bucket, PEER)
    mine = open_catalog()
    assert checksum(mine) == checksum(peer)
    tags = [r[0] for r in mine.execute("SELECT tag FROM media_tags WHERE source = 'user'")]
    assert tags == ["after-backup"] and mine.execute("SELECT title FROM media").fetchone()[0] == "Peer"
    # the restored device keeps creating ops with fresh, never-reused ids
    op = Emitter(mine, device.device_id).emit("tag.add", {"photo_id": rec["photo_id"], "tag": "new"})
    assert mine.execute("SELECT COUNT(*) FROM ops WHERE op_id = ?", (op["op_id"],)).fetchone()[0] == 1
    assert op["hlc"] > max(r[0] for r in peer.execute("SELECT hlc FROM ops"))
