import plistlib
import subprocess
from datetime import datetime, timedelta, timezone

from click.testing import CliRunner

from shoebox import cli


def test_install_and_uninstall_write_a_launch_agent(tmp_path, monkeypatch):
    monkeypatch.setenv("SHOEBOX_LAUNCHAGENTS_DIR", str(tmp_path / "agents"))
    res = CliRunner().invoke(cli.main, ["db", "schedule", "install", "--bucket", "b", "--at", "04:15"])
    assert res.exit_code == 0, res.output
    plist = plistlib.loads((tmp_path / "agents" / "app.shoebox.backup.plist").read_bytes())
    assert plist["ProgramArguments"][-2:] == ["db", "backup"]
    assert plist["StartCalendarInterval"] == {"Hour": 4, "Minute": 15}
    assert plist["EnvironmentVariables"]["SHOEBOX_BUCKET"] == "b"
    lint = subprocess.run(["plutil", "-lint", str(tmp_path / "agents" / "app.shoebox.backup.plist")],
                          capture_output=True, text=True)
    assert lint.returncode == 0, lint.stdout
    res = CliRunner().invoke(cli.main, ["db", "schedule", "uninstall"])
    assert "removed" in res.output and list((tmp_path / "agents").glob("*.plist")) == []


def test_status_line_ages(shoebox_home):
    from shoebox.backup import _remember_local

    assert "never backed up" in cli._backup_age_line()
    now = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)
    _remember_local((now - timedelta(hours=3)).isoformat(), "backups/D/x/", "b")
    assert cli._backup_age_line(now).startswith("last backup 3 h ago")
    _remember_local((now - timedelta(days=3)).isoformat(), "backups/D/x/", "b")
    assert "older than 48 h" in cli._backup_age_line(now)
