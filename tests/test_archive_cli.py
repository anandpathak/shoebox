"""End to end: `shoebox archive` with fake Photos items, real sips compression and a mocked S3 bucket."""
import json
import shutil
import sqlite3
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from conftest import make_jpeg
from shoebox import cli, paths

pytestmark = pytest.mark.skipif(shutil.which("sips") is None, reason="needs macOS sips")


def fake_photo(path, uuid, day):
    apple = {"title": None, "description": None, "favorite": False, "hidden": False, "ismovie": False,
             "keywords": [], "labels": ["Beach"], "albums": ["Test"], "persons": [], "latitude": None,
             "longitude": None, "exif_info": {"camera_make": "Apple", "camera_model": "iPhone 15"},
             "place": {"name": "Gokarna", "address": {"city": "Gokarna", "country": "India"}}}
    return SimpleNamespace(
        uuid=uuid, path=str(path), date=datetime(2025, 8, day, 10, tzinfo=timezone.utc), original_filename=path.name,
        width=4032, height=3024, uti_original="public.jpeg", ismovie=False, has_raw=False, path_raw=None,
        live_photo=False, path_live_photo=None, intrash=False, hidden=False, json=lambda: json.dumps(apple))


@pytest.fixture
def photos(tmp_path, monkeypatch):
    items = [fake_photo(make_jpeg(tmp_path / f"src/IMG_{i}.jpg", (4032, 3024), (40 * i, 90, 160)), f"UUID-{i}", i)
             for i in range(1, 4)]
    from shoebox import archiver
    monkeypatch.setattr(archiver, "select_photos", lambda *a, **k: items)
    monkeypatch.setattr(archiver, "add_to_album", lambda *a, **k: None)
    return items


def test_local_only_builds_catalog_next_to_output(tmp_path, photos):
    out = tmp_path / "out"
    res = CliRunner().invoke(cli.main, ["archive", "--all", "--local-only", "--out", str(out)])
    assert res.exit_code == 0, res.output
    conn = sqlite3.connect(out / "catalog.sqlite")
    assert conn.execute("SELECT COUNT(*) FROM media WHERE thumb_key IS NOT NULL").fetchone()[0] == 3
    assert len(list((out / "thumb").rglob("*.jpg"))) == 3
    assert not paths.catalog_db().exists()  # the real catalog stays untouched by test runs


def test_archive_uploads_catalogs_backs_up_and_is_idempotent(tmp_path, photos, bucket, monkeypatch):
    monkeypatch.setattr(cli, "_bucket", lambda name, profile: bucket)
    out = tmp_path / "lib"
    res = CliRunner().invoke(cli.main, ["archive", "--all", "--bucket", "test-archive", "--out", str(out)])
    assert res.exit_code == 0, res.output
    keys = bucket.list_keys("")
    for prefix in ("raw/2025/2025-08/", "compressed/2025/2025-08/", "thumb/2025/2025-08/", "meta/2025/2025-08/"):
        assert sum(k.startswith(prefix) for k in keys) == 3, prefix
    assert any(k.endswith("/catalog.sqlite.gz") for k in keys if k.startswith("backups/"))
    raw = next(k for k in keys if k.startswith("raw/"))
    assert bucket.client.head_object(Bucket="test-archive", Key=raw)["StorageClass"] == "DEEP_ARCHIVE"

    conn = sqlite3.connect(paths.catalog_db())
    assert conn.execute("SELECT COUNT(*) FROM media").fetchone()[0] == 3
    assert conn.execute("SELECT COUNT(*) FROM media_fts WHERE media_fts MATCH 'gokarna'").fetchone()[0] == 3

    runs = list((paths.home() / "runs").glob("*.jsonl"))
    events = [json.loads(x)["event"] for x in runs[0].read_text().splitlines()]
    assert events[:2] == ["accepted", "start"] and "end" in events and events.count("item") == 3
    assert all(e == "message" for e in events[events.index("end") + 1:])

    again = CliRunner().invoke(cli.main, ["archive", "--all", "--bucket", "test-archive", "--out", str(out),
                                          "--no-backup"])
    assert "3 already archived · 0 to process" in again.output


