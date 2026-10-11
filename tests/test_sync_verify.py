from click.testing import CliRunner

from opkit import record
from photoctl import cli
from photoctl.catalog import open_catalog
from photoctl.catalog.ops import Emitter
from photoctl.catalog.store import upsert_record
from photoctl.device import Device
from photoctl.sync import sync_once, verify

OTHER = Device("01OTHEROTHEROTHEROTHEROTHE", "Mini", "macos", "x")


def test_verify_reports_converged_and_differences(bucket, tmp_path, sidecar, device, monkeypatch):
    monkeypatch.setattr(cli, "_bucket", lambda name, profile: bucket)
    other = open_catalog(tmp_path / "other.sqlite")
    rec = record(sidecar, 1)
    upsert_record(other, rec, OTHER)
    sync_once(other, bucket, OTHER)

    res = CliRunner().invoke(cli.main, ["sync", "--bucket", "test-archive", "verify"])
    assert res.exit_code == 1 and "differs: Mini" in res.output   # other hasn't seen our registration yet
    sync_once(other, bucket, OTHER)
    res = CliRunner().invoke(cli.main, ["sync", "--bucket", "test-archive", "verify"])
    assert res.exit_code == 0 and "converged: 2 devices" in res.output

    Emitter(other, OTHER.device_id).emit("media.set", {"photo_id": rec["photo_id"], "field": "title", "value": "x"})
    me = open_catalog()
    assert verify(me, bucket, device)["converged"]  # other hasn't synced its edit yet: registry unchanged
