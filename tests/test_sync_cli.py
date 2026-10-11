from click.testing import CliRunner

from photoctl import cli
from photoctl.runlock import SyncLock


def test_sync_command_runs_and_reports(bucket, monkeypatch):
    monkeypatch.setattr(cli, "_bucket", lambda name, profile: bucket)
    res = CliRunner().invoke(cli.main, ["sync", "--bucket", "test-archive"])
    assert res.exit_code == 0 and "Synced: pushed" in res.output


def test_second_concurrent_sync_is_skipped(bucket, monkeypatch):
    monkeypatch.setattr(cli, "_bucket", lambda name, profile: bucket)
    with SyncLock():
        res = CliRunner().invoke(cli.main, ["sync", "--bucket", "test-archive"])
    assert "already running" in res.output