def test_archive_refuses_while_another_run_holds_the_lock(tmp_path, photos):
    from shoebox.runlock import IngestLock

    with IngestLock():
        res = CliRunner().invoke(cli.main, ["archive", "--all", "--local-only", "--out", str(tmp_path / "o")])
    assert res.exit_code != 0 and "already in progress" in res.output


def test_catalog_rebuilds_from_sidecars_after_loss(tmp_path, photos, bucket, monkeypatch):
    monkeypatch.setattr(cli, "_bucket", lambda name, profile: bucket)
    out = tmp_path / "lib"
    CliRunner().invoke(cli.main, ["archive", "--all", "--bucket", "test-archive", "--out", str(out), "--no-backup"])
    before = sqlite3.connect(paths.catalog_db()).execute("SELECT COUNT(*) FROM media").fetchone()[0]
    for suffix in ("", "-wal", "-shm"):
        (paths.home() / f"catalog.sqlite{suffix}").unlink(missing_ok=True)

    local = CliRunner().invoke(cli.main, ["catalog", "backfill", "--from-dir", str(out)])
    assert local.exit_code == 0, local.output
    assert sqlite3.connect(paths.catalog_db()).execute("SELECT COUNT(*) FROM media").fetchone()[0] == before == 3

    for suffix in ("", "-wal", "-shm"):
        (paths.home() / f"catalog.sqlite{suffix}").unlink(missing_ok=True)
    remote = CliRunner().invoke(cli.main, ["catalog", "backfill", "--from-s3", "--bucket", "test-archive"])
    assert remote.exit_code == 0, remote.output
    assert sqlite3.connect(paths.catalog_db()).execute("SELECT COUNT(*) FROM media").fetchone()[0] == 3


def test_recover_on_a_fresh_mac_knows_everything_is_archived(tmp_path, photos, bucket, monkeypatch, shoebox_home):
    import shutil as sh

    monkeypatch.setattr(cli, "_bucket", lambda name, profile: bucket)
    out = tmp_path / "lib"
    res = CliRunner().invoke(cli.main, ["archive", "--all", "--bucket", "test-archive", "--out", str(out)])
    assert res.exit_code == 0, res.output
    old_id = json.loads((shoebox_home / "device.json").read_text())["device_id"]

    sh.rmtree(shoebox_home)                     # the Mac is wiped
    res = CliRunner().invoke(cli.main, ["recover", "--bucket", "test-archive"], input="1\n")
    assert res.exit_code == 0, res.output
    assert json.loads((shoebox_home / "device.json").read_text())["device_id"] == old_id
    dry = CliRunner().invoke(cli.main, ["archive", "--all", "--dry-run", "--out", str(out)])
    assert "3 already archived · 0 to process" in dry.output


def test_recover_without_backup_rebuilds_state_from_s3(tmp_path, photos, bucket, monkeypatch, shoebox_home):
    import shutil as sh

    monkeypatch.setattr(cli, "_bucket", lambda name, profile: bucket)
    out = tmp_path / "lib"
    CliRunner().invoke(cli.main, ["archive", "--all", "--bucket", "test-archive", "--out", str(out), "--no-backup"])
    sh.rmtree(shoebox_home)
    res = CliRunner().invoke(cli.main, ["recover", "--bucket", "test-archive", "--yes"], input="1\n")
    assert res.exit_code == 0, res.output
    assert "No database backup" in res.output and "Marked 3 Apple Photos items" in res.output
    dry = CliRunner().invoke(cli.main, ["archive", "--all", "--dry-run", "--out", str(out)])
    assert "3 already archived · 0 to process" in dry.output


