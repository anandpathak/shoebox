from click.testing import CliRunner

from shoebox import cli
from shoebox.runlock import SyncLock


def test_sync_command_runs_and_reports(bucket, monkeypatch):
    monkeypatch.setattr(cli, "_bucket", lambda name, profile: bucket)
    res = CliRunner().invoke(cli.main, ["sync", "--bucket", "test-archive"])
    assert res.exit_code == 0 and "Synced: pushed" in res.output


def test_second_concurrent_sync_is_skipped(bucket, monkeypatch):
    monkeypatch.setattr(cli, "_bucket", lambda name, profile: bucket)
    with SyncLock():
        res = CliRunner().invoke(cli.main, ["sync", "--bucket", "test-archive"])
    assert "already running" in res.output


def test_devices_lists_registry_after_sync(bucket, monkeypatch):
    monkeypatch.setattr(cli, "_bucket", lambda name, profile: bucket)
    CliRunner().invoke(cli.main, ["sync", "--bucket", "test-archive"])
    from shoebox.sync import list_registry
    other = {"device_id": "PHONE1", "name": "Pixel 8", "platform": "android", "last_seen": "2026-01-01T00:00:00+00:00",
             "checksum": "ab" * 32, "media": 4, "pending_ops": 0, "ops": 9, "shoebox_version": "web"}
    import json
    bucket.put_bytes("sync/devices/PHONE1.json", json.dumps(other).encode())
    assert {d["platform"] for d in list_registry(bucket)} == {"android", "macos"}
    res = CliRunner().invoke(cli.main, ["devices", "--bucket", "test-archive"])
    assert "(this device)" in res.output and "Pixel 8 · android" in res.output