def test_rebuild_catalog_and_state_from_s3_only(tmp_path, photos, bucket, monkeypatch, shoebox_home):
    monkeypatch.setattr(cli, "_bucket", lambda name, profile: bucket)
    out = tmp_path / "lib"
    CliRunner().invoke(cli.main, ["archive", "--all", "--bucket", "test-archive", "--out", str(out), "--no-backup"])
    for name in ("catalog.sqlite", "state.sqlite"):
        (shoebox_home / name).unlink()
    r1 = CliRunner().invoke(cli.main, ["catalog", "rebuild", "--bucket", "test-archive", "--yes"])
    r2 = CliRunner().invoke(cli.main, ["state", "rebuild", "--bucket", "test-archive"])
    assert r1.exit_code == 0 and "3 photos" in r1.output, r1.output
    assert "Marked 3" in r2.output
    r3 = CliRunner().invoke(cli.main, ["catalog", "rebuild", "--bucket", "test-archive", "--from-meta", "--yes"])
    assert "Rebuilt 3 records" in r3.output and "3 photos" in r3.output


def test_search_command(tmp_path, photos, bucket, monkeypatch):
    monkeypatch.setattr(cli, "_bucket", lambda name, profile: bucket)
    out = tmp_path / "lib"
    CliRunner().invoke(cli.main, ["archive", "--all", "--bucket", "test-archive", "--out", str(out), "--no-backup"])
    res = CliRunner().invoke(cli.main, ["search", "beach", "--from", "2025-08-01", "--type", "photo", "--out", str(out)])
    assert res.exit_code == 0, res.output
    lines = [x for x in res.output.splitlines() if x.startswith("2025-08")]
    assert len(lines) == 3 and all(str(out) in x for x in lines)
    data = json.loads(CliRunner().invoke(cli.main, ["search", "gokarna", "--json", "--limit", "2"]).output)
    assert len(data) == 2 and data[0]["taken_at"] >= data[1]["taken_at"]

    exported = tmp_path / "copy.sqlite"
    CliRunner().invoke(cli.main, ["catalog", "export", "-o", str(exported)])
    multi = CliRunner().invoke(cli.main, ["search", "beach", "--catalog", str(exported), "--catalog",
                                          str(paths.catalog_db()), "--json"])
    assert len(json.loads(multi.output)) == 3   # same photos in both files: listed once


def test_get_local_then_from_s3_and_presign(tmp_path, photos, bucket, monkeypatch):
    from urllib.parse import parse_qs, urlparse

    monkeypatch.setattr(cli, "_bucket", lambda name, profile: bucket)
    out = tmp_path / "lib"
    CliRunner().invoke(cli.main, ["archive", "--all", "--bucket", "test-archive", "--out", str(out), "--no-backup"])
    pid = json.loads(CliRunner().invoke(cli.main, ["search", "--json", "--limit", "1"]).output)[0]["photo_id"]

    local = CliRunner().invoke(cli.main, ["get", pid, "-o", str(tmp_path / "a"), "--out", str(out)])
    assert local.exit_code == 0 and (tmp_path / "a" / f"{pid}.jpg").exists()
    # a second device: no local library, fetches from S3
    remote = CliRunner().invoke(cli.main, ["get", pid, "--kind", "thumb", "-o", str(tmp_path / "b"),
                                           "--out", str(tmp_path / "none"), "--bucket", "test-archive"])
    assert remote.exit_code == 0, remote.output and (tmp_path / "b" / f"{pid}.jpg").exists()
    url = CliRunner().invoke(cli.main, ["get", pid, "--url", "--bucket", "test-archive"]).output.strip()
    q = parse_qs(urlparse(url).query)
    assert q["X-Amz-Expires"] == ["900"] and q["X-Amz-Algorithm"] == ["AWS4-HMAC-SHA256"]
    assert 'filename="' in q["response-content-disposition"][0]
